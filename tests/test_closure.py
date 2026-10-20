"""Offline closure fixtures: actual API35 tree shape, synthetic pixels, no network/device."""
import asyncio
import copy
import hashlib
import io
import json
import os
import tempfile
import time
import unittest
import uuid
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch
from PIL import Image, ImageDraw
from phonelife.audit import scrub, typed_value, AuditKind, redact
from phonelife.semantic import normalize, BoundedSemanticProvider, semantic_content_progress
from phonelife.semantic_stability import dynamic_regions, trusted, compare, stable_pixels, is_about_page
from phonelife.capabilities import CapabilityRegistry, GroundingError
from phonelife.models import ScreenObservation, ModelResponse, utc_now
from phonelife.brain import APILifeBrain, validate_references
from phonelife.decision_errors import LifeDecisionRejected, DecisionContractError
from phonelife.life_models import LifeDecision
from phonelife.storage import Store
from phonelife.devices import DeviceTransportError
from phonelife.public_evidence import public_task
from test_semantics import Backend, Phone, tree
import test_semantics as semantic_fixtures
from test_autonomy import Fixture
from test_phase11 import SequenceGui, ACHIEVED
from phonelife.loop import PhoneLife
from phonelife.llm import MockPlanner
from phonelife.policy import ActionPolicy
from phonelife.verification_registry import GoalVerifierRegistry

FIXTURE=Path(__file__).parent/'fixtures/uptime-api35'
FG=json.loads((FIXTURE/'PROVENANCE.json').read_text(encoding='utf8'))['actual_foreground_package']+'/'+json.loads((FIXTURE/'PROVENANCE.json').read_text(encoding='utf8'))['actual_foreground_activity']


def fixture(index=0):
    return json.loads((FIXTURE/f'tree-{index}.json').read_text(encoding='utf8'))


def flatten(raw):
    def visit(n): return [n]+[c for child in n.get('children',[]) for c in visit(child)]
    return [c for w in raw['windows'] for c in visit(w['root'])]


def display(raw):
    return next(n for n in flatten(raw) if n.get('resource_id')=='android:id/summary' and n.get('text','').count(':')==2)


def pixels(index=0,outside=False):
    im=Image.new('RGB',(720,1600),'white');draw=ImageDraw.Draw(im)
    a,b,c,d=display(fixture())['bounds'];draw.rectangle((a,b,c-1,d-1),fill=('black' if index==0 else 'red'))
    if outside: draw.rectangle((300,400,310,410),fill='black')
    stream=io.BytesIO();im.save(stream,format='PNG');return ScreenObservation.from_png(stream.getvalue(),'offline')


class DeepPhone(Phone):
    async def foreground(self):return FG
    async def observe(self):return pixels(1)


class DynamicTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.before=pixels();self.after=pixels(1);self.phone=DeepPhone();self.reg=CapabilityRegistry()
    def semantic(self,raw=None,obs=None):return normalize(raw or fixture(),obs or self.before,FG,'offline_trusted_tree')
    async def collect(self,values=None,phone=None):
        return await BoundedSemanticProvider([Backend(values or [fixture(),fixture(1)])]).collect(phone or self.phone,self.before)

    async def test_real_shape_reproduces_old_tree_and_body_failure(self):
        from phonelife.progress import ActionProgressDetector
        a,b=self.semantic(),self.semantic(fixture(1),self.after)
        self.assertNotEqual(a.tree_sha256,b.tree_sha256)
        self.assertNotEqual(ActionProgressDetector()._body(self.before)[0],ActionProgressDetector()._body(self.after)[0])
        self.assertEqual(compare(a,b)[0]['change_type'],'noninteractive_text')

    async def test_real_activity_and_known_entrypoints_require_about_header(self):
        self.assertEqual(FG,'com.android.settings/.SubSettings')
        s=self.semantic();self.assertTrue(is_about_page(s));self.assertTrue(dynamic_regions(s))
        self.assertTrue(dynamic_regions(replace(s,foreground_activity='.Settings$MyDeviceInfoActivity')))
        for activity in ('.Other','.OtherSubSettings','evil.SubSettings'):
            self.assertFalse(dynamic_regions(replace(s,foreground_activity=activity)))

    async def test_subsettings_without_exact_safe_about_header_is_not_dynamic(self):
        for change in ('wrong_title','hidden','clickable','unknown_attribute'):
            raw=fixture();n=next(n for n in flatten(raw) if n.get('resource_id')=='com.android.settings:id/collapsing_toolbar')
            if change=='wrong_title':n.update(text='Battery',content_description='Battery')
            elif change=='hidden':n['visible']=False
            elif change=='clickable':n['clickable']=True
            else:n['editable']=None
            self.assertFalse(dynamic_regions(self.semantic(raw)))

    async def test_activity_change_between_two_valid_about_entrypoints_still_blocks(self):
        a=self.semantic();b=replace(a,foreground_activity='.Settings$MyDeviceInfoActivity')
        self.assertTrue(dynamic_regions(b))
        with self.assertRaises(ValueError):compare(a,b)

    async def test_paired_dynamic_text_and_pixels_are_trusted_with_audit(self):
        s=await self.collect();self.assertEqual(s.evidence_quality,'trusted_dynamic_noninteractive')
        self.assertFalse(s.stability[0]['involves_interactive_control'])
        self.assertEqual(s.stability[0]['ignored_fields'],['text']);self.assertEqual(len(s.private_frames),1)
        self.assertTrue(s.raw['_collection']['samples'])
        self.assertEqual(s.raw['_collection']['frames'][0]['sha256'],s.private_frames[0].sha256)

    async def test_stable_scroll_control_remains_bound_with_dynamic_text(self):
        s=await self.collect();target=next(c for c in s.controls if c.scrollable is True)
        action,binding=self.reg.ground(dict(type='scroll_region',reason='Read lower page',observation_id=s.observation_id,
            scrollable_region_id=target.control_id,direction='toward_end'),s)
        current=replace(await self.collect([fixture(1),fixture(1)]),observation_id=str(uuid.uuid4()))
        self.assertEqual(self.reg.revalidate(binding,s,current)['pre_dispatch_consistency'],'verified')
        self.assertEqual(action.type,'swipe')

    async def test_uptime_is_not_semantic_progress(self):
        a,b=self.semantic(),self.semantic(fixture(1),self.after)
        self.assertFalse(semantic_content_progress(a,b)['changed'])

    async def test_every_dynamic_safety_attribute_change_blocks(self):
        for key in ('clickable','enabled','checkable','editable','password','visible','scrollable','long_clickable'):
            raw=fixture(1);node=display(raw);node[key]=not node[key]
            with self.subTest(key=key):self.assertFalse(trusted(await self.collect([fixture(),raw])))

    async def test_unknown_dynamic_attribute_is_not_coerced(self):
        raw=fixture(1);display(raw)['editable']=None
        self.assertFalse(trusted(await self.collect([fixture(),raw])))

    async def test_dynamic_bounds_change_blocks(self):
        raw=fixture(1);display(raw)['bounds'][0]+=3
        self.assertFalse(trusted(await self.collect([fixture(),raw])))

    async def test_unrecognized_timer_title_has_no_mask(self):
        raw=fixture();next(n for n in flatten(raw) if n.get('text')=='Uptime')['text']='Unknown timer'
        self.assertFalse(dynamic_regions(self.semantic(raw)))

    async def test_clickable_parent_has_no_mask(self):
        raw=fixture();path=display(raw)['path'].rsplit('/',1)[0]
        next(n for n in flatten(raw) if n['path']==path)['clickable']=True
        self.assertFalse(dynamic_regions(self.semantic(raw)))

    async def test_outside_mask_pixel_change_blocks(self):
        class Changed(DeepPhone):
            async def observe(self):return pixels(1,True)
        s=await self.collect(phone=Changed());self.assertFalse(trusted(s))
        self.assertIn('SCREEN_TREE_INCONSISTENT',str(s.attempts));self.assertTrue(s.raw['_collection']['samples'])

    async def test_noninteractive_unknown_text_change_blocks(self):
        raw=fixture(1);n=next(n for n in flatten(raw) if n.get('text')=='fixture label' and n.get('package')=='com.android.settings');n['text']='different'
        self.assertFalse(trusted(await self.collect([fixture(),raw])))

    async def test_overlay_and_window_bounds_changes_block(self):
        for change in ('overlay','window'):
            raw=fixture(1)
            if change=='window':raw['windows'][0]['bounds'][2]-=1
            else:
                w=copy.deepcopy(raw['windows'][0]);w.update(id=999,type=2,active=False);raw['windows'].append(w)
            self.assertFalse(trusted(await self.collect([fixture(),raw])))

    async def test_activity_switch_blocks_even_dynamic_page(self):
        class Changed(DeepPhone):
            count=0
            async def foreground(self):
                self.count+=1;return FG if self.count==1 else 'com.android.settings/.Other'
        self.assertFalse(trusted(await self.collect(phone=Changed())))

    async def test_target_moves_disappears_or_identity_changes_block(self):
        a=self.semantic();target=next(c for c in a.controls if c.scrollable is True)
        _,binding=self.reg.ground(dict(type='scroll_region',reason='x',observation_id=a.observation_id,
            scrollable_region_id=target.control_id,direction='toward_end'),a)
        for key in ('bounds','resource_id','children'):
            raw=fixture(1);n=next(n for n in flatten(raw) if n.get('scrollable') is True)
            if key=='bounds':n['bounds'][0]+=1
            elif key=='children':n['children']=[]
            else:n['resource_id']='different'
            with self.assertRaises(GroundingError):self.reg.revalidate(binding,a,self.semantic(raw))

    async def test_cooldown_honored_then_recovery_after_expiry(self):
        backend=Backend(failure=TimeoutError());provider=BoundedSemanticProvider([backend])
        await provider.collect(self.phone,self.before);calls=backend.calls
        s=await provider.collect(self.phone,self.before);self.assertEqual(backend.calls,calls)
        self.assertEqual(s.attempts[0]['reason'],'BOUNDED_FAILURE_COOLDOWN')
        backend.failure=None;backend.values=[fixture()];provider.cooldown[backend.name]=time.monotonic()-1
        self.assertTrue(trusted(await provider.collect(self.phone,self.before)))

    async def test_rotation_and_screen_resize_block(self):
        raw=fixture(1);raw['rotation']=1
        self.assertFalse(trusted(await self.collect([fixture(),raw])))


