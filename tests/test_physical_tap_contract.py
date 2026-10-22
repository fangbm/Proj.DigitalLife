"""offline_fixture: synthetic native XML/temporary ledgers, never a real device.

The separately hashed private capture is tested locally, not copied here.
"""
import tempfile
import time
import unittest
import json
import hashlib
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch
import test_physical_core as core
from test_semantics import tree
from phonelife.capabilities import CapabilityRegistry, GroundingError
from phonelife.devices import mock_png
from phonelife.models import Action, ScreenObservation
from phonelife.physical_execution import (ColorOSSemanticProvider, PhysicalPhone,
    PhysicalRegistry, PhysicalContract, PhysicalActionPolicy, page_key)
from phonelife.policy import PolicyDenied
from phonelife.semantic import UIAutomatorBackend, normalize
from phonelife.device_profiles import digest


def native_tree(**row_changes):
    raw=tree()
    def visit(n):
        n['editable']=None
        for child in n['children']:visit(child)
    visit(raw['windows'][0]['root'])
    row=raw['windows'][0]['root']['children'][0]
    row.update(row_changes)
    row['children'][0].update(class_name='android.widget.TextView',text='显示与亮度')
    return raw


class NativeTapContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.f=core.PhysicalFixture(self.temp.name)
        self.stack=self.f.mocks();self.stack.__enter__()
        self.reg=PhysicalRegistry(self.f.contract)
    async def asyncTearDown(self):self.stack.close();self.f.close();self.temp.cleanup()
    def sample(self,raw=None,approve=True):
        s=normalize(raw or native_tree(),self.f.obs,'com.android.settings/.Settings','coloros16_native_xml/1')
        if approve:
            self.f.contract.manifest['apps'][0]['pages'][0]['page_sha256']=page_key(s)
            self.f.grant['manifest_sha256']=digest(self.f.contract.manifest)
        return s,next(c for c in s.controls if c.clickable is True)
    def ground(self,s,c):
        return self.reg.ground({'type':'tap_control','reason':'offline navigation',
            'observation_id':s.observation_id,'control_id':c.control_id},s)
    async def test_tap_unknown_editable_remains_unknown_and_partial(self):
        s,c=self.sample();a,b=self.ground(s,c)
        self.assertEqual(a.type,'tap');self.assertIsNone(c.editable)
        self.assertEqual(s.evidence_quality,'partial');self.assertEqual(b['target']['editable'],None)
        self.reg.revalidate(b,s,replace(s,observation_id='new-frame'))
    async def test_generic_emulator_registry_not_relaxed(self):
        s,c=self.sample()
        with self.assertRaises(PolicyDenied):CapabilityRegistry().safe_target(s,c)
    async def test_text_still_denied_with_unknown_or_true_editable(self):
        for value in (None,True):
            s,c=self.sample(native_tree(editable=value))
            with self.assertRaises(PolicyDenied):self.reg.ground({'type':'text','text':'x','reason':'offline'},s)
            with self.assertRaises(PolicyDenied):PhysicalActionPolicy(self.f.contract).check(Action('text',text='x'),self.f.obs)
    async def test_missing_visibility_not_inferred_from_xml_presence(self):
        raw=native_tree();raw['windows'][0]['root']['children'][0]['visible']=None
        s,c=self.sample(raw)
        with self.assertRaisesRegex(PolicyDenied,'PHYSICAL_TRUSTED_NATIVE_SEMANTICS_REQUIRED'):self.ground(s,c)
    async def test_explicitly_hidden_row_or_ancestor_or_title_denied(self):
        for path in ('row','root','title'):
            raw=native_tree();root=raw['windows'][0]['root'];row=root['children'][0]
            {'root':root,'row':row,'title':row['children'][0]}[path]['visible']=False
            s,c=self.sample(raw)
            with self.assertRaises(PolicyDenied):self.ground(s,c)
    async def test_editable_true_and_custom_widget_denied(self):
        for attrs in ({'editable':True},{'class_name':'oem.CustomEditableRow'}):
            s,c=self.sample(native_tree(**attrs))
            with self.assertRaises(PolicyDenied):self.ground(s,c)
    async def test_swipe_retains_complete_editable_contract(self):
        s,_=self.sample();region=next(c for c in s.controls if c.scrollable)
        with self.assertRaises(PolicyDenied):self.reg.safe_target(s,region,True)
    async def test_clickable_ancestor_not_title_and_unbound_control_denied(self):
        s,c=self.sample();a,b=self.ground(s,c)
        self.assertEqual(b['control_id'],c.control_id);self.assertEqual((a.x,a.y),(360,400))
        with self.assertRaises(PolicyDenied):self.reg.safe_target(s,replace(c,control_id='invented'))
        title=next(n for n in s.controls if n.parent==c.control_id)
        with self.assertRaises(PolicyDenied):self.reg.safe_target(s,title)
    async def test_bounds_outside_screen_and_child_outside_row_denied(self):
        for bounds in ([-1,300,720,500],[0,300,721,500],[0,330,720,500]):
            s,c=self.sample(native_tree(bounds=bounds))
            with self.assertRaises(PolicyDenied):self.ground(s,c)
    async def test_wrong_window_owner_focus_resolution_or_overlay_denied(self):
        s,c=self.sample()
        for other in (replace(s,foreground_package='foreign.app'),replace(s,foreground_activity='.Other'),
                replace(s,screen_width=1080),replace(s,overlays=({'reason':'unowned'},))):
            with self.assertRaises(PolicyDenied):self.ground(other,c)
    async def test_page_hash_and_control_properties_cannot_be_forged(self):
        s,c=self.sample();changed=replace(c,visible=True,bounds=(1,300,720,500))
        altered=replace(s,controls=tuple(changed if x==c else x for x in s.controls))
        with self.assertRaises(PolicyDenied):self.ground(altered,changed)
    async def test_unapproved_controls_and_unapproved_page_denied(self):
        s,c=self.sample();self.f.contract.manifest['apps'][0]['pages'][0]['controls']=[]
        self.f.grant['manifest_sha256']=digest(self.f.contract.manifest)
        with self.assertRaises(PolicyDenied):self.ground(s,c)
        s,c=self.sample(native_tree(bounds=[0,301,720,500]),approve=False)
        with self.assertRaises(PolicyDenied):self.ground(s,c)


class NativeXmlEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def parse(self,extra):
        class Phone:
            async def hierarchy(self):
                return '<hierarchy rotation="0"><node bounds="[0,0][720,1600]" package="com.android.settings" '+extra+'/></hierarchy>'
        return await UIAutomatorBackend().read(Phone(),ScreenObservation.from_png(mock_png(),'offline'))
    async def test_explicit_native_visibility_alias_and_unknown_preserved(self):
        raw=await self.parse('visible-to-user="true"')
        self.assertIs(raw['windows'][0]['root']['visible'],True)
        self.assertIsNone(raw['windows'][0]['root']['editable'])
        raw=await self.parse('');self.assertIsNone(raw['windows'][0]['root']['visible'])
        raw=await self.parse('visible-to-user="false"');self.assertIs(raw['windows'][0]['root']['visible'],False)
    async def test_invalid_or_conflicting_serialized_values_reject(self):
        for attrs in ('visible="true" visible-to-user="false"','visible-to-user="yes"','editable="0"'):
            with self.assertRaises(ValueError):await self.parse(attrs)
    async def test_provider_rejects_old_png_and_future_timestamp_before_xml(self):
        class Phone:
            async def hierarchy(self):raise AssertionError('must not read stale evidence')
        obs=ScreenObservation.from_png(mock_png(),'offline')
        for timestamp in (obs.captured_monotonic_ns-3_000_000_000,obs.captured_monotonic_ns+3_000_000_000):
            s=await ColorOSSemanticProvider().collect(Phone(),replace(obs,captured_monotonic_ns=timestamp))
            self.assertEqual(s.evidence_quality,'semantic_unavailable')
    async def test_provider_uses_actual_acquisition_intervals(self):
        class Phone:
            async def observe(self):return ScreenObservation.from_png(mock_png(),'offline')
            async def foreground(self):return 'com.android.settings/.Settings'
        obs=ScreenObservation.from_png(mock_png(),'offline')
        with patch.object(UIAutomatorBackend,'read',return_value=native_tree()):
            s=await ColorOSSemanticProvider().collect(Phone(),obs)
        self.assertEqual(s.evidence_quality,'partial')
        t=s.attempts[0]
        self.assertLessEqual(t['png_received_ns'],t['xml_started_ns'])
        self.assertLessEqual(t['xml_finished_ns'],t['second_xml_started_ns'])
        self.assertLessEqual(t['second_xml_finished_ns'],t['validation_finished_ns'])
    async def test_slow_xml_not_refreshed_by_fast_foreground_query(self):
        obs=ScreenObservation.from_png(mock_png(),'offline');clock=[obs.captured_monotonic_ns]
        class Phone:
            async def observe(self):return replace(obs,captured_monotonic_ns=clock[0])
            async def foreground(self):return 'com.android.settings/.Settings'
        async def read(*args):clock[0]+=1_100_000_000;return native_tree()
        with patch('phonelife.physical_execution.time.monotonic_ns',side_effect=lambda:clock[0]),patch.object(UIAutomatorBackend,'read',side_effect=read):
            s=await ColorOSSemanticProvider().collect(Phone(),obs)
        self.assertEqual(s.evidence_quality,'semantic_unavailable')
    async def test_second_png_timestamp_cannot_precede_xml_completion(self):
        obs=ScreenObservation.from_png(mock_png(),'offline');clock=[obs.captured_monotonic_ns]
        class Phone:
            async def observe(self):return obs
            async def foreground(self):return 'com.android.settings/.Settings'
        async def read(*args):clock[0]+=10;return native_tree()
        with patch('phonelife.physical_execution.time.monotonic_ns',side_effect=lambda:clock[0]),patch.object(UIAutomatorBackend,'read',side_effect=read):
            s=await ColorOSSemanticProvider().collect(Phone(),obs)
        self.assertEqual(s.evidence_quality,'semantic_unavailable')


class DispatchDeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.f=core.PhysicalFixture(self.temp.name)
        self.stack=self.f.mocks();self.stack.__enter__();self.phone=PhysicalPhone(self.f.contract,self.f.adb);self.sent=[]
        async def send(args):self.sent.append(args)
        self.phone._send_input=send
        async def collect(phone,obs):return replace(self.f.semantic,observation_id=obs.observation_id,screenshot_sha256=obs.sha256)
        self.collect=collect;self.stack.enter_context(patch.object(ColorOSSemanticProvider,'collect',side_effect=collect))
    async def asyncTearDown(self):self.stack.close();self.f.close();self.temp.cleanup()
    def permit(self):
        s=self.f.semantic;c=next(c for c in s.controls if c.clickable)
        a,b=self.phone.capabilities.ground({'type':'tap_control','reason':'offline','observation_id':s.observation_id,'control_id':c.control_id},s)
        a=a.bind(self.f.obs)
        self.phone.prepare_dispatch(a,s,b,self.f.store,self.f.lease_adapter,'new-run','offline-owner',self.f.attempt,1)
        self.f.store.update_attempt(self.f.attempt,execution_status='uncertain_after_transport_error')
        return a
    async def test_final_guard_cannot_refresh_original_two_second_deadline(self):
        clock=[time.monotonic()]
        async def slow(phone,obs):
            clock[0]+=2.001
            return await self.collect(phone,obs)
        with patch('phonelife.physical_execution.time.monotonic',side_effect=lambda:clock[0]):
            a=self.permit()
            with patch.object(ColorOSSemanticProvider,'collect',side_effect=slow):
                with self.assertRaises(PolicyDenied):await self.phone.act(a)
        self.assertEqual(self.sent,[]);self.assertIsNone(self.phone._permit)
    async def test_last_identity_read_and_sync_guard_both_count_toward_deadline(self):
        for phase in ('identity','guard'):
            clock=[time.monotonic()];identity=self.phone._identity_online;guard=self.phone._final_guard
            calls=[0]
            async def final_identity():
                await identity();calls[0]+=1
                if calls[0]==4:clock[0]+=2.001
            def final_guard(*args):guard(*args);clock[0]+=2.001
            with patch('phonelife.physical_execution.time.monotonic',side_effect=lambda:clock[0]):
                a=self.permit()
                with patch.object(self.phone,'_identity_online',side_effect=final_identity if phase=='identity' else identity),patch.object(self.phone,'_final_guard',side_effect=final_guard if phase=='guard' else guard):
                    with self.assertRaises(PolicyDenied):await self.phone.act(a)
            self.assertEqual(self.sent,[])
    async def test_future_permit_clock_is_not_fresh(self):
        a=self.permit();p=self.phone._permit;self.phone._permit=(*p[:-1],time.monotonic()+10)
        with self.assertRaises(PolicyDenied):await self.phone.act(a)
        self.assertEqual(self.sent,[])
    async def test_transport_confirmation_not_business_success_and_no_replay(self):
        a=self.permit();result=await self.phone.act(a)
        self.assertIn('goal remains independently unverified',result)
        self.assertEqual(self.f.store.task('new-run')['status'],'running')
        with self.assertRaises(PolicyDenied):await self.phone.act(a)
        self.assertEqual(len(self.sent),1)
    async def test_partial_native_tap_full_executor_path_keeps_unknown_editable(self):
        self.f.semantic=normalize(native_tree(),self.f.obs,'com.android.settings/.Settings','coloros16_native_xml/1')
        self.f.contract.manifest['apps'][0]['pages'][0]['page_sha256']=page_key(self.f.semantic)
        self.f.grant['manifest_sha256']=digest(self.f.contract.manifest)
        # Keep the independent Status contract consistent with the reviewed
        # synthetic page too; an in-memory manifest alone is not authorization.
        state=self.f.ledger.load();spec=state['config']['physical_execution']
        path=Path(spec['manifest_path']);path.write_text(json.dumps(self.f.contract.manifest),encoding='utf8')
        spec['manifest_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        state['fingerprint']=digest(state['config']);self.f.ledger.save(state)
        self.f.grant['config_fingerprint']=state['fingerprint']
        self.f.contract=PhysicalContract(state['config'],state)
        self.phone.contract=self.f.contract;self.phone.capabilities=PhysicalRegistry(self.f.contract)
        result=await self.phone.act(self.permit())
        self.assertEqual(len(self.sent),1);self.assertFalse(self.phone._unknown)
        self.assertEqual(self.f.semantic.evidence_quality,'partial')
        self.assertTrue(all(c.editable is None for c in self.f.semantic.controls))
        self.assertIn('independently unverified',result)
