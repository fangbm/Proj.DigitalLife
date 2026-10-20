"""Offline semantic safety regressions; no SDK, device, network or credentials required."""
import asyncio
import copy
import hashlib
import json
import tempfile
import unittest
from dataclasses import replace, asdict
from pathlib import Path
from types import SimpleNamespace

from phonelife.semantic import BoundedSemanticProvider, normalize
from phonelife.capabilities import CapabilityRegistry, GroundingError
from phonelife.devices import MockPhone, DeviceTransportError, mock_png
from phonelife.models import ScreenObservation, ModelResponse, utc_now
from phonelife.storage import Store
from phonelife.semantic_storage import enable_semantics
from phonelife.loop import PhoneLife
from phonelife.llm import MockPlanner
from phonelife.policy import ActionPolicy, PolicyDenied
from phonelife.life_models import LifeContext, LifeDecision
from phonelife.brain import APILifeBrain
from phonelife.verification_registry import GoalVerifierRegistry
from phonelife.goal_contracts import consistent
from test_phase11 import SequenceGui, ACHIEVED


def node(path, bounds, text='', **changes):
    value=dict(path=path,bounds=bounds,text=text,content_description='',class_name='android.widget.LinearLayout',
        resource_id='android:id/title',package='com.android.settings',clickable=False,scrollable=False,
        enabled=True,checkable=False,editable=False,visible=True,password=False,long_clickable=False,children=[])
    value.update(changes); return value


def tree():
    row=node('w1/0',[0,300,720,500],clickable=True,children=[node('w1/0/0',[20,320,300,380],'Battery')])
    return dict(width=720,height=1600,rotation=0,windows=[dict(id=1,type=1,active=True,bounds=[0,0,720,1600],
        root=node('w1',[0,0,720,1600],class_name='androidx.recyclerview.widget.RecyclerView',scrollable=True,
            resource_id='com.android.settings:id/recycler_view',children=[row,
                node('w1/1',[20,150,500,210],'Battery',resource_id='com.android.settings:id/collapsing_toolbar')]))])


class Phone(MockPhone):
    async def foreground(self): return 'com.android.settings/.Settings'


class Backend:
    name, timeout='offline_trusted_tree',1
    def __init__(self, values=None, failure=None): self.values=values or [tree()];self.calls=0;self.failure=failure
    async def read(self,phone,obs):
        self.calls+=1
        if self.failure: raise self.failure
        return copy.deepcopy(self.values[min(self.calls-1,len(self.values)-1)])


class SemanticTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.phone=Phone();self.obs=await self.phone.observe();self.reg=CapabilityRegistry()

    async def collect(self,raw=None):
        return await BoundedSemanticProvider([Backend([raw or tree()])]).collect(self.phone,self.obs)

    async def test_idle_error_switches_once_to_alternative_without_input(self):
        bad=Backend(failure=RuntimeError('ERROR: could not get idle state.'));bad.name='uiautomator'
        good=Backend();s=await BoundedSemanticProvider([bad,good]).collect(self.phone,self.obs)
        self.assertEqual(s.evidence_quality,'trusted');self.assertEqual(bad.calls,1);self.assertEqual(good.calls,2)
        self.assertIn('idle state',s.attempts[0]['reason']);self.assertEqual(self.phone.actions,[])

    async def test_advertised_schema_includes_strict_semantic_intents(self):
        from phonelife.capabilities import semantic_action_schema
        from phonelife.models import action_schema
        schema=semantic_action_schema();self.assertEqual(len(schema['oneOf']),len(action_schema()['oneOf'])+4)
        self.assertTrue(all(v['additionalProperties'] is False for v in schema['oneOf']))
        scroll=next(v for v in schema['oneOf'] if v['properties']['type']['const']=='scroll_region')
        self.assertEqual(scroll['properties']['direction']['enum'],['toward_end','toward_start'])

    async def test_each_backend_has_bounded_timeout_and_no_inputs(self):
        class Hung(Backend):
            timeout=.01
            async def read(self,*args): await asyncio.sleep(10)
        s=await BoundedSemanticProvider([Hung()]).collect(self.phone,self.obs)
        self.assertEqual(s.evidence_quality,'semantic_unavailable');self.assertEqual(s.attempts[0]['reason'],'BACKEND_TIMEOUT')
        self.assertEqual(self.phone.actions,[])

    async def test_missing_attributes_stay_unknown_and_cannot_ground(self):
        raw=tree();del raw['windows'][0]['root']['children'][0]['editable']
        parsed=normalize(raw,self.obs,await self.phone.foreground(),'fixture')
        self.assertIsNone(parsed.controls[1].editable);self.assertEqual(parsed.evidence_quality,'partial')
        s=await self.collect(raw);self.assertEqual(s.evidence_quality,'semantic_unavailable')

    async def test_tree_changes_even_without_pixel_changes_fail_closed(self):
        changed=tree();changed['windows'][0]['root']['children'][0]['bounds'][1]+=10
        s=await BoundedSemanticProvider([Backend([tree(),changed])]).collect(self.phone,self.obs)
        self.assertEqual(s.evidence_quality,'semantic_unavailable');self.assertIn('TREE_OR_ROTATION_CHANGED',str(s.attempts))

    async def test_activity_changes_fail_closed(self):
        calls=0
        async def fg():
            nonlocal calls;calls+=1
            return 'com.android.settings/.Settings' if calls==1 else 'com.android.settings/.Other'
        self.phone.foreground=fg
        s=await self.collect();self.assertIn('FOREGROUND_ACTIVITY_CHANGED',str(s.attempts))

    async def test_rotation_changes_fail_closed(self):
        changed=tree();changed['rotation']=1
        s=await BoundedSemanticProvider([Backend([tree(),changed])]).collect(self.phone,self.obs)
        self.assertIn('ROTATION_CHANGED',str(s.attempts))

    async def test_resized_screen_fails_closed(self):
        async def observe(): return ScreenObservation.from_png(mock_png(1600,720),'mock')
        self.phone.observe=observe
        s=await self.collect();self.assertIn('ROTATED_OR_RESIZED',str(s.attempts))

    async def test_screen_changes_fail_closed(self):
        async def observe(): return ScreenObservation.from_png(mock_png(color=120),'mock')
        self.phone.observe=observe
        s=await self.collect();self.assertIn('SCREEN_TREE_INCONSISTENT',str(s.attempts))

    async def test_foreign_overlay_and_expanded_system_panel_are_not_status_bars(self):
        for package in ('evil.overlay','com.android.systemui'):
            raw=tree();raw['windows'].append(dict(id=2,type=3,active=False,bounds=[0,0,720,1400],
                root=node('w2',[0,0,720,1400],package=package)))
            s=await self.collect(raw);self.assertEqual(s.evidence_quality,'semantic_unavailable')

    async def test_identified_top_system_bar_is_excluded_from_content(self):
        raw=tree();raw['windows'].append(dict(id=2,type=3,active=False,bounds=[0,0,720,132],
            root=node('w2',[0,0,720,132],package='com.android.systemui')))
        self.assertEqual((await self.collect(raw)).evidence_quality,'trusted')

    async def test_connection_window_ids_do_not_mimic_page_changes(self):
        raw=tree();raw['windows'][0]['id']=99
        s=await BoundedSemanticProvider([Backend([tree(),raw])]).collect(self.phone,self.obs)
        self.assertEqual(s.evidence_quality,'trusted')

    async def test_known_navigation_computes_pixels_and_bound_evidence(self):
        s=await self.collect();view=self.reg.view(s);target=next(c for c in view['controls'] if c['intent']=='tap_control')
        action,binding=self.reg.ground(dict(type='tap_control',reason='Open Battery',observation_id=s.observation_id,control_id=target['control_id']),s)
        self.assertEqual((action.x,action.y),(360,400));self.assertEqual(binding['capability'],'navigate_readonly')
        self.assertEqual(binding['bounds'],(0,300,720,500));self.assertIn('evidence_source',binding)

    async def test_forged_and_old_control_references_are_rejected(self):
        s=await self.collect()
        for obs,cid,code in [(s.observation_id,'invented','UNKNOWN_CONTROL'),('old','invented','STALE_REFERENCE')]:
            with self.assertRaises(GroundingError) as e:
                self.reg.ground(dict(type='tap_control',reason='x',observation_id=obs,control_id=cid),s)
            self.assertEqual(e.exception.errors[0]['code'],code)

    async def test_toggle_editable_password_unknown_are_not_navigation(self):
        for attr,value in [('checkable',True),('editable',True),('password',True),('long_clickable',True),('enabled',None)]:
            raw=tree();raw['windows'][0]['root']['children'][0][attr]=value
            s=normalize(raw,self.obs,await self.phone.foreground(),'fixture')
            with self.assertRaises(PolicyDenied):self.reg.safe_target(s,s.controls[1])

    async def test_unknown_and_high_risk_routes_do_not_grant_capabilities(self):
        for text in ('Reset','Grant permissions','Buy','Mystery'):
            raw=tree();raw['windows'][0]['root']['children'][0]['children'][0]['text']=text
            s=normalize(raw,self.obs,await self.phone.foreground(),'fixture')
            with self.assertRaises(PolicyDenied):self.reg.safe_target(s,s.controls[1])

    async def test_safe_scroll_uses_identified_list_not_arbitrary_coordinates(self):
        s=await self.collect();action,binding=self.reg.ground(dict(type='scroll_region',reason='Look lower',
            observation_id=s.observation_id,scrollable_region_id=s.controls[0].control_id,direction='up'),s)
        self.assertEqual(action.type,'swipe');self.assertGreater(action.y,action.y2)
        self.assertEqual(action.x,action.x2);self.assertEqual(binding['capability'],'scroll_readonly')

    async def test_legacy_invalid_pixels_and_duration_cannot_be_laundered_by_grounding(self):
        from phonelife.models import ValidationError
        s=await self.collect()
        with self.assertRaises(ValidationError) as error:self.reg.ground(dict(type='tap',reason='x',x=5000,y=400),s)
        self.assertTrue(error.exception.coordinate_error)
        with self.assertRaises(PolicyDenied):self.reg.ground(dict(type='swipe',reason='x',x=360,y=800,x2=360,y2=400,duration_ms=9000),s)
        with self.assertRaises(PolicyDenied):self.reg.ground(dict(type='swipe',reason='x',x=100,y=800,x2=600,y2=800,duration_ms=400),s)

    async def test_scroll_endpoint_on_toggle_is_rejected(self):
        raw=tree();raw['windows'][0]['root']['children'][0].update(bounds=[0,900,720,1400],checkable=True)
        s=normalize(raw,self.obs,await self.phone.foreground(),'fixture')
        with self.assertRaises(PolicyDenied):self.reg.ground(dict(type='scroll_region',reason='x',observation_id=s.observation_id,
            scrollable_region_id=s.controls[0].control_id,direction='up'),s)

    async def test_target_move_disappear_properties_activity_rotation_block_freshness(self):
        s=await self.collect();action,b=self.reg.ground(dict(type='tap_control',reason='x',observation_id=s.observation_id,control_id=s.controls[1].control_id),s)
        for changes in ({'foreground_activity':'.Other'},{'orientation':1},{'tree_sha256':'changed'},
                        {'content_sha256':'changed'},{'evidence_quality':'partial'},{'controls':()}):
            with self.assertRaises(GroundingError):self.reg.revalidate(b,s,replace(s,**changes))

    async def test_page_verifier_requires_header_and_matching_language_scope(self):
        provider=BoundedSemanticProvider([Backend()]);registry=GoalVerifierRegistry(self.phone,semantic_provider=provider)
        candidate=SimpleNamespace(description='Open the Battery page',verification=dict(kind='page',package='com.android.settings',title='Battery'))
        result=await registry.bind(candidate,True).verify(candidate.description,self.obs,[])
        self.assertEqual(result.verdict,'verified')
        candidate.description='Complete a device security check and read Battery'
        result=await registry.bind(candidate,True).verify(candidate.description,self.obs,[])
        self.assertEqual(result.verdict,'uncertain');self.assertEqual(result.evidence_type,'goal_contract_mismatch')

    async def test_list_row_title_is_not_proof_page_opened(self):
        raw=tree();raw['windows'][0]['root']['children'].pop()
        registry=GoalVerifierRegistry(self.phone,semantic_provider=BoundedSemanticProvider([Backend([raw])]))
        candidate=SimpleNamespace(description='Open the Battery page',verification=dict(kind='page',package='com.android.settings',title='Battery'))
        result=await registry.bind(candidate,True).verify(candidate.description,self.obs,[])
        self.assertEqual(result.verdict,'not_met')

    async def test_reading_android_version_cannot_prove_security_check(self):
        self.assertFalse(consistent('Complete device security check',{'kind':'readonly_fact','facts':['android_version']})[0])
        self.assertFalse(consistent('Read Android version and complete device security check',{'kind':'readonly_fact','facts':['android_version']})[0])
        self.assertTrue(consistent('Read Android version',{'kind':'readonly_fact','facts':['android_version']})[0])
        self.assertFalse(consistent('Read Android version and System information',{'kind':'readonly_fact','facts':['android_version']})[0])

    async def test_changed_target_never_dispatches_before_fresh_reference(self):
        original=await self.collect()
        action,b=self.reg.ground(dict(type='tap_control',reason='x',observation_id=original.observation_id,control_id=original.controls[1].control_id),original)
        raw=tree();raw['windows'][0]['root']['children'][0]['bounds']=[0,600,720,800]
        fresh=normalize(raw,self.obs,await self.phone.foreground(),'offline_trusted_tree')
        with self.assertRaises(GroundingError):self.reg.revalidate(b,original,fresh)
        self.assertEqual(self.phone.actions,[])

    async def test_new_scroll_content_requires_new_visible_labels_not_just_pixels(self):
        from phonelife.models import Action,ActionRecord
        before=await self.collect();raw=tree();raw['windows'][0]['root']['children'][0]['children'][0]['text']='Storage'
        new=replace(normalize(raw,self.obs,await self.phone.foreground(),'offline_trusted_tree'),content_sha256='changed')
        class Provider:
            async def collect(inner,*args): return new
        registry=GoalVerifierRegistry(self.phone,semantic_provider=Provider());registry.semantic_baseline=before
        history=[ActionRecord('id',1,Action('swipe'),self.obs.sha256,'executed')]
        result=await registry._new_scroll_content({'kind':'new_scroll_content'},self.obs,history)
        self.assertEqual(result.verdict,'exploration_observed')
        new=replace(before,content_sha256='changed')
        self.assertEqual((await registry._new_scroll_content({'kind':'new_scroll_content'},self.obs,history)).verdict,'uncertain')

    async def test_semantic_progress_uses_visible_content_and_excludes_system_clock(self):
        from phonelife.semantic import semantic_content_progress
        s=await self.collect();self.assertFalse(semantic_content_progress(s,s)['changed'])
        raw=tree();raw['windows'][0]['root']['children'][0]['children'][0]['text']='Storage'
        changed=normalize(raw,self.obs,await self.phone.foreground(),'offline_trusted_tree')
        self.assertTrue(semantic_content_progress(s,changed)['changed'])
        raw=tree();raw['windows'][0]['root']['children'].append(node('w1/clock',[20,5,300,40],'12:31'))
        clock=normalize(raw,self.obs,await self.phone.foreground(),'offline_trusted_tree')
        self.assertFalse(semantic_content_progress(s,clock)['changed'])


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(Path(self.temp.name)/'state.db')
    async def asyncTearDown(self): self.store.close();self.temp.cleanup()
    def life(self,gui,phone=None,provider=None):
        return PhoneLife(self.store,'mock',phone or Phone(),MockPlanner(),gui,ActionPolicy('mock',frozenset({'com.android.settings'})),
            step_limit=4,action_delay=0,semantic_provider=provider or BoundedSemanticProvider([Backend()]))

    async def test_reference_repairs_are_bounded_and_invalid_candidates_never_dispatch(self):
        bad={'type':'tap_control','reason':'x','observation_id':'old','control_id':'forged'}
        gui=SequenceGui([json.dumps(bad)]*3);life=self.life(gui)
        result=await life.execute_goal('Open the Battery page')
        self.assertEqual(result.reason_code,'REFERENCE_REPAIR_EXHAUSTED');self.assertEqual(life.phone.actions,[])
        attempts=self.store.task(result.task_run_id)['attempts'];self.assertEqual(len(attempts),2)
        self.assertTrue(all(a['payload']['execution_status']=='not_executed' for a in attempts))

    async def test_non_string_action_kind_uses_original_bounded_format_repair(self):
        bad=json.dumps({'type':[],'reason':'invalid kind'})
        gui=SequenceGui([bad]*4);life=self.life(gui)
        result=await life.execute_goal('Open the Battery page')
        self.assertEqual(result.reason_code,'MODEL_OUTPUT_INVALID');self.assertEqual(len(gui.calls),3)
        self.assertEqual(life.phone.actions,[])

    async def test_reference_failure_feedback_reobserves_and_can_repair(self):
        class Gui:
            calls=0
            async def propose(inner,goal,obs,history,feedback):
                inner.calls+=1
                if inner.calls==1: data=dict(type='navigate_back',reason='x',observation_id='stale')
                elif inner.calls==2:
                    self.assertEqual(feedback['field_errors'][0]['code'],'STALE_REFERENCE')
                    data=dict(type='navigate_back',reason='x',observation_id=obs.observation_id)
                else: data=ACHIEVED
                return ModelResponse(data if isinstance(data,str) else json.dumps(data))
        life=self.life(Gui());result=await life.execute_goal('Open the Battery page')
        self.assertEqual(len(life.phone.actions),1);self.assertEqual(life.phone.actions[0].type,'back')
        self.assertEqual(result.status,'succeeded')

    async def test_transport_unknown_never_replays_semantic_action(self):
        class Unknown(Phone):
            async def act(inner,action): inner.actions.append(action);raise DeviceTransportError('unknown')
        class Gui:
            async def propose(inner,goal,obs,history,feedback):
                return ModelResponse(json.dumps(dict(type='navigate_back',reason='x',observation_id=obs.observation_id)))
        life=self.life(Gui(),Unknown());result=await life.execute_goal('Open the Battery page')
        self.assertEqual(result.reason_code,'ADB_OUTCOME_UNKNOWN');self.assertEqual(len(life.phone.actions),1)
        self.assertEqual(self.store.conn.execute('SELECT COUNT(*) FROM lease').fetchone()[0],0)

    async def test_unavailable_semantics_refuses_coordinate_action(self):
        life=self.life(SequenceGui([json.dumps(dict(type='tap',reason='x',x=360,y=400))]),provider=BoundedSemanticProvider([Backend(failure=RuntimeError('idle'))]))
        result=await life.execute_goal('Open the Battery page')
        self.assertEqual(result.status,'blocked');self.assertEqual(life.phone.actions,[])

    async def test_schema_migration_reopen_preserves_original_rows(self):
        self.store.remember('mock','old','unaltered')
        original=[tuple(r) for r in self.store.conn.execute('SELECT * FROM memories')]
        enable_semantics(self.store);enable_semantics(self.store)
        self.assertEqual(original,[tuple(r) for r in self.store.conn.execute('SELECT * FROM memories')])
        self.assertEqual(self.store.conn.execute('PRAGMA user_version').fetchone()[0],12)
        self.store.close();self.store=Store(Path(self.temp.name)/'state.db');enable_semantics(self.store)
        self.assertEqual(original,[tuple(r) for r in self.store.conn.execute('SELECT * FROM memories')])

    async def test_registry_usage_bound_is_enforced_before_input(self):
        life=self.life(SequenceGui([]));life.capability_usage['navigate_readonly']=100
        with self.assertRaises(PolicyDenied):life._use_capability('navigate_readonly')
        self.assertEqual(life.phone.actions,[])

    async def test_semantic_audit_remains_leased_and_releases_after_exception(self):
        life=self.life(SequenceGui([ACHIEVED]));second=Store(Path(self.temp.name)/'state.db')
        async def audit(result,obs):
            self.assertFalse(second.acquire('mock','competitor',1))
            row=self.store.conn.execute('SELECT screenshot_sha256 FROM semantic_observations WHERE observation_id=?',(obs.observation_id,)).fetchone()
            self.assertEqual(row[0],obs.sha256)
            raise RuntimeError('audit fault')
        try:
            result=await life.execute_goal('Open the Battery page',post_audit=audit)
            self.assertEqual(result.reason_code,'AUDIT_ERROR');self.assertTrue(second.acquire('mock','competitor',1))
        finally:second.release('mock','competitor');second.close()

    async def test_recent_benchmark_pages_do_not_enter_autonomous_context(self):
        from phonelife.autonomy import AutonomousLife
        from phonelife.brain import MockLifeBrain,MockLifeGui
        from phonelife.budgets import LifeLimits
        life=self.life(SequenceGui([ACHIEVED]));await life.execute_goal('Open the Battery page',run_kind='benchmark')
        class Brain(MockLifeBrain):
            async def decide_life(inner,context,observation):
                self.assertFalse(any(p.get('run_kind')=='benchmark' for p in context.semantic_environment['recent_pages']))
                self.assertFalse(context.recent_episodes)
                return LifeDecision('rest',[],None,'No new opportunity selected',30)
        controller=AutonomousLife(self.store,'mock',Brain(),LifeLimits())
        autonomous=PhoneLife(self.store,'mock',Phone(),MockPlanner(),MockLifeGui(),ActionPolicy('mock',frozenset({'com.android.settings'})),
            autonomy=controller,semantic_provider=BoundedSemanticProvider([Backend()]),action_delay=0)
        self.assertEqual((await autonomous.run_once(force=True))['status'],'rested')

    async def test_public_export_excludes_raw_identifiers_and_is_database_readonly(self):
        from phonelife.public_evidence import export_public,public_task
        life=self.life(SequenceGui([ACHIEVED]));result=await life.execute_goal('Open the Battery page')
        task=self.store.task(result.task_run_id)
        marker='PRIVATE_PHONE_123456789012345'
        task['goal']=marker;task['result']['summary']=marker;task['attempts'][0]['payload']['candidate_action']['reason']=marker
        public=json.dumps(public_task(task));self.assertNotIn(marker,public);self.assertNotIn('PRIVATE',public)
        path=Path(self.temp.name)/'state.db';self.store.conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        before=hashlib.sha256(path.read_bytes()).hexdigest()
        reader=Store.open_readonly(path)
        try:export_public(reader,Path(self.temp.name)/'public')
        finally:reader.close()
        self.assertEqual(before,hashlib.sha256(path.read_bytes()).hexdigest())

    async def test_continuation_budget_is_cumulative_and_proposals_do_not_spend_other_goals_actions(self):
        from phonelife.autonomy import AutonomousLife
        from phonelife.brain import MockLifeBrain,MockLifeGui
        from phonelife.budgets import LifeLimits
        class Brain(MockLifeBrain):
            async def decide_life(inner,context,observation):
                goals={g['goal_id']:g for g in context.unfinished_goals}
                self.assertEqual(goals['root']['remaining_action_budget'],3)
                self.assertEqual(goals['child']['remaining_action_budget'],2)
                self.assertEqual(goals['proposal']['remaining_action_budget'],5)
                self.assertTrue(goals['child']['can_continue']) # Public facts need no GUI hierarchy.
                return LifeDecision('rest',[],None,'Review persisted allowance',30)
        controller=AutonomousLife(self.store,'mock',Brain(),LifeLimits(max_actions=5))
        for source,count in [('source-1',2),('source-2',1)]:
            self.store.start_task(source,'mock','Historical scoped goal',{},run_kind='autonomous')
            for step in range(count):self.store.attempt(source,step+1,1,{'execution_status':'executed'})
            self.store.finish_task(source,{'status':'uncertain','summary':'Historical task'},0,'Historical')
        for goalid,source,parent,status in [('root','source-1',None,'partial'),('child','source-2','root','partial'),('proposal','source-2',None,'proposed')]:
            candidate=dict(goal_id=goalid,description='Read Android version',motivation='Review',expected_value=.5,novelty=.5,
                estimated_cost=1,risk_class='low',continuation_of=parent,created_at=utc_now(),verification={'kind':'readonly_fact','facts':['android_version']})
            self.store.conn.execute('INSERT INTO life_goals VALUES(?,?,?,?,?,?,?)',(goalid,'mock','autonomous',json.dumps(candidate),status,source,utc_now()))
        life=PhoneLife(self.store,'mock',Phone(),MockPlanner(),MockLifeGui(),ActionPolicy('mock',frozenset({'com.android.settings'})),
            autonomy=controller,semantic_provider=BoundedSemanticProvider([Backend(failure=RuntimeError('idle'))]),action_delay=0)
        self.assertEqual((await life.run_once(force=True))['status'],'rested')


