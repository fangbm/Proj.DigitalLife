"""offline_fixture: actual dedicated executor/worker with synthetic ADB only."""
import asyncio
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from phonelife import dedicated_device as d
from phonelife.devices import mock_png,DeviceTransportError
from phonelife.device_profiles import PhysicalDeviceProfile
from phonelife.life_storage import enable_life
from phonelife.longrun_state import SessionLedger
from phonelife.physical_execution import PhysicalRegistry
from phonelife.storage import Store
from phonelife.models import Action,ScreenObservation
from phonelife.semantic import normalize,UIAutomatorBackend
from phonelife.policy import PolicyDenied
from test_device_profiles import FakeAdb,IDENTITY,SERIAL

def xml(after=False,extra=''):
    def node(cls,bounds,children='',**attrs):
        values={'class':cls,'bounds':bounds,'package':'com.android.settings','clickable':'false','scrollable':'false',
                'enabled':'true','checkable':'false','password':'false','long-clickable':'false'}|attrs
        return '<node '+ ' '.join(k+'="'+v+'"' for k,v in values.items())+'>'+children+'</node>'
    if after:
        inner=node('android.widget.TextView','[0,80][720,160]',text='显示与亮度',**{'resource-id':'com.android.settings:id/toolbar_title'})
    else:
        title=node('android.widget.TextView','[100,410][600,530]',text='显示与亮度',**{'resource-id':'android:id/title'})
        row=node('android.widget.LinearLayout','[0,400][720,560]',title,clickable='true')
        inner=node('androidx.recyclerview.widget.RecyclerView','[0,200][720,1500]',row,scrollable='true')
    return '<?xml version="1.0"?><hierarchy rotation="0">'+node('android.widget.FrameLayout','[0,0][720,1600]',inner+extra)+'</hierarchy>'

class DedicatedAdb(FakeAdb):
    def __init__(self):
        super().__init__();self.props.update({'ro.build.version.sdk':'36','ro.build.version.release':'16'})
        self.after=False;self.extra='';self.windows='mCurrentFocus=Window{abc u0 com.android.settings/.Settings}'
    async def run(self,serial,args):
        if args==('exec-out','uiautomator','dump','/proc/self/fd/1'):
            self.calls.append((serial,args));return xml(self.after,self.extra).encode()
        if args==('exec-out','screencap','-p'):self.png=mock_png(720,1600,100 if self.after else 0)
        return await super().run(serial,args)

class Fixture:
    def __init__(self,root):
        self.root=Path(root);self.source=self.root/'history.sqlite3';s=Store(self.source);enable_life(s)
        s.start_task('old-life','emulator','historical autonomous rest',{},run_kind='autonomous')
        s.conn.execute("UPDATE task_runs SET status='rested' WHERE task_run_id='old-life'")
        s.conn.execute("INSERT INTO life_requests(request_id,device,run_kind,task_run_id,phase,requested_at,status) VALUES('old-request','emulator','autonomous','old-life','brain',?,'received')",(time.time(),))
        self.wake=time.time()+1800
        s.conn.execute("INSERT OR REPLACE INTO schedule VALUES('emulator',?,'{}')",(self.wake,))
        s.conn.execute("INSERT INTO life_interests VALUES('emulator','autonomous','historical curiosity',1.0,3,'offline')")
        s.close();self.source_sha=hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.lease=self.root/'coordinator.sqlite3';s=Store(self.lease);s.close()
        self.adb=DedicatedAdb();self.profile=PhysicalDeviceProfile(SERIAL,dict(self.adb.props))
        self.profile_path=self.root/'original-profile.json';self.profile_path.write_text(json.dumps(self.profile.wire()),'utf8')
        self.life=self.root/'private-life'/'agent.sqlite3';self.session=self.root/'smoke'
    def create(self,session=None,smoke=True,seconds=180,first=True):
        return d.create_session(session or self.session,profile_path=self.profile_path,life_db=self.life,
            source_db=self.source if first else None,lease_path=self.lease,smoke=smoke,seconds=seconds)
    def phone(self,contract):
        phone=d.DedicatedPhone(contract,self.adb);self.phone_instance=phone
        async def send(args):
            self.sent.append(args)
            if self.input_error:raise OSError('offline injected USB outcome unknown')
            if self.change:
                self.adb.after=True;self.adb.windows='mCurrentFocus=Window{abc u0 com.android.settings/.DisplaySettings}'
        phone._send_input=send
        if getattr(self,'rotate_before_dispatch',False):
            original=phone.capabilities.revalidate;count=0
            def changed_scene(*args):
                nonlocal count
                result=original(*args);count+=1
                if count==2:self.adb.rotation=1
                return result
            phone.capabilities.revalidate=changed_scene
        return phone
    async def run_smoke(self,input_error=False,change=True):
        self.sent=[];self.input_error=input_error;self.change=change
        factory=self.phone
        # Save real constructor: worker imports the factory below.
        original=d.DedicatedPhone
        def make(contract):
            with patch.object(d,'DedicatedPhone',original):return factory(contract)
        with (patch.object(d,'DedicatedPhone',side_effect=make),patch('phonelife.longrun_api.SoakAPI',side_effect=AssertionError('No API in smoke')),
             patch('phonelife.physical_execution.unprotect_approval',side_effect=AssertionError('No DPAPI action grant'))):
            return await d.run_manual_smoke(self.session)

class DedicatedModeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.f=Fixture(self.tmp.name)
    async def semantic(self):
        v=self.f.create();ledger=SessionLedger(self.f.session/'soak-control.sqlite3');ledger.start();ledger.close()
        phone=d.DedicatedPhone(d.DedicatedContract(v['config'],v),self.f.adb)
        obs=await phone.observe();s=await d.DedicatedSemanticProvider().collect(phone,obs)
        return phone,obs,s
    async def test_unknown_visibility_editable_remain_unknown_and_navigation_is_allowed(self):
        phone,obs,s=await self.semantic()
        self.assertTrue(all(c.visible is None and c.editable is None for c in s.controls))
        safe=phone.capabilities.view(s)['controls'];row=next(c for c in safe if c['intent']=='tap_control')
        action,binding=phone.capabilities.ground({'type':'tap_control','reason':'offline navigation','observation_id':s.observation_id,'control_id':row['control_id']},s)
        self.assertEqual(action.type,'tap');self.assertEqual(binding['evidence_source'],d.PROVIDER)
        with self.assertRaises(PolicyDenied):PhysicalRegistry(None).safe_target(s,s.controls[-1])
    async def test_actual_worker_smoke_click_and_independent_page_change_without_api_or_dpapi(self):
        self.f.create();result=await self.f.run_smoke()
        self.assertEqual(result['settings_smoke']['status'],'succeeded')
        self.assertTrue(result['settings_smoke']['independent_page_change']);self.assertEqual(len(self.f.sent),1)
        self.assertEqual(self.f.sent[0][0],'tap');self.assertEqual(result['state'],'paused')
        self.assertFalse(self.f.phone_instance._unknown)
        s=Store.open_readonly(self.f.life)
        self.addCleanup(s.close)
        self.assertEqual(s.conn.execute('SELECT COUNT(*) FROM life_requests').fetchone()[0],1)
        self.assertEqual(s.conn.execute('SELECT run_kind FROM task_runs ORDER BY rowid DESC LIMIT 1').fetchone()[0],'manual_targeted')
        self.assertFalse(s.conn.execute('SELECT 1 FROM lease').fetchone());s.close()
        self.assertEqual(hashlib.sha256(self.f.source.read_bytes()).hexdigest(),self.f.source_sha)
        self.assertFalse(PhysicalDeviceProfile.load(self.f.profile_path).read_only_authorized)
    async def test_no_input_when_control_stopped(self):
        phone,obs,s=await self.semantic();ledger=SessionLedger(self.f.session/'soak-control.sqlite3');ledger.operator('emergency_stopped');ledger.close()
        with self.assertRaises(PolicyDenied):phone._final_guard(None,None,'x','x','x')
    async def test_unknown_input_outcome_latches_stop_and_is_not_retried(self):
        self.f.create();result=await self.f.run_smoke(input_error=True)
        self.assertEqual(len(self.f.sent),1);self.assertTrue(result['requires_manual_review'])
        self.assertEqual(result['state'],'failed');self.assertTrue(self.f.phone_instance._unknown)
        with self.assertRaises(PolicyDenied):await self.f.phone_instance.act(Action('tap',x=360,y=480))
        self.assertTrue((self.f.session/'NEEDS_REVIEW.json').exists() or result['persistent_barrier_present'])
        s=Store.open_readonly(self.f.lease);self.assertFalse(s.conn.execute('SELECT 1 FROM lease').fetchone());s.close()
    async def test_no_page_change_is_not_false_success(self):
        self.f.create();result=await self.f.run_smoke(change=False)
        self.assertNotEqual(result['settings_smoke']['status'],'succeeded')
        self.assertFalse(result['settings_smoke']['independent_page_change']);self.assertEqual(len(self.f.sent),1)
    async def test_rotation_after_xml_revalidation_denies_before_input(self):
        self.f.create();self.f.rotate_before_dispatch=True
        result=await self.f.run_smoke()
        self.assertEqual(self.f.sent,[])
        self.assertFalse(result['requires_manual_review'])
        self.assertNotEqual(result['settings_smoke']['status'],'succeeded')
    async def test_one_hour_uses_same_life_and_budget_with_no_fingerprint_approval(self):
        self.f.create();await self.f.run_smoke()
        with patch.dict(os.environ,{'PHONE_LIFE_API_BASE':'https://example.invalid','PHONE_LIFE_API_KEY':'offline-placeholder','PHONE_LIFE_BRAIN_MODEL':'offline','PHONE_LIFE_GUI_MODEL':'offline'}):
            value=self.f.create(self.f.root/'hour',smoke=False,seconds=3600,first=False)
        self.assertIsNone(value['config']['test_controller']);self.assertEqual(value['config']['agent_db'],str(self.f.life))
        self.assertEqual(value['state'],'prepared')
        ledger=SessionLedger(self.f.root/'hour/soak-control.sqlite3');ledger.start();self.assertEqual(ledger.load()['state'],'running');ledger.close()
        s=Store.open_readonly(self.f.life)
        self.assertEqual(s.conn.execute('SELECT COUNT(*) FROM life_requests').fetchone()[0],1)
        self.assertEqual(s.conn.execute("SELECT next_wake_at FROM schedule WHERE device='subject:digital-life-main'").fetchone()[0],self.f.wake)
        self.assertEqual(tuple(s.conn.execute("SELECT weight,evidence_count FROM life_interests WHERE device='subject:digital-life-main'").fetchone()),(1.0,3))
        s.close()
    async def test_swipe_uses_current_list_without_inventing_visibility(self):
        phone,obs,s=await self.semantic();target=next(c for c in s.controls if c.scrollable)
        action,binding=phone.capabilities.ground({'type':'scroll_region','reason':'offline scroll','observation_id':s.observation_id,'scrollable_region_id':target.control_id,'direction':'toward_end'},s)
        self.assertEqual(action.type,'swipe');self.assertGreater(action.y,action.y2)
        self.assertIsNone(target.visible)
    async def test_state_changing_controls_and_high_risk_route_denied(self):
        phone,obs,s=await self.semantic();target=next(c for c in s.controls if c.clickable)
        for updated in (replace(target,checkable=True),replace(target,editable=True),replace(target,class_name='android.widget.Switch'),replace(target,visible=False),replace(target,enabled=False)):
            changed=replace(s,controls=tuple(updated if c==target else c for c in s.controls))
            with self.assertRaises(PolicyDenied):phone.capabilities.safe_target(changed,updated)
    async def test_foreign_xml_stale_ids_and_changed_target_denied(self):
        phone,obs,s=await self.semantic();row=next(c for c in phone.capabilities.view(s)['controls'] if c['intent']=='tap_control')
        candidate={'type':'tap_control','reason':'offline','observation_id':s.observation_id,'control_id':row['control_id']}
        action,binding=phone.capabilities.ground(candidate,s)
        with self.assertRaises(ValueError):phone.capabilities.ground(candidate|{'observation_id':'stale'},s)
        with self.assertRaises(PolicyDenied):phone.capabilities.page(replace(s,foreground_package='other.app'))
        with self.assertRaises(ValueError):phone.capabilities.revalidate(binding,s,replace(s,controls=()))
    async def test_arbitrary_shell_text_launch_and_home_denied(self):
        phone,obs,s=await self.semantic()
        for kind in ('shell','text','launch','home','open_allowed_app'):
            with self.assertRaises(ValueError):phone.capabilities.ground({'type':kind},s)
    async def test_unknown_input_layer_alone_is_not_a_veto_but_concrete_blocker_is(self):
        phone,obs,s=await self.semantic();action=Action('tap',x=360,y=480)
        phone._window_metadata={'input':'UNKNOWN_COLOROS_INPUT_HANDLE TYPE_2999','windows':''}
        phone._check_touch_blockers(action)
        phone._window_metadata['input']='DROP_INPUT, touchableRegion=[0,0][720,1600]'
        with self.assertRaises(PolicyDenied):phone._check_touch_blockers(action)
    async def test_visible_overlay_only_blocks_covered_target(self):
        phone,obs,s=await self.semantic()
        phone._window_metadata={'input':'','windows':'Window #1 Window{1 u0 other.app/.Overlay}\nmViewVisibility=0x0 mHasSurface=true package=other.app ty=2038 mFrame=[0,0][300,300]'}
        phone._check_touch_blockers(Action('tap',x=360,y=480))
        with self.assertRaises(PolicyDenied):phone._check_touch_blockers(Action('tap',x=100,y=200))
    async def test_status_does_not_probe_device(self):
        self.f.create()
        with patch('subprocess.run',side_effect=AssertionError('status must not run ADB')):
            result=SessionLedger.read_status(self.f.session/'soak-control.sqlite3')
        self.assertEqual(result['execution_mode'],d.MODE);self.assertFalse(result['per_action_approval_required'])
        self.assertIsNone(result['physical_device']['online'])
    def test_unknown_history_prevents_new_session_without_rewriting_it(self):
        self.f.create();s=Store(self.f.life);s.start_task('unknown', 'physical:'+self.f.profile.identity_sha256,'offline',{},run_kind='autonomous')
        aid=s.attempt('unknown',1,1,{'execution_status':'uncertain_after_transport_error'});s.close()
        with self.assertRaisesRegex(PermissionError,'UNKNOWN_DISPATCH'):
            self.f.create(self.f.root/'new-smoke',first=False)
        s=Store.open_readonly(self.f.life);self.assertEqual(json.loads(s.conn.execute('SELECT payload FROM action_attempts WHERE attempt_id=?',(aid,)).fetchone()[0])['execution_status'],'uncertain_after_transport_error');s.close()
    def test_one_hour_requires_real_smoke_record_and_cannot_recopy_budget(self):
        self.f.create()
        with self.assertRaises(ValueError):self.f.create(self.f.root/'overwrite')
        with patch.dict(os.environ,{'PHONE_LIFE_API_BASE':'https://example.invalid','PHONE_LIFE_API_KEY':'offline-placeholder','PHONE_LIFE_BRAIN_MODEL':'offline','PHONE_LIFE_GUI_MODEL':'offline'}):
            with self.assertRaisesRegex(ValueError,'Settings smoke'):self.f.create(self.f.root/'hour',smoke=False,seconds=3600,first=False)
    def test_device_lease_conflict_refuses_setup_without_stealing(self):
        s=Store(self.f.lease);s.acquire('real','other-owner',120)
        with self.assertRaises(PermissionError):self.f.create()
        self.assertEqual(s.conn.execute("SELECT owner FROM lease WHERE device='real'").fetchone()[0],'other-owner');s.close()
    def test_24_hour_does_not_auto_start_or_accept_old_formal_candidate(self):
        with self.assertRaises(ValueError):d.verify_one_hour(None,self.f.life,self.f.profile.identity_sha256)
        self.f.create()
        with self.assertRaises(ValueError):d.verify_one_hour(self.f.session,self.f.life,self.f.profile.identity_sha256)
