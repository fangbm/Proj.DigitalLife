"""offline_fixture: synthetic identities, mock ADB, isolated SQLite only."""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from phonelife.device_profiles import (DeviceProfileError,EmulatorDeviceProfile,PhysicalDeviceProfile,
    PhysicalDeviceProbe,ReadOnlyAdb,ProfileBoundReadOnlyPhone,PhysicalCapabilityPolicy,
    physical_admission,parse_size,parse_safety,xml_compatibility,main,IDENTITY_PROPERTIES)
from phonelife.devices import mock_png
from phonelife.models import Action
from phonelife.policy import ActionPolicy,PolicyDenied
from phonelife.longrun_state import SessionLedger
from phonelife.longrun import prepare_session
from phonelife.storage import Store
from phonelife.memory import LifeMemory
from phonelife.life_storage import enable_life
from phonelife.longrun import LongRunSoakController


IDENTITY={'ro.product.manufacturer':'OPPO','ro.product.brand':'OPPO','ro.product.model':'PGFM10',
    'ro.product.device':'offline-oppo','ro.build.version.release':'15','ro.build.version.sdk':'35',
    'ro.build.fingerprint':'offline/synthetic/test:15/fixture:user/release-keys','ro.kernel.qemu':'0'}
SERIAL='offline-usb-001'


class FakeAdb:
    def __init__(self):
        self.calls=[];self.props=dict(IDENTITY);self.online=True
        self.list=[{'serial':SERIAL,'state':'device','metadata':[]}]
        self.size='Physical size: 100x200';self.rotation=0;self.power='mInteractive=true'
        self.lock='mShowingLockscreen=false';self.windows='mCurrentFocus=Window{123 u0 com.android.settings/.Settings}'
        self.png=mock_png(100,200);self.hook=None
    async def devices(self):return self.list
    async def run(self,serial,args):
        self.calls.append((serial,args))
        if self.hook:self.hook(serial,args)
        if not self.online:raise DeviceProfileError('READONLY_ADB_FAILED')
        if args[:2]==('shell','getprop'):return self.props.get(args[2],'').encode()
        values={('get-state',):b'device',('shell','wm','size'):self.size.encode(),('shell','wm','density'):b'Physical density: 420',
            ('shell','dumpsys','input'):f'SurfaceOrientation: {self.rotation}'.encode(),('shell','dumpsys','power'):self.power.encode(),
            ('shell','dumpsys','window','policy'):self.lock.encode(),('shell','dumpsys','window','windows'):self.windows.encode(),
            ('shell','dumpsys','window','displays'):b'',
            ('shell','dumpsys','battery'):b'level: 60\ntemperature: 280\nAC powered: false\nUSB powered: true',
            ('shell','df','-k','/data'):b'Filesystem 1K-blocks Used Available Use% Mounted\n/data 100 40 60 40% /data',
            ('shell','pm','list','packages','--show-versioncode'):b'package:com.android.settings versionCode:35',
            ('exec-out','screencap','-p'):self.png}
        if args not in values:raise AssertionError('Unexpected command')
        return values[args]


def base_status():
    return {'safety_evidence_readable':True,'effective_state':'prepared','requires_manual_review':False,
        'safety_terminated':False,'worker_launch_allowed':True,'effective_resume_allowed':True}