class ContinuationTests(unittest.IsolatedAsyncioTestCase):
    def context(self):
        return LifeContext(utc_now(),0,{}, {},[],[],[],[],[dict(goal_id='persisted',can_continue=True,
            remaining_action_budget=3,verification={'kind':'observe'})],{},{},{})
    def choice(self,reference):
        return dict(kind='continue',candidates=[dict(goal_id='new',description='Observe page',motivation='Resume evidence',
            expected_value=.5,novelty=.5,estimated_cost=1,risk_class='low',continuation_of=reference,
            created_at=utc_now(),verification={'kind':'observe'})],selected_goal_id='new',reason='Resume',wake_after_seconds=30)
    async def test_missing_reference_gets_structured_repair_not_conversion(self):
        outer=self
        class API:
            calls=0
            async def generate(inner,model,system,user,*args,**kwargs):
                data=json.loads(user);inner.calls+=1
                if inner.calls==2:
                    outer.assertIn('MISSING_CONTINUATION_REFERENCE',data['structured_feedback']['detail'])
                    outer.assertEqual(data['structured_feedback']['eligible_goal_ids'],['persisted'])
                return ModelResponse(json.dumps(outer.choice(None if inner.calls==1 else 'persisted')))
        api=API();brain=APILifeBrain(api,'offline');decision=await brain.decide_life(self.context(),ScreenObservation.from_png(mock_png(),'mock'))
        self.assertEqual(decision.kind,'continue');self.assertEqual(decision.selected.continuation_of,'persisted')
        self.assertEqual(api.calls,2);self.assertEqual(brain.last_audit[0]['validation_status'],'rejected')
    async def test_forged_reference_exhausts_two_attempts(self):
        outer=self
        class API:
            calls=0
            async def generate(inner,*args,**kwargs): inner.calls+=1;return ModelResponse(json.dumps(outer.choice('forged')))
        api=API();brain=APILifeBrain(api,'offline')
        with self.assertRaises(ValueError):await brain.decide_life(self.context(),ScreenObservation.from_png(mock_png(),'mock'))
        self.assertEqual(api.calls,2);self.assertTrue(all(a['validation_status']=='rejected' for a in brain.last_audit))