class TypedAuditTests(unittest.TestCase):
    def test_sha_uuid_with_phone_digit_sequences_remain_exact(self):
        sha='a'*12+'13800138000'+'b'*41;identity='13800138-0000-4000-8000-138001380001'
        value=scrub({'response_sha256':sha,'source_task_run_id':identity,'control_id':'a13800138000abcdefabcdefa'})
        self.assertEqual(value['response_sha256'],sha);self.assertEqual(value['source_task_run_id'],identity)
        self.assertEqual(value['control_id'],'a13800138000abcdefabcdefa')
        self.assertEqual(scrub({'eligible_goal_ids':[identity]})['eligible_goal_ids'],[identity])
        self.assertEqual(scrub({'attempt_id':identity,'arbitrary_id':identity})['attempt_id'],identity)
        self.assertEqual(scrub({'arbitrary_id':identity})['arbitrary_id'],identity)
        self.assertEqual(redact('Task '+identity+' stopped'),'Task '+identity+' stopped')
        with patch.dict(os.environ,{'OFFLINE_API_KEY':identity}):
            self.assertNotIn(identity,json.dumps(scrub({'arbitrary_id':identity,'task_run_id':identity})))

    def test_safety_boolean_and_null_types_survive_storage(self):
        for key in ('password','editable','checkable','enabled','clickable','visible','scrollable','long_clickable'):
            for v in (True,False,None):self.assertIs(scrub({key:v})[key],v)
        self.assertEqual(scrub({'password':'secret text'})['password'],'[REDACTED]')

    def test_actual_secrets_and_personal_identifiers_remain_masked(self):
        with patch.dict(os.environ,{'OFFLINE_API_KEY':'offline-secret-only-123'}):
            value=scrub({'free':'offline-secret-only-123 sk-offlineCredential 13800138000 a@example.com',
                         'api_key':'offline-secret-only-123','password_text':'actual password','imei':'123456789012345'})
        encoded=json.dumps(value)
        for secret in ('offline-secret-only-123','sk-offlineCredential','13800138000','a@example.com','123456789012345','actual password'):
            self.assertNotIn(secret,encoded)

    def test_secrets_in_dictionary_keys_are_redacted(self):
        with patch.dict(os.environ,{'OFFLINE_API_KEY':'offline-key-as-dictionary-field'}):
            value=scrub({'offline-key-as-dictionary-field':False,'bindings':{'13800138000':'masked'}})
        self.assertNotIn('offline-key-as-dictionary-field',json.dumps(value))
        self.assertNotIn('13800138000',json.dumps(value))

    def test_free_text_network_identifiers_mask_without_corrupting_timers(self):
        value=scrub({'detail':'Network 192.0.2.1 and 2001:db8::1 MAC 02:00:00:00:00:01; uptime 12:41:48'})['detail']
        for secret in ('192.0.2.1','2001:db8::1','02:00:00:00:00:01'):self.assertNotIn(secret,value)
        self.assertIn('12:41:48',value)

    def test_malformed_identifier_is_never_repaired(self):
        self.assertNotEqual(scrub({'response_sha256':'bad-13800138000'})['response_sha256'],'0'*64)
        self.assertEqual(typed_value('false',AuditKind.BooleanSafetyAttribute),'[INVALID_SAFETY_ATTRIBUTE]')

    def test_public_export_drops_model_text_and_raw_evidence(self):
        task={'task_run_id':str(uuid.uuid4()),'status':'failed','goal':'private-account-cookie','result':{},
              'raw':'private raw tree','attempts':[{'step_index':1,'generation_attempt':1,'payload':{
                  'model_response_excerpt':'private-account-cookie','candidate_action':{'type':'tap','text':'private-account-cookie'},
                  'semantic_binding':{'target':{'text':'private-account-cookie','password':False}}}}]}
        data=json.dumps(public_task(task));self.assertNotIn('private-account-cookie',data);self.assertNotIn('target',data)