class ProfileTests(unittest.IsolatedAsyncioTestCase):
    def profile(self,authorized=True):return PhysicalDeviceProfile(SERIAL,dict(IDENTITY),authorized)
    def test_default_profile_disabled(self):
        p=PhysicalDeviceProfile(SERIAL,dict(IDENTITY));self.assertFalse(p.read_only_authorized);self.assertFalse(p.input_enabled)
    def test_no_input_flag_can_enable_physical_dispatch(self):
        for value in [True,1,'true',None]:
            with self.assertRaises(DeviceProfileError):PhysicalDeviceProfile(SERIAL,dict(IDENTITY),input_enabled=value)
    def test_profile_requires_complete_identity(self):
        for key in IDENTITY:
            value=dict(IDENTITY);value.pop(key)
            with self.assertRaises(DeviceProfileError):PhysicalDeviceProfile(SERIAL,value)
    def test_profile_rejects_empty_fingerprint_and_wrong_types(self):
        for value in ['',None,[],123]:
            d=dict(IDENTITY);d['ro.build.fingerprint']=value
            with self.assertRaises(DeviceProfileError):PhysicalDeviceProfile(SERIAL,d)
    def test_model_or_manufacturer_mismatch(self):
        for key,value in [('ro.product.model','other'),('ro.product.manufacturer','other'),('ro.kernel.qemu','1')]:
            d=dict(IDENTITY);d[key]=value
            with self.assertRaises(DeviceProfileError):PhysicalDeviceProfile(SERIAL,d)
    def test_profile_identity_is_immutable_and_detached(self):
        d=dict(IDENTITY);p=PhysicalDeviceProfile(SERIAL,d,True);d['ro.product.model']='other'
        self.assertEqual(p.identity['ro.product.model'],'PGFM10')
        with self.assertRaises(TypeError):p.identity['ro.product.model']='other'
    def test_serial_and_build_exact_match(self):
        p=self.profile();p.verify(SERIAL,dict(IDENTITY))
        for serial,d in [('other',dict(IDENTITY)),(SERIAL,{**IDENTITY,'ro.build.fingerprint':'changed'})]:
            with self.assertRaises(DeviceProfileError):p.verify(serial,d)
    def test_qemu_property_absent_does_not_replace_other_identity(self):
        d={**IDENTITY,'ro.kernel.qemu':''};p=PhysicalDeviceProfile(SERIAL,d,True);p.verify(SERIAL,d)
        with self.assertRaises(DeviceProfileError):p.verify('another-device',d)
    def test_explicit_usb_serial_rejects_network_and_emulator(self):
        for serial in ['',None,'-s','192.168.1.2:5555','emulator-5582','adb-host._adb-tls-connect._tcp.']:
            with self.assertRaises(DeviceProfileError):PhysicalDeviceProbe(serial,FakeAdb())
    def test_emulator_qemu_contract_still_strict(self):
        e=EmulatorDeviceProfile('emulator-5582');e.verify('emulator-5582',{**IDENTITY,'ro.kernel.qemu':'1'})
        for key,value in [('ro.kernel.qemu','0'),('ro.build.version.sdk','34')]:
            with self.assertRaises(DeviceProfileError):e.verify('emulator-5582',{**IDENTITY,'ro.kernel.qemu':'1',key:value})
    def test_profile_types_cannot_be_mixed(self):
        with self.assertRaises(DeviceProfileError):ProfileBoundReadOnlyPhone(EmulatorDeviceProfile('emulator-5582'))
        with self.assertRaises(DeviceProfileError):EmulatorDeviceProfile(SERIAL)
    def test_duplicate_profile_keys_and_unknown_fields_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'p.json';p.write_text('{"serial":"x","serial":"y"}',encoding='utf8')
            with self.assertRaises(DeviceProfileError):PhysicalDeviceProfile.load(p)
            p.write_text(json.dumps({**self.profile().wire(),'run':True}),encoding='utf8')
            with self.assertRaises(DeviceProfileError):PhysicalDeviceProfile.load(p)
    def test_profile_private_roundtrip_preserves_disabled(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'p.json';p=PhysicalDeviceProfile(SERIAL,dict(IDENTITY));path.write_text(json.dumps(p.wire()),encoding='utf8')
            q=PhysicalDeviceProfile.load(path);self.assertEqual(q.identity_sha256,p.identity_sha256);self.assertFalse(q.read_only_authorized)
    async def test_default_observation_sends_no_adb(self):
        adb=FakeAdb();phone=ProfileBoundReadOnlyPhone(self.profile(False),adb)
        with self.assertRaises(DeviceProfileError):await phone.observe()
        self.assertEqual(adb.calls,[])
    async def test_inventory_requires_readonly_consent(self):
        adb=FakeAdb()
        with self.assertRaises(DeviceProfileError):await PhysicalDeviceProbe(SERIAL,adb).inventory()
        self.assertEqual(adb.calls,[])
    async def test_multiple_devices_use_only_explicit_target(self):
        adb=FakeAdb();adb.list.append({'serial':'other-usb','state':'device','metadata':[]})
        value=await PhysicalDeviceProbe(SERIAL,adb).inventory(consent=True)
        self.assertEqual(value['adb_device_count'],2);self.assertEqual({s for s,_ in adb.calls},{SERIAL})
    async def test_unauthorized_or_duplicate_target_stops_before_metadata(self):
        for rows in [[{'serial':SERIAL,'state':'unauthorized'}],[{'serial':'other','state':'device'}],
                     [{'serial':SERIAL,'state':'device'},{'serial':SERIAL,'state':'device'}]]:
            adb=FakeAdb();adb.list=rows
            with self.assertRaises(DeviceProfileError):await PhysicalDeviceProbe(SERIAL,adb).inventory(consent=True)
            self.assertFalse(adb.calls)
    async def test_usb_disconnect_no_retry(self):
        adb=FakeAdb();adb.online=False
        with self.assertRaises(DeviceProfileError):await PhysicalDeviceProbe(SERIAL,adb).inventory(consent=True)
        self.assertEqual(len(adb.calls),1)
    async def test_identity_change_during_probe_refuses_binding(self):
        adb=FakeAdb()
        def hook(serial,args):
            if args==('shell','pm','list','packages','--show-versioncode'):adb.props['ro.build.fingerprint']='changed'
        adb.hook=hook
        with self.assertRaisesRegex(DeviceProfileError,'IDENTITY_CHANGED'):await PhysicalDeviceProbe(SERIAL,adb).inventory(consent=True)
    async def test_observation_matches_actual_png_dimensions(self):
        adb=FakeAdb();obs=await ProfileBoundReadOnlyPhone(self.profile(),adb).observe()
        self.assertEqual((obs.width,obs.height),(100,200))
    async def test_landscape_uses_real_png_not_emulator_coordinates(self):
        adb=FakeAdb();adb.rotation=1;adb.png=mock_png(200,100)
        obs=await ProfileBoundReadOnlyPhone(self.profile(),adb).observe();self.assertEqual((obs.width,obs.height),(200,100))
    async def test_geometry_mismatch_stops_capture(self):
        adb=FakeAdb();adb.png=mock_png(99,200)
        with self.assertRaisesRegex(DeviceProfileError,'GEOMETRY_CHANGED'):await ProfileBoundReadOnlyPhone(self.profile(),adb).observe()
    async def test_mid_capture_resolution_change_is_rejected(self):
        adb=FakeAdb()
        def hook(serial,args):
            if args==('exec-out','screencap','-p'):adb.size='Physical size: 200x400'
        adb.hook=hook
        with self.assertRaisesRegex(DeviceProfileError,'GEOMETRY_CHANGED'):await ProfileBoundReadOnlyPhone(self.profile(),adb).observe()
    async def test_identity_changed_before_observation_no_screenshot(self):
        adb=FakeAdb();adb.props['ro.product.device']='another'
        with self.assertRaises(DeviceProfileError):await ProfileBoundReadOnlyPhone(self.profile(),adb).observe()
        self.assertFalse(any(a==('exec-out','screencap','-p') for _,a in adb.calls))
    async def test_locked_or_unknown_lock_stops(self):
        for lock in ['mShowingLockscreen=true','unrecognized=0','showing=false']:
            adb=FakeAdb();adb.lock=lock
            with self.assertRaises(DeviceProfileError):await ProfileBoundReadOnlyPhone(self.profile(),adb).observe()
            self.assertFalse(any(a==('exec-out','screencap','-p') for _,a in adb.calls))
    async def test_screen_off_stops_no_unlock(self):
        adb=FakeAdb();adb.power='mInteractive=false'
        with self.assertRaises(DeviceProfileError):await ProfileBoundReadOnlyPhone(self.profile(),adb).observe()
        self.assertFalse(any(a[:2]==('shell','input') for _,a in adb.calls))
    async def test_permission_popup_stops(self):
        adb=FakeAdb();adb.windows='mCurrentFocus=Window{x u0 com.android.permissioncontroller/.GrantPermissionsActivity}'
        with self.assertRaises(DeviceProfileError):await ProfileBoundReadOnlyPhone(self.profile(),adb).observe()
    async def test_unknown_overlay_stops(self):
        adb=FakeAdb();adb.windows+=' TYPE_APPLICATION_OVERLAY'
        with self.assertRaises(DeviceProfileError):await ProfileBoundReadOnlyPhone(self.profile(),adb).observe()
    async def test_unapproved_app_stops(self):
        adb=FakeAdb();adb.windows='mCurrentFocus=Window{x u0 com.tencent.mobileqq/.Main}'
        with self.assertRaises(DeviceProfileError):await ProfileBoundReadOnlyPhone(self.profile(),adb).observe()
    async def test_all_physical_actions_disabled_without_adb(self):
        adb=FakeAdb();phone=ProfileBoundReadOnlyPhone(self.profile(),adb)
        for kind in ['tap','swipe','text','home','back','launch']:
            with self.assertRaises(PolicyDenied):await phone.act(Action(type=kind))
        with self.assertRaises(PolicyDenied):await phone.reopen_settings()
        self.assertEqual(adb.calls,[])
    async def test_inventory_is_metadata_only_and_profile_not_granted(self):
        adb=FakeAdb();value=await PhysicalDeviceProbe(SERIAL,adb).inventory(consent=True)
        self.assertEqual(value['adb_inputs'],0);self.assertFalse(value['physical_autonomy_ready'])
        self.assertFalse(any(a[0]=='exec-out' for _,a in adb.calls))
    async def test_transport_rejects_arbitrary_shell_and_app_install(self):
        transport=ReadOnlyAdb()
        for args in [('shell','input','tap','1','1'),('install','anything.apk'),('shell','pm','grant','pkg','perm'),('shell','am','start','-d','intent:anything')]:
            with patch.object(transport,'_run',side_effect=AssertionError('must reject before process')):
                with self.assertRaises(DeviceProfileError):await transport.run(SERIAL,args)
    def test_physical_registry_never_grants_app_or_message(self):
        policy=PhysicalCapabilityPolicy();self.assertFalse(policy.wire()['default_enabled'])
        for action in [Action(type='launch',package='com.tencent.mobileqq'),Action(type='text',text='message'),Action(type='tap',x=1,y=1)]:
            with self.assertRaises(PolicyDenied):policy.check(action)
    def test_original_action_policy_remains_strict(self):
        for kind in ['tap','swipe','text']:
            with self.assertRaises(PolicyDenied):ActionPolicy('real',frozenset({'com.android.settings'})).check(Action(type=kind))
    def test_admission_never_turns_diagnostics_into_resume_or_worker(self):
        result=physical_admission(base_status(),identity_matches=True,screen_safe=True)
        self.assertFalse(result['worker_launch_allowed']);self.assertFalse(result['effective_resume_allowed'])
        self.assertEqual(result['physical_admission_reason'],'PHYSICAL_AUTONOMY_NOT_IMPLEMENTED')
    def test_physical_status_legacy_alias_cannot_report_resume_allowed(self):
        result=physical_admission({**base_status(),'resume_allowed':True,'admission_reason_code':'ALLOWED'},identity_matches=True,screen_safe=True)
        self.assertFalse(result['resume_allowed']);self.assertFalse(result['effective_resume_allowed'])
        self.assertEqual(result['effective_state'],'blocked');self.assertEqual(result['admission_reason_code'],'PHYSICAL_AUTONOMY_NOT_IMPLEMENTED')
    def test_emergency_priority_and_review_fact_preserved(self):
        value={**base_status(),'effective_state':'emergency_stopped','requires_manual_review':True,'safety_terminated':True}
        result=physical_admission(value,identity_matches=True,screen_safe=True)
        self.assertEqual(result['physical_admission_reason'],'OPERATOR_STOP');self.assertTrue(result['requires_manual_review']);self.assertTrue(result['safety_terminated'])
    def test_unknown_dispatch_and_reconnect_never_grant_resume(self):
        value={**base_status(),'requires_manual_review':True}
        for connected in [False,True]:
            result=physical_admission(value,identity_matches=connected,screen_safe=True)
            self.assertEqual(result['physical_admission_reason'],'UNKNOWN_DISPATCH_NEEDS_REVIEW');self.assertFalse(result['worker_launch_allowed'])
    def test_missing_safety_evidence_fails_closed(self):
        result=physical_admission({},identity_matches=True,screen_safe=True)
        self.assertEqual(result['physical_admission_reason'],'SAFETY_EVIDENCE_UNAVAILABLE')
    def test_emergency_remains_highest_with_unreadable_history(self):
        result=physical_admission({**base_status(),'effective_state':'emergency_stopped','safety_evidence_readable':False})
        self.assertEqual(result['physical_admission_reason'],'OPERATOR_STOP')
    async def test_rotation_changes_during_capture_are_rejected(self):
        adb=FakeAdb()
        def hook(serial,args):
            if args==('exec-out','screencap','-p'):adb.rotation=1
        adb.hook=hook
        with self.assertRaisesRegex(DeviceProfileError,'SAFETY_CHANGED'):await ProfileBoundReadOnlyPhone(self.profile(),adb).observe()
    async def test_lock_or_permission_changes_during_capture_are_rejected(self):
        adb=FakeAdb()
        def hook(serial,args):
            if args==('exec-out','screencap','-p'):adb.lock='mShowingLockscreen=true'
        adb.hook=hook
        with self.assertRaisesRegex(DeviceProfileError,'SAFETY_CHANGED'):await ProfileBoundReadOnlyPhone(self.profile(),adb).observe()
    def test_cross_device_old_identity_rejected(self):
        p=self.profile()
        with self.assertRaises(DeviceProfileError):p.verify('emulator-5582',{**IDENTITY,'ro.kernel.qemu':'1'})
    def test_xml_missing_coloros_attributes_is_not_trusted(self):
        result=xml_compatibility('<hierarchy rotation="0"><node bounds="[0,0][100,200]" enabled="true"/></hierarchy>')
        self.assertEqual(result['required_missing']['editable'],1);self.assertFalse(result['trusted_for_physical_input'])
    def test_xml_entity_rejected(self):
        with self.assertRaises(DeviceProfileError):xml_compatibility('<!DOCTYPE hierarchy><hierarchy/>')
    def test_display_override_is_not_assumed_720(self):
        self.assertEqual(parse_size('Physical size: 1264x2780\nOverride size: 1080x2376'),(1080,2376))
    def test_unknown_orientation_is_not_assumed_portrait(self):
        self.assertIsNone(parse_safety('mInteractive=true','mShowingLockscreen=false','','no rotation')['orientation'])
    def test_coloros_keyguard_service_and_internal_viewport_shape(self):
        result=parse_safety('mWakefulness=Awake','KeyguardServiceDelegate\n showing=false\n KeyguardStateMonitor\n mIsShowing=false\n Looper state:',
            'mCurrentFocus=Window{x u0 com.android.settings/.Settings}',
            'Viewport INTERNAL: displayId=0, uniqueId=offline, orientation=1, logicalFrame=[0,0,200,100]')
        self.assertFalse(result['locked']);self.assertEqual(result['orientation'],1)
    def test_coloros_conflicting_lock_or_rotation_fails_conservatively(self):
        result=parse_safety('mInteractive=true','KeyguardServiceDelegate\n showing=false\n mIsShowing=true\n Looper state:','',
            'Viewport INTERNAL: displayId=0, orientation=0\nViewport INTERNAL: displayId=0, orientation=1')
        self.assertTrue(result['locked']);self.assertIsNone(result['orientation'])
    def test_unrelated_showing_field_does_not_unlock(self):
        result=parse_safety('mInteractive=true','SomeUnrelatedService\n showing=false','','')
        self.assertIsNone(result['locked'])
    def test_no_cli_consent_means_no_adapter(self):
        with tempfile.TemporaryDirectory() as d:
            with patch('phonelife.device_profiles.ReadOnlyAdb',side_effect=AssertionError('no consent')):
                self.assertEqual(main(['--serial',SERIAL,'--output',str(Path(d)/'new')]),2)
    def test_restart_keeps_shared_unknown_barrier(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'control.sqlite3';l=SessionLedger(path);l.create({'source_kind':'offline_fixture'},10)
            l.latch_review({'sources':[{'task_run_id':'synthetic','attempt_id':'unknown','execution_status':'uncertain_after_transport_error'}]});l.close()
            l=SessionLedger(path,readonly=True)
            try:self.assertEqual(l.load()['state'],'failed');self.assertTrue(l.load()['requires_manual_review'])
            finally:l.close()
    def test_marker_failed_sqlite_stop_not_reclassified_by_physical_gate(self):
        with tempfile.TemporaryDirectory() as d:
            l=SessionLedger(Path(d)/'control.sqlite3');l.create({'source_kind':'offline_fixture'},10)
            with patch.object(Path,'open',side_effect=PermissionError('offline injected')):l.latch_review({'sources':[]})
            v=l.load();l.close()
            result=physical_admission({**base_status(),'requires_manual_review':v['requires_manual_review'],'safety_terminated':True})
            self.assertFalse(result['worker_launch_allowed']);self.assertTrue(result['safety_terminated'])
    def test_sqlite_failed_marker_still_rejects_physical_gate(self):
        with tempfile.TemporaryDirectory() as d:
            l=SessionLedger(Path(d)/'control.sqlite3');l.create({'source_kind':'offline_fixture'},10);l.conn.execute('PRAGMA query_only=ON')
            try:l.latch_review({'sources':[]})
            except Exception:pass
            v=l.load();l.close()
            result=physical_admission({**base_status(),'requires_manual_review':v['requires_manual_review'],'safety_terminated':True})
            self.assertFalse(result['effective_resume_allowed']);self.assertTrue(result['requires_manual_review'])
    def test_life_namespace_cannot_be_silently_renamed_to_real(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'life.sqlite3');enable_life(store)
            try:
                store.start_task('offline-source','emulator','Offline namespace fixture',{},run_kind='autonomous')
                memory=LifeMemory(store,'emulator','autonomous');memory.add('episodic','offline topic',{'objective':{'status':'uncertain'}},'offline-source')
                before=store.conn.execute('SELECT memory_id,device,body FROM life_memories').fetchall()
                self.assertTrue(memory.retrieve());self.assertEqual(LifeMemory(store,'real','autonomous').retrieve(),[])
                self.assertEqual(before,store.conn.execute('SELECT memory_id,device,body FROM life_memories').fetchall())
            finally:store.close()
    def test_physical_profile_shares_legacy_real_lease_without_force(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'lease.sqlite3')
            try:
                self.assertEqual(self.profile().lease_key,'real')
                self.assertTrue(store.acquire('real','owner-1',30));self.assertFalse(store.acquire(self.profile().lease_key,'owner-2',30))
                store.release('real','owner-1');self.assertTrue(store.acquire('real','owner-2',30));store.release('real','owner-2')
            finally:store.close()
    async def test_existing_supervisor_cannot_mislabel_physical_as_qemu(self):
        class PhysicalProbe:
            async def sample(self):return {'online':True,'qemu':False,'sdk':'35'}
        with tempfile.TemporaryDirectory() as d:
            directory=Path(d)/'session';prepare_session(directory,mode='mock',seconds=10)
            controller=LongRunSoakController(directory,probe=PhysicalProbe())
            try:
                with patch('phonelife.longrun.subprocess.Popen',side_effect=AssertionError('worker forbidden')):
                    result=await controller.run()
                self.assertEqual(result['state'],'failed');self.assertEqual(result['reason'],'DEVICE_IDENTITY_CHANGED')
            finally:controller.close()
    async def test_readonly_transport_keeps_exact_target_and_arguments(self):
        transport=ReadOnlyAdb()
        async def run(args,limit):
            self.assertEqual(args,('-s',SERIAL,'shell','getprop','ro.product.model'));return b'PGFM10'
        with patch.object(transport,'_run',side_effect=run):
            self.assertEqual(await transport.run(SERIAL,('shell','getprop','ro.product.model')),b'PGFM10')


if __name__=='__main__':unittest.main()