class RejectionTests(Fixture):
    def test_repaired_candidate_refusal_and_sent_context_are_auditable(self):
        bad=semantic_fixtures.ContinuationTests().choice(None)
        valid=copy.deepcopy(bad);valid.update(kind='explore')
        valid['candidates'][0].update(description='Read Android version',verification={'kind':'readonly_fact','facts':['android_version']})
        class API:
            provider='offline';calls=0;users=[]
            async def generate(inner,model,system,user,*args,**kwargs):
                inner.calls+=1;inner.users.append(user)
                if system.startswith('Reflect'):
                    return ModelResponse(json.dumps(dict(attempted='Read Android version',subjective_outcome='Use verified result',
                        discoveries=[],topics=[],worth_continuing=False,next_adjustment='Review actual outcome')))
                return ModelResponse(json.dumps(bad if inner.calls==1 else valid))
        api=API();life=self.agent(APILifeBrain(api,'offline'));result=self.cycle(life)
        self.assertEqual(result['status'],'succeeded');errors=result['life_decision_rejections']
        self.assertEqual(len(errors),1);self.assertEqual(errors[0]['reason_code'],'MISSING_CONTINUATION_REFERENCE')
        event=json.loads(self.store.conn.execute("SELECT payload FROM events WHERE event_type='life_model_choice'").fetchone()[0])
        self.assertEqual(event['audit'][0]['request_user_sha256'],hashlib.sha256(api.users[0].encode()).hexdigest())
        self.assertEqual(self.store.conn.execute('SELECT COUNT(*) FROM life_goals').fetchone()[0],1)

    def test_two_failures_persist_and_next_new_brain_receives_specific_error(self):
        choice=semantic_fixtures.ContinuationTests().choice(None)
        class API:
            provider='offline'
            async def generate(self,*args,**kwargs):return ModelResponse(json.dumps(choice))
        life=self.agent(APILifeBrain(API(),'offline'));first=self.cycle(life)
        self.assertEqual(first['reason_code'],'MISSING_CONTINUATION_REFERENCE');self.assertEqual(life.phone.actions,[])
        saved=self.store.task(first['task_run_id'])['result']['life_decision_rejections']
        self.assertEqual(len(saved),2);self.assertEqual(saved[-1]['repairs_used'],1)
        self.assertEqual(saved[-1]['confirmed_actions'],0);self.assertFalse(saved[-1]['executed'])
        original=first['task_run_id'];outer=self
        self.store.close();self.store=Store(self.path) # Restart must retrieve it from SQLite.
        class Next:
            provider='offline';seen=False
            async def generate(inner,model,system,user,*args,**kwargs):
                context=json.loads(user)['life_context'];failure=context['recent_failures'][0]
                error=failure['body']['objective']['decision_rejections'][-1]
                outer.assertEqual(error['reason_code'],'MISSING_CONTINUATION_REFERENCE')
                outer.assertEqual(error['source_task_run_id'],original);inner.seen=True
                return ModelResponse(json.dumps(dict(kind='rest',candidates=[],selected_goal_id=None,
                    reason='Review exact refusal before deciding again',wake_after_seconds=30)))
        api=Next();second=self.cycle(self.agent(APILifeBrain(api,'offline')))
        self.assertTrue(api.seen);self.assertEqual(second['status'],'rested')
        self.assertEqual(self.store.conn.execute('SELECT COUNT(*) FROM life_goals').fetchone()[0],0)

    def test_reference_error_codes_are_distinct_and_no_silent_repair(self):
        fixture_case=semantic_fixtures.ContinuationTests();context=fixture_case.context()
        for reference,code in [(None,'MISSING_CONTINUATION_REFERENCE'),('missing','INVALID_CONTINUATION_REFERENCE')]:
            d=LifeDecision.from_dict(fixture_case.choice(reference))
            with self.assertRaises(DecisionContractError) as e:validate_references(d,context)
            self.assertEqual(e.exception.code,code);self.assertEqual(d.selected.continuation_of,reference)
        for cost,spec,code in [(4,{'kind':'observe'},'CONTINUATION_BUDGET_EXCEEDED'),
                (1,{'kind':'readonly_fact','facts':['model']},'CONTINUATION_CONTRACT_CHANGED')]:
            raw=fixture_case.choice('persisted');raw['candidates'][0].update(estimated_cost=cost,verification=spec)
            with self.assertRaises(DecisionContractError) as e:validate_references(LifeDecision.from_dict(raw),context)
            self.assertEqual(e.exception.code,code)


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(Path(self.temp.name)/'state.db')
    async def asyncTearDown(self):self.store.close();self.temp.cleanup()
    def life(self,unknown=False):
        class Emu(Phone):
            kind='emulator';launches=0
            async def reopen_settings(inner):
                inner.launches+=1
                if unknown:raise DeviceTransportError('unknown')
        phone=Emu()
        class Provider:
            async def collect(inner,device,obs):
                s=normalize(tree(),obs,await device.foreground(),'offline')
                # Same normalized hash/pixels as real provider once the fixed launch succeeds.
                return s if device.launches else replace(s,evidence_quality='semantic_unavailable')
        return PhoneLife(self.store,'emulator',phone,MockPlanner(),SequenceGui([ACHIEVED]),
            ActionPolicy('emulator',frozenset({'com.android.settings'})),semantic_provider=Provider(),action_delay=0)

    async def test_recovery_requires_live_lease(self):
        life=self.life();run=str(uuid.uuid4());self.store.start_task(run,'emulator','test',{})
        obs=await life._observe(run)
        with self.assertRaises(RuntimeError):await life._recover_semantics(run,'unowned',obs)
        self.assertEqual(life.phone.launches,0)

    async def test_recovery_launch_separately_counted_and_old_reference_refused(self):
        life=self.life();run=str(uuid.uuid4());self.store.start_task(run,'emulator','test',{})
        self.store.acquire('emulator',run,60);obs=await life._observe(run);old=life.semantic_observation
        fresh=await life._recover_semantics(run,run,obs)
        self.assertTrue(trusted(life.semantic_observation));self.assertNotEqual(fresh.observation_id,old.observation_id)
        with self.assertRaises(GroundingError):life.capabilities.ground(dict(type='navigate_back',reason='old',observation_id=old.observation_id),life.semantic_observation)
        self.assertEqual(life.phone.launches,1);self.assertEqual(life.phone.actions,[])
        await life._recover_semantics(run,run,fresh);self.assertEqual(life.phone.launches,1)
        event=json.loads(self.store.conn.execute("SELECT payload FROM events WHERE event_type='semantic_recovery_finished'").fetchone()[0])
        self.assertEqual(event['recovery_actions'],1);self.assertFalse(event['business_goal_succeeded'])
        self.store.release('emulator',run)

    async def test_uncertain_launch_never_retries_and_lease_released(self):
        life=self.life(True);result=await life.execute_goal('Open the Battery page')
        self.assertEqual(result.reason_code,'ADB_OUTCOME_UNKNOWN');self.assertEqual(life.phone.launches,1)
        self.assertEqual(life.phone.actions,[]);self.assertEqual(self.store.conn.execute('SELECT COUNT(*) FROM lease').fetchone()[0],0)

    async def test_known_overlay_stops_recovery_without_launch(self):
        life=self.life();run=str(uuid.uuid4());self.store.start_task(run,'emulator','test',{})
        self.store.acquire('emulator',run,60);obs=await life._observe(run)
        original=life.semantic_provider.collect
        async def overlay(device,frame):return replace(await original(device,frame),overlays=({'type':2,'package':'unknown.overlay'},))
        life.semantic_provider.collect=overlay;await life._recover_semantics(run,run,obs)
        self.assertEqual(life.phone.launches,0);self.assertEqual(life.phone.actions,[])
        self.store.release('emulator',run)


class CollectionJournalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(Path(self.temp.name)/'state.db')
        class Emu(Phone):kind='emulator'
        self.phone=Emu();self.backend=Backend();self.provider=BoundedSemanticProvider([self.backend])
        self.life=PhoneLife(self.store,'emulator',self.phone,MockPlanner(),SequenceGui([ACHIEVED]),
            ActionPolicy('emulator',frozenset({'com.android.settings'})),semantic_provider=self.provider,
            action_delay=0,evidence_dir=Path(self.temp.name)/'private')
    async def asyncTearDown(self):self.store.close();self.temp.cleanup()

    async def test_independent_samples_are_immutable_hash_linked_and_context_is_reset(self):
        from phonelife.semantic_storage import COLLECTION_SINK
        result=await self.life.execute_goal('Open the Battery page')
        self.assertEqual(result.status,'succeeded');self.assertIsNone(COLLECTION_SINK.get())
        rows=self.store.conn.execute("SELECT payload FROM events WHERE event_type='additional_semantic_collection'").fetchall()
        self.assertTrue(rows)
        for row in rows:
            sample=json.loads(row[0]);self.assertEqual(sample['task_run_id'],result.task_run_id)
            self.assertEqual(hashlib.sha256(Path(sample['private_path']).read_bytes()).hexdigest(),sample['raw_sha256'])
            self.assertIs(sample['assessment']['controls'][0]['password'],False)
            for frame in sample['private_frames']:
                self.assertEqual(hashlib.sha256(Path(frame['path']).read_bytes()).hexdigest(),frame['sha256'])
        count=len(rows);await self.provider.collect(self.phone,await self.phone.observe())
        self.assertEqual(self.store.conn.execute("SELECT COUNT(*) FROM events WHERE event_type='additional_semantic_collection'").fetchone()[0],count)

    async def test_post_audit_outage_is_recorded_under_lease_then_released(self):
        from types import SimpleNamespace
        from phonelife.semantic_storage import COLLECTION_SINK
        second=Store(Path(self.temp.name)/'state.db')
        async def audit(result,obs):
            self.backend.failure=RuntimeError('OFFLINE_INDEPENDENT_CAPTURE_OUTAGE')
            candidate=SimpleNamespace(description='Open the Battery page',verification={'kind':'page','package':'com.android.settings','title':'Battery'})
            verifier=GoalVerifierRegistry(self.phone,semantic_provider=self.provider).bind(candidate,True)
            value=await verifier.verify(candidate.description,obs,[])
            self.assertFalse(second.acquire('emulator','competitor',1))
            return {'verification':asdict(value)}
        try:
            result=await self.life.execute_goal('Open the Battery page',post_audit=audit)
            self.assertEqual(result.status,'uncertain');self.assertEqual(result.reason_code,'POST_AUDIT_NOT_VERIFIED')
            rows=self.store.conn.execute("SELECT payload FROM events WHERE event_type='additional_semantic_collection' ORDER BY id").fetchall()
            sample=json.loads(rows[-1][0]);self.assertEqual(sample['assessment']['evidence_quality'],'semantic_unavailable')
            self.assertIn('OFFLINE_INDEPENDENT_CAPTURE_OUTAGE',str(sample['assessment']['attempts']))
            self.assertEqual(sample['observation_id'],result.post_audit['observation_id'])
            self.assertTrue(Path(sample['private_path']).is_file());self.assertIsNone(COLLECTION_SINK.get())
            self.assertTrue(second.acquire('emulator','competitor',1));second.release('emulator','competitor')
        finally:second.close()

    async def test_journal_io_failure_never_falls_back_to_another_backend(self):
        from phonelife.semantic_storage import COLLECTION_SINK
        alternate=Backend();alternate.name='alternative'
        provider=BoundedSemanticProvider([self.backend,alternate])
        def failed(_):raise OSError('OFFLINE_JOURNAL_DISK_FAILURE')
        token=COLLECTION_SINK.set(failed)
        try:
            with self.assertRaises(OSError):await provider.collect(self.phone,await self.phone.observe())
            self.assertEqual(self.backend.calls,2);self.assertEqual(alternate.calls,0);self.assertEqual(self.phone.actions,[])
        finally:COLLECTION_SINK.reset(token)

