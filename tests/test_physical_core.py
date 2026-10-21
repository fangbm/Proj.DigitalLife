"""offline_fixture only: synthetic clocks/devices and temporary databases."""
import asyncio
import json
import math
import os
import sqlite3
import subprocess
import sys
import time
import copy
from dataclasses import replace,asdict
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from phonelife.longrun import prepare_session
from phonelife.longrun_state import SessionLedger
from phonelife.longrun_state import canonical,SoakPolicy,REVIEW_STOP
from phonelife.life_subject import LifeBinding,migrate_subject,attach_subject
from phonelife.life_storage import enable_life
from phonelife.storage import Store
from phonelife.memory import LifeMemory
from phonelife.budgets import RequestBudget,LifeLimits,BudgetExceeded
from phonelife.autonomy import AutonomousLife
from phonelife.brain import MockLifeBrain
from phonelife.device_profiles import PhysicalDeviceProfile,digest
from phonelife.physical_execution import (PhysicalContract,PhysicalPhone,PhysicalActionPolicy,
    PhysicalRegistry,ColorOSSemanticProvider,LeaseNamespace,visible_windows_safe,page_key,physical_session_admission)
from phonelife.policy import PolicyDenied
from phonelife.devices import DeviceTransportError,mock_png
from phonelife.models import Action,ScreenObservation
from phonelife.semantic import normalize
from test_device_profiles import FakeAdb,IDENTITY,SERIAL
from test_semantics import tree

SAFE_WINDOWS='''mCurrentFocus=Window{123 u0 com.android.settings/.Settings}
Window #0 Window{123 u0 com.android.settings/.Settings}
 mViewVisibility=0x0 mHasSurface=true ty=1 package=com.android.settings
'''
ONLINE={'bound':True,'online':True,'identity_verified':True,'observation_allowed':True,'execution_allowed':True,'user_authorized':True}

class ExecutionAdb(FakeAdb):
    def __init__(self):
        super().__init__();self.props.update({'ro.build.version.sdk':'36','ro.build.version.release':'16'})
        self.windows=SAFE_WINDOWS;self.size='Physical size: 720x1600';self.png=mock_png()
    async def run(self,serial,args):
        if args==('shell','pm','list','packages','--show-versioncode'):return b'package:com.android.settings versionCode:36'
        return await super().run(serial,args)

class PhysicalFixture:
    def __init__(self,root):
        self.directory=Path(root)/'session';prepare_session(self.directory,seconds=120,policy=SoakPolicy(min_free_bytes=1))
        self.ledger=SessionLedger(self.directory/'soak-control.sqlite3');v=self.ledger.load()
        self.adb=ExecutionAdb();self.profile=PhysicalDeviceProfile(SERIAL,self.adb.props.copy() | {k:IDENTITY[k] for k in []},True)
        # FakeAdb stores exactly identity properties, matching real profile requirements.
        self.binding=LifeBinding('offline-life','physical:'+self.profile.identity_sha256,self.profile.identity_sha256,v['session_id'])
        self.store=Store(self.directory/'agent.sqlite3')
        self.store.start_task('legacy-run','emulator','offline historical source',{},run_kind='autonomous')
        self.store.conn.execute("UPDATE task_runs SET status='rested' WHERE task_run_id='legacy-run'")
        migrate_subject(self.store,self.binding,source_device='emulator',source_sha256='a'*64)
        attach_subject(self.store,self.binding)
        self.profile_path=self.directory/'profile.json';self.profile_path.write_text(json.dumps(self.profile.wire()),encoding='utf8')
        self.obs=ScreenObservation.from_png(self.adb.png,SERIAL)
        self.semantic=normalize(tree(),self.obs,'com.android.settings/.Settings','coloros16_native_xml/1')
        self.manifest={'schema':'physical-app-capabilities/1','enabled':True,'apps':[{'package':'com.android.settings','version_code':'36',
            'pages':[{'page_sha256':page_key(self.semantic),'back_allowed':True,'controls':[
                {'resource_id':'android:id/title','class_name':'android.widget.LinearLayout','action':'tap'},
                {'resource_id':'com.android.settings:id/recycler_view','class_name':'androidx.recyclerview.widget.RecyclerView','action':'swipe'}]}]}]}
        mp=self.directory/'manifest.json';mp.write_text(json.dumps(self.manifest),encoding='utf8')
        ap=self.directory/'approval.dpapi';ap.write_bytes(b'offline-fake-dpapi-not-a-real-grant')
        v['config'].update(mode='api',serial='profile-bound',device=self.binding.execution_device_id,
            physical_execution={'profile_path':str(self.profile_path),'profile_sha256':__import__('hashlib').sha256(self.profile_path.read_bytes()).hexdigest(),
                'manifest_path':str(mp),'manifest_sha256':__import__('hashlib').sha256(mp.read_bytes()).hexdigest(),
                'approval_path':str(ap),'life_binding':asdict(self.binding),'control_path':str(self.ledger.path)})
        v['fingerprint']=digest(v['config']);self.ledger.save(v);self.ledger.start();self.ledger.update(active_pid=os.getpid())
        self.state=self.ledger.load();self.contract=PhysicalContract(self.state['config'],self.state)
        self.grant={'schema':'physical-execution-approval/1','session_id':v['session_id'],'config_fingerprint':v['fingerprint'],
            'identity_sha256':self.profile.identity_sha256,'manifest_sha256':digest(self.manifest),'life_subject_id':self.binding.life_subject_id,
            'duration_seconds':self.state['duration_seconds'],'execute_gui':True,'expires_at':time.time()+600,'operator_review_reference':'offline review only'}
        self.leases=Store(Path(v['config']['lease_path']));self.leases.acquire('real','offline-owner',120)
        self.leases.acquire('autonomous-api-writer','offline-owner',120)
        self.leases.acquire('subject:'+self.binding.life_subject_id,'offline-owner',120)
        self.lease_adapter=LeaseNamespace(self.leases,self.binding.execution_device_id)
        self.store.start_task('new-run',self.binding.execution_device_id,'offline physical goal',{},run_kind='autonomous')
        self.binding.bind_run(self.store,'new-run')
        self.attempt=self.store.attempt('new-run',1,1,{'execution_status':'not_executed'})
    def mocks(self):
        from contextlib import ExitStack
        stack=ExitStack();stack.enter_context(patch('phonelife.physical_execution.unprotect_approval',side_effect=lambda b:json.dumps(self.grant).encode()))
        stack.enter_context(patch('phonelife.physical_execution.physical_read_check',return_value=dict(ONLINE)))
        return stack
    def close(self):self.leases.close();self.store.close();self.ledger.close()

class SubjectMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'copy.sqlite3';self.s=Store(self.path);enable_life(self.s)
        self.s.start_task('old','emulator','historical',{},run_kind='autonomous');self.s.conn.execute("UPDATE task_runs SET status='uncertain'")
        self.memory=LifeMemory(self.s,'emulator');self.mid=self.memory.add('episodic','old experience',{'verified':False},'old')
        self.s.conn.execute("INSERT INTO life_interests VALUES('emulator','autonomous','android',.7,3,1)")
        from phonelife.life_state import LifeState
        self.state=LifeState().wire();self.s.conn.execute('INSERT INTO life_states VALUES(?,?,?)',('emulator','autonomous',json.dumps(self.state)))
        self.s.set_wake('emulator',time.time()+300,'old rest')
        self.binding=LifeBinding('same-life','physical:offline','offline-profile','offline-session')
        self.old={t:[tuple(r) for r in self.s.conn.execute('SELECT * FROM '+t)] for t in ['task_runs','action_attempts','life_memories','life_interests','life_states','schedule']}
    def tearDown(self):self.s.close();self.temp.cleanup()
    def migrate(self,**kw):return migrate_subject(self.s,self.binding,source_device='emulator',source_sha256='a'*64,**kw)
    def test_all_original_rows_ids_and_statuses_preserved(self):
        self.migrate()
        for t,rows in self.old.items():self.assertTrue(set(rows)<=set(tuple(r) for r in self.s.conn.execute('SELECT * FROM '+t)),t)
    def test_cross_device_reads_same_memory_id(self):
        self.migrate();attach_subject(self.s,self.binding)
        self.assertEqual(LifeMemory(self.s,self.binding.execution_device_id).retrieve()[0]['memory_id'],self.mid)
    def test_interest_weights_and_next_wake_preserved(self):
        self.migrate();attach_subject(self.s,self.binding)
        self.assertEqual(LifeMemory(self.s,self.binding.execution_device_id).interests()[0]['weight'],.7)
        self.assertEqual(self.s.next_wake(self.binding.execution_device_id),self.old['schedule'][0][1])
    def test_life_state_loads_same_subject(self):
        self.migrate();attach_subject(self.s,self.binding)
        self.assertEqual(AutonomousLife(self.s,self.binding.execution_device_id,MockLifeBrain()).load_state().wire(),self.state)
    def test_duplicate_migration_idempotent(self):
        self.migrate();before=list(self.s.conn.iterdump());self.migrate();self.assertEqual(list(self.s.conn.iterdump()),before)
    def test_interrupted_migration_rolls_back_then_recovers(self):
        def fail(_):raise OSError('offline injected migration interruption')
        with self.assertRaises(OSError):self.migrate(fault=fail)
        self.assertFalse(self.s.conn.in_transaction);self.migrate();self.binding.verify(self.s.conn)
    def test_restart_and_separate_process_binding(self):
        self.migrate();self.s.close();self.s=Store(self.path)
        code="from pathlib import Path;from phonelife.storage import Store;from phonelife.life_subject import LifeBinding;import sys;s=Store.open_readonly(Path(sys.argv[1]));LifeBinding('same-life','physical:offline','offline-profile','offline-session').verify(s.conn);s.close()"
        result=subprocess.run([sys.executable,'-c',code,str(self.path)],capture_output=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr.decode())
    def test_different_subject_cannot_reuse_source(self):
        self.migrate()
        with self.assertRaises((ValueError,sqlite3.IntegrityError)):migrate_subject(self.s,replace(self.binding,life_subject_id='other'),source_device='emulator',source_sha256='a'*64)
    def test_source_hash_change_refused(self):
        self.migrate()
        with self.assertRaises(ValueError):migrate_subject(self.s,self.binding,source_device='emulator',source_sha256='b'*64)
    def test_missing_source_never_guessed(self):
        with self.assertRaises(ValueError):migrate_subject(self.s,self.binding,source_device='real',source_sha256='a'*64)
    def test_manual_benchmark_mock_not_imported(self):
        for kind in ['benchmark','manual_targeted','mock_test']:
            self.s.start_task(kind,'emulator','test',{},run_kind=kind);self.s.remember('emulator','test',kind,run_kind=kind)
        self.migrate();attach_subject(self.s,self.binding)
        retrieved=LifeMemory(self.s,self.binding.execution_device_id).retrieve()
        self.assertEqual([m['memory_id'] for m in retrieved],[self.mid])
        self.assertEqual(self.s.conn.execute('SELECT COUNT(*) FROM life_run_bindings').fetchone()[0],1)
    def test_new_session_keeps_previous_session_binding(self):
        self.migrate();new=replace(self.binding,device_session_id='second-session')
        migrate_subject(self.s,new,source_device='emulator',source_sha256='a'*64)
        self.binding.verify(self.s.conn);new.verify(self.s.conn)
    def test_binding_profile_mismatch_refused(self):
        self.migrate()
        with self.assertRaises(ValueError):replace(self.binding,device_profile_id='different').verify(self.s.conn)
    def test_new_actions_keep_physical_origin(self):
        self.migrate();attach_subject(self.s,self.binding)
        self.s.start_task('new',self.binding.execution_device_id,'new',{},run_kind='autonomous')
        m=LifeMemory(self.s,self.binding.execution_device_id);m.add('episodic','new',{},'new')
        self.assertEqual(self.s.conn.execute('SELECT device FROM task_runs WHERE task_run_id=?',('new',)).fetchone()[0],self.binding.execution_device_id)
        self.assertEqual(self.s.conn.execute('SELECT device FROM task_runs WHERE task_run_id=?',('old',)).fetchone()[0],'emulator')
    def test_budget_history_not_reset_on_device_change(self):
        self.s.conn.execute('INSERT INTO life_requests(request_id,device,run_kind,task_run_id,phase,requested_at,status) VALUES(?,?,?,?,?,?,?)',('request','emulator','autonomous','old','brain',time.time(),'failed'))
        self.migrate();attach_subject(self.s,self.binding);b=RequestBudget(self.s,self.binding.execution_device_id,LifeLimits(hourly_requests=1));b.run_id='new'
        self.assertEqual(b.snapshot()['hour_used'],1)
        with self.assertRaises(BudgetExceeded):b.reserve('brain')
    def test_readonly_migration_refused(self):
        r=Store.open_readonly(self.path)
        try:
            with self.assertRaises(ValueError):migrate_subject(r,self.binding,source_device='emulator',source_sha256='a'*64)
        finally:r.close()

class PhysicalExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.f=PhysicalFixture(self.temp.name);self.stack=self.f.mocks();self.stack.__enter__()
        self.phone=PhysicalPhone(self.f.contract,self.f.adb);self.reg=self.phone.capabilities;self.sent=[]
        async def send(args):self.sent.append(args)
        self.phone._send_input=send
        async def collect(phone,obs):return replace(self.f.semantic,observation_id=obs.observation_id,screenshot_sha256=obs.sha256)
        self.stack.enter_context(patch.object(ColorOSSemanticProvider,'collect',side_effect=collect))
    async def asyncTearDown(self):self.stack.close();self.f.close();self.temp.cleanup()
    def candidate(self,kind='tap_control'):
        s=self.f.semantic
        target=next(c for c in s.controls if (c.scrollable if kind=='scroll_region' else c.clickable))
        return {'type':kind,'reason':'offline approved navigation','observation_id':s.observation_id,
            **({'scrollable_region_id':target.control_id,'direction':'toward_end'} if kind=='scroll_region' else {'control_id':target.control_id})}
    def permit(self,kind='tap_control'):
        action,binding=self.reg.ground(self.candidate(kind),self.f.semantic);action=action.bind(self.f.obs)
        self.phone.prepare_dispatch(action,self.f.semantic,binding,self.f.store,self.f.lease_adapter,'new-run','offline-owner',self.f.attempt,15)
        self.f.store.update_attempt(self.f.attempt,execution_status='uncertain_after_transport_error')
        return action
    async def test_legal_mock_tap_once(self):
        await self.phone.act(self.permit());self.assertEqual(self.sent,[('tap','360','400')])
    async def test_legal_mock_swipe_once(self):
        await self.phone.act(self.permit('scroll_region'));self.assertEqual(len(self.sent),1);self.assertEqual(self.sent[0][0],'swipe')
    async def test_single_use_permit_no_replay(self):
        a=self.permit();await self.phone.act(a)
        with self.assertRaises(PolicyDenied):await self.phone.act(a)
        self.assertEqual(len(self.sent),1)
    async def test_direct_unbound_tap_denied(self):
        with self.assertRaises(PolicyDenied):await self.phone.act(Action('tap',x=1,y=2))
        self.assertEqual(self.sent,[])
    async def test_identity_changes_before_dispatch_no_input(self):
        a=self.permit();self.f.adb.props['ro.product.device']='changed'
        with self.assertRaises(PolicyDenied):await self.phone.act(a)
        self.assertEqual(self.sent,[])
    async def test_usb_disconnect_before_dispatch_no_input(self):
        a=self.permit();self.f.adb.online=False
        with self.assertRaises(PolicyDenied):await self.phone.act(a)
        self.assertEqual(self.sent,[])
    async def test_disconnect_during_input_unknown_and_no_resend(self):
        async def fail(args):self.sent.append(args);raise OSError('offline USB failure')
        self.phone._send_input=fail;a=self.permit()
        with self.assertRaises(DeviceTransportError):await self.phone.act(a)
        with self.assertRaises(PolicyDenied):await self.phone.act(a)
        self.assertTrue(self.phone._unknown);self.assertEqual(len(self.sent),1)
    async def test_unknown_input_audit_survives_sqlite_reopen_and_separate_process(self):
        async def fail(args):self.sent.append(args);raise OSError('offline injected unknown')
        self.phone._send_input=fail
        with self.assertRaises(DeviceTransportError):await self.phone.act(self.permit())
        path=Path(self.f.state['config']['agent_db']);self.f.store.close();self.f.store=Store(path)
        self.assertEqual(self.f.store.task('new-run')['attempts'][0]['payload']['execution_status'],'uncertain_after_transport_error')
        code="from pathlib import Path;from phonelife.longrun_state import SessionLedger;import sys,json;s=SessionLedger.read_status(Path(sys.argv[1]));print(json.dumps({'review':s['requires_manual_review'],'allow':s['worker_launch_allowed']}))"
        result=subprocess.run([sys.executable,'-c',code,str(self.f.ledger.path)],capture_output=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr.decode());self.assertEqual(json.loads(result.stdout),{'review':True,'allow':False})
        self.assertEqual(len(self.sent),1)
    async def test_identity_changes_after_input_unknown(self):
        async def change(args):self.sent.append(args);self.f.adb.props['ro.product.device']='changed'
        self.phone._send_input=change
        with self.assertRaises(DeviceTransportError):await self.phone.act(self.permit())
        self.assertEqual(len(self.sent),1)
    async def test_post_input_observation_failure_requires_review(self):
        original=self.phone.observe;calls=0
        async def observe():
            nonlocal calls
            calls+=1
            if calls>1:raise OSError('offline disconnect before visual confirmation')
            return await original()
        self.phone.observe=observe
        with self.assertRaises(DeviceTransportError):await self.phone.act(self.permit())
        self.assertTrue(self.phone._unknown);self.assertEqual(len(self.sent),1)
    async def test_lock_screen_blocks(self):
        a=self.permit();self.f.adb.lock='mShowingLockscreen=true'
        with self.assertRaises(PolicyDenied):await self.phone.act(a)
        self.assertEqual(self.sent,[])
    async def test_permission_overlay_blocks(self):
        a=self.permit();self.f.adb.windows+=' permissioncontroller'
        with self.assertRaises(PolicyDenied):await self.phone.act(a)
        self.assertEqual(self.sent,[])
    async def test_unknown_window_evidence_denied(self):
        a=self.permit();self.f.adb.windows='mCurrentFocus=Window{123 u0 com.android.settings/.Settings}'
        with self.assertRaises(PolicyDenied):await self.phone.act(a)
        self.assertEqual(self.sent,[])
    async def test_rotation_change_denied(self):
        a=self.permit();self.f.adb.rotation=1
        with self.assertRaises(PolicyDenied):await self.phone.act(a)
    async def test_resolution_change_denied(self):
        a=self.permit();self.f.adb.size='Physical size: 1080x2400'
        with self.assertRaises(PolicyDenied):await self.phone.act(a)
    async def test_lease_lost_denied(self):
        a=self.permit();self.f.leases.release('real','offline-owner')
        with self.assertRaises(PolicyDenied):await self.phone.act(a)
    async def test_shared_real_lease_conflict(self):
        self.assertFalse(self.f.leases.acquire('real','another-process',120))
    async def test_action_budget_denied(self):
        self.phone._dispatched=15
        with self.assertRaises(PolicyDenied):self.permit()
    async def test_stale_observation_permit_denied(self):
        a=self.permit()
        with patch('phonelife.physical_execution.time.monotonic',return_value=time.monotonic()+5):
            with self.assertRaises(PolicyDenied):await self.phone.act(a)
    async def test_unknown_control_flag_denied(self):
        raw=tree();raw['windows'][0]['root']['children'][0]['editable']=None
        s=normalize(raw,self.f.obs,'com.android.settings/.Settings','coloros16_native_xml/1')
        with self.assertRaises(PolicyDenied):self.reg.ground(self.candidate(),s)
    async def test_wrong_semantics_provider_denied(self):
        with self.assertRaises(PolicyDenied):self.reg.ground(self.candidate(),replace(self.f.semantic,provider='shell_ui_snapshot/1'))
    async def test_legacy_dynamic_exception_not_used(self):
        with self.assertRaises(PolicyDenied):self.reg.page(replace(self.f.semantic,stability=({'dynamic':'API35'},)))
    async def test_unapproved_page_denied(self):
        with self.assertRaises(PolicyDenied):self.reg.page(replace(self.f.semantic,foreground_activity='.Unknown'))
    async def test_unapproved_app_denied(self):
        with self.assertRaises(PolicyDenied):self.reg.page(replace(self.f.semantic,foreground_package='com.tencent.mobileqq'))
    async def test_external_text_and_launch_denied(self):
        p=PhysicalActionPolicy(self.f.contract)
        for a in [Action('text',text='send'),Action('launch',package='com.tencent.mobileqq'),Action('home')]:
            with self.assertRaises(PolicyDenied):p.check(a,self.f.obs)
    async def test_out_of_bounds_tap_denied(self):
        a=Action('tap',x=99999,y=0).bind(self.f.obs)
        with self.assertRaises(ValueError):PhysicalActionPolicy(self.f.contract).check(a,self.f.obs)
    async def test_approval_expired_denied(self):
        self.f.grant['expires_at']=time.time()-1
        with self.assertRaises(PermissionError):self.f.contract.authorize()
    async def test_approval_other_session_denied(self):
        self.f.grant['session_id']='wrong'
        with self.assertRaises(PermissionError):self.f.contract.authorize()
    async def test_plain_config_cannot_grant_execution(self):
        self.stack.close()
        with self.assertRaises(PermissionError):self.f.contract.authorize()
    async def test_recovery_launch_never_physical(self):
        with self.assertRaises(PolicyDenied):await self.phone.reopen_settings()
        self.assertEqual(self.sent,[])
    async def test_prewrite_audit_required_before_actual_input(self):
        a=self.permit();self.f.store.update_attempt(self.f.attempt,execution_status='not_executed')
        with self.assertRaises(PolicyDenied):await self.phone.act(a)
        self.assertEqual(self.sent,[])
    async def test_subject_writer_lease_required_at_input(self):
        a=self.permit();self.f.leases.release('subject:'+self.f.binding.life_subject_id,'offline-owner')
        with self.assertRaises(PolicyDenied):await self.phone.act(a)
        self.assertEqual(self.sent,[])
    async def test_emergency_during_observation_blocks_input(self):
        a=self.permit()
        async def collect(phone,obs):
            self.f.ledger.operator('emergency_stopped');return self.f.semantic
        with patch.object(ColorOSSemanticProvider,'collect',side_effect=collect):
            with self.assertRaises(PolicyDenied):await self.phone.act(a)
        self.assertEqual(self.sent,[])
    async def test_real_loop_persists_confirmed_tap_and_independent_uncertain_finish(self):
        from phonelife.loop import PhoneLife
        from phonelife.llm import MockPlanner
        from phonelife.models import ModelResponse
        class Gui:
            count=0
            async def propose(gui,goal,obs,previous,feedback=None):
                gui.count+=1
                if gui.count==1:
                    sem=life.semantic_observation;target=next(c for c in sem.controls if c.clickable)
                    value={'type':'tap_control','reason':'offline accepted row','observation_id':sem.observation_id,'control_id':target.control_id}
                else:value={'type':'finish','reason':'requires independent verification','outcome':'achieved'}
                return ModelResponse(json.dumps(value),provider='offline',model='offline')
        async def collect(phone,obs):return normalize(tree(),obs,'com.android.settings/.Settings','coloros16_native_xml/1')
        self.phone.preheld_lease_owner='offline-owner'
        life=PhoneLife(self.f.store,self.f.binding.execution_device_id,self.phone,MockPlanner(),Gui(),PhysicalActionPolicy(self.f.contract),
            action_delay=0,step_limit=2,semantic_provider=ColorOSSemanticProvider(),lease_store=self.f.lease_adapter)
        with patch.object(ColorOSSemanticProvider,'collect',side_effect=collect):
            result=await life.execute_goal('Inspect approved Settings content',owner='offline-owner',run_kind='autonomous')
        task=self.f.store.task(result.task_run_id)
        self.assertEqual(len(self.sent),1);self.assertEqual(result.status,'uncertain')
        self.assertEqual(sum(a['payload'].get('execution_status')=='executed' for a in task['attempts']),1)

class FourEntryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.f=PhysicalFixture(self.temp.name);self.stack=self.f.mocks();self.stack.__enter__()
        for key in ['real','autonomous-api-writer','subject:'+self.f.binding.life_subject_id]:self.f.leases.release(key,'offline-owner')
    async def asyncTearDown(self):self.stack.close();self.f.close();self.temp.cleanup()
    async def test_status_allowed_with_all_evidence_and_no_worker(self):
        s=self.f.ledger.status(worker_alive=False)
        self.assertTrue(s['worker_launch_allowed']);self.assertTrue(s['physical_device']['identity_verified'])
    async def test_status_offline_never_allows_worker(self):
        with patch('phonelife.physical_execution.physical_read_check',return_value={**ONLINE,'online':False}):
            self.assertFalse(self.f.ledger.status(worker_alive=False)['worker_launch_allowed'])
    async def test_resume_uses_same_offline_denial(self):
        from phonelife.longrun import control_session
        with patch('phonelife.physical_execution.physical_read_check',return_value={**ONLINE,'online':False}):
            with self.assertRaises(ValueError):control_session(self.f.directory,'running')
        self.assertEqual(self.f.ledger.load()['operator'],'running')
    async def test_worker_denied_before_model_or_phone_creation(self):
        from phonelife.longrun import run_worker
        self.f.grant['execute_gui']=False
        with patch('phonelife.longrun_api.SoakAPI') as api,patch('phonelife.physical_execution.PhysicalPhone') as phone:
            with self.assertRaises(PermissionError):await run_worker(self.f.directory,os.getpid())
            api.assert_not_called();phone.assert_not_called()
    async def test_worker_budget_exhausted_before_client_creation(self):
        from phonelife.longrun import run_worker
        for i in range(120):
            self.f.store.conn.execute('INSERT INTO life_requests(request_id,device,run_kind,task_run_id,phase,requested_at,status) VALUES(?,?,?,?,?,?,?)',
                (str(i),'emulator','autonomous','legacy-run','brain',time.time(),'failed'))
        with patch('phonelife.longrun_api.SoakAPI') as api:
            with self.assertRaises(PermissionError):await run_worker(self.f.directory,os.getpid())
            api.assert_not_called()
    async def test_supervisor_cannot_start_worker_without_approval(self):
        from phonelife.longrun import LongRunSoakController
        self.f.grant['execute_gui']=False;c=LongRunSoakController(self.f.directory)
        try:
            with patch('phonelife.longrun.subprocess.Popen') as child:
                s=await c.run();self.assertEqual(s['state'],'failed');child.assert_not_called()
        finally:c.close()
    async def test_status_actually_readonly(self):
        before=self.f.ledger.path.read_bytes();dumps=list(self.f.store.conn.iterdump())
        self.f.ledger.status(worker_alive=False)
        self.assertEqual(before,self.f.ledger.path.read_bytes());self.assertEqual(dumps,list(self.f.store.conn.iterdump()))
    async def test_restart_retains_sqlite_only_stop(self):
        marker=self.f.ledger.review_path;original=Path.open
        def fail(p,*a,**kw):
            if p==marker and a and a[0]=='x':raise PermissionError('offline marker denied')
            return original(p,*a,**kw)
        with patch.object(Path,'open',fail):self.f.ledger.latch_review({'sources':[]})
        reopened=SessionLedger(self.f.ledger.path,readonly=True)
        try:
            s=reopened.status(worker_alive=False);self.assertTrue(s['requires_manual_review']);self.assertFalse(s['resume_allowed']);self.assertEqual(s['state'],'failed')
        finally:reopened.close()
    async def test_restart_retains_marker_only_stop(self):
        self.f.ledger.conn.execute('PRAGMA query_only=ON')
        with self.assertRaises(sqlite3.Error):self.f.ledger.latch_review({'sources':[]})
        self.f.ledger.conn.execute('PRAGMA query_only=OFF')
        reopened=SessionLedger(self.f.ledger.path,readonly=True)
        try:self.assertFalse(reopened.status(worker_alive=False)['worker_launch_allowed']);self.assertTrue(reopened.status()['requires_manual_review'])
        finally:reopened.close()
    async def test_double_failure_rechecks_original_unknown_on_restart(self):
        self.f.store.update_attempt(self.f.attempt,execution_status='outcome_unknown')
        self.f.store.conn.execute("UPDATE task_runs SET status='uncertain' WHERE task_run_id='new-run'")
        marker=self.f.ledger.review_path;original=Path.open
        def fail(p,*a,**kw):
            if p==marker and a and a[0]=='x':raise PermissionError('offline marker denied')
            return original(p,*a,**kw)
        self.f.ledger.conn.execute('PRAGMA query_only=ON')
        with patch.object(Path,'open',fail):
            with self.assertRaises(sqlite3.Error):self.f.ledger.latch_review({'sources':[]})
        self.f.ledger.conn.execute('PRAGMA query_only=OFF')
        reopened=SessionLedger(self.f.ledger.path,readonly=True)
        try:
            s=reopened.status(worker_alive=False);self.assertTrue(s['requires_manual_review']);self.assertFalse(s['resume_allowed'])
        finally:reopened.close()
    async def test_emergency_beats_deadline_and_physical_denial(self):
        self.f.ledger.operator('emergency_stopped')
        s=self.f.ledger.status(worker_alive=False);self.assertEqual(s['effective_state'],'emergency_stopped');self.assertFalse(s['worker_launch_allowed'])
    async def test_unknown_dispatch_beats_global_deadline(self):
        self.f.store.update_attempt(self.f.attempt,execution_status='uncertain_after_transport_error')
        self.f.store.conn.execute("UPDATE task_runs SET status='uncertain' WHERE task_run_id='new-run'")
        with patch.object(self.f.ledger.clock,'monotonic',return_value=self.f.state['started_monotonic']+120):
            s=self.f.ledger.status(worker_alive=False);self.assertTrue(s['requires_manual_review']);self.assertFalse(s['worker_launch_allowed'])
    async def test_clearing_review_marker_cannot_clear_sqlite_stop(self):
        self.f.ledger.latch_review({'sources':[]});self.f.ledger.review_path.unlink()
        s=self.f.ledger.status(worker_alive=False);self.assertEqual(s['state'],'failed');self.assertFalse(s['resume_allowed'])
    async def test_wrong_life_subject_denied(self):
        self.f.store.conn.execute("UPDATE life_device_bindings SET life_subject_id='wrong' WHERE execution_device_id=?",(self.f.binding.execution_device_id,))
        self.assertFalse(self.f.ledger.status(worker_alive=False)['worker_launch_allowed'])
    async def test_global_single_budget_writer_lease(self):
        self.f.leases.acquire('autonomous-api-writer','other-session',120)
        s=self.f.ledger.status(worker_alive=False);self.assertFalse(s['worker_launch_allowed']);self.assertEqual(s['admission_reason_code'],'DEVICE_LEASE_BUSY')
    async def test_api_worker_cleanup_releases_all_owned_leases(self):
        from phonelife.longrun import run_worker
        # Admission succeeds but client construction fails. No actual API/ADB adapter.
        with patch.dict(os.environ,{'PHONE_LIFE_API_KEY':'offline','PHONE_LIFE_GUI_API_KEY':'offline'}),patch('phonelife.longrun_api.SoakAPI',side_effect=RuntimeError('offline constructor failure')):
            with self.assertRaises(RuntimeError):await run_worker(self.f.directory,os.getpid())
        self.assertEqual(self.f.leases.conn.execute('SELECT COUNT(*) FROM lease').fetchone()[0],0)
    async def test_unowned_worker_rejected(self):
        from phonelife.longrun import run_worker
        self.f.ledger.update(active_pid=99999999)
        with patch('phonelife.longrun_api.SoakAPI') as api:
            with self.assertRaises(PermissionError):await run_worker(self.f.directory,os.getpid())
            api.assert_not_called()
    async def test_mock_worker_enters_physical_path_without_real_api(self):
        from phonelife.longrun import run_worker
        from phonelife.brain import MockLifeGui
        from phonelife.physical_execution import PhysicalPhone as OriginalPhone
        observed=[]
        class RestBrain(MockLifeBrain):
            async def decide_life(self,context,observation):
                observed.append(context)
                from phonelife.life_models import LifeDecision
                return LifeDecision('rest',[],None,'offline autonomous rest',30)
        async def collect(phone,obs):return normalize(tree(),obs,'com.android.settings/.Settings','coloros16_native_xml/1')
        with patch.dict(os.environ,{'PHONE_LIFE_API_KEY':'offline','PHONE_LIFE_GUI_API_KEY':'offline'}),patch('phonelife.longrun_api.SoakAPI') as api,\
                patch('phonelife.brain.APILifeBrain',side_effect=lambda *a:RestBrain()),patch('phonelife.llm.APIGuiPlanner',side_effect=lambda *a:MockLifeGui()),\
                patch('phonelife.physical_execution.PhysicalPhone',side_effect=lambda c:OriginalPhone(c,self.f.adb)),\
                patch.object(ColorOSSemanticProvider,'collect',side_effect=collect):
            await run_worker(self.f.directory,os.getpid())
        self.assertEqual(len(observed),1);self.assertEqual(observed[0].device['kind'],'real')
        self.assertEqual(self.f.leases.conn.execute('SELECT COUNT(*) FROM lease').fetchone()[0],0)
        self.assertGreater(self.f.store.conn.execute('SELECT COUNT(*) FROM life_run_bindings').fetchone()[0],1)

class NativeContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_nonobject_contract_rejected(self):
        from phonelife.physical_execution import strict_json
        with self.assertRaises(ValueError):strict_json(b'[]')
    async def test_duplicate_contract_key_rejected(self):
        from phonelife.physical_execution import strict_json
        with self.assertRaises(ValueError):strict_json(b'{"enabled":false,"enabled":true}')
    @unittest.skipUnless(os.name=='nt','Windows DPAPI only; never uses an existing credential')
    async def test_dpapi_approval_purpose_roundtrip_synthetic_only(self):
        import ctypes
        from phonelife.physical_execution import unprotect_approval
        class Blob(ctypes.Structure):_fields_=[('size',ctypes.c_uint32),('data',ctypes.POINTER(ctypes.c_ubyte))]
        text=b'offline-approval-codec-test';buf=ctypes.create_string_buffer(text)
        entbuf=ctypes.create_string_buffer(b'Proj.DigitalLife physical execution approval v1')
        src=Blob(len(text),ctypes.cast(buf,ctypes.POINTER(ctypes.c_ubyte)));ent=Blob(len(entbuf.raw)-1,ctypes.cast(entbuf,ctypes.POINTER(ctypes.c_ubyte)));out=Blob()
        self.assertTrue(ctypes.windll.crypt32.CryptProtectData(ctypes.byref(src),None,ctypes.byref(ent),None,None,1,ctypes.byref(out)))
        try:encrypted=ctypes.string_at(out.data,out.size)
        finally:ctypes.windll.kernel32.LocalFree(out.data)
        self.assertEqual(unprotect_approval(encrypted),text)
    async def test_prepare_api_creates_only_unapproved_synthetic_session(self):
        from phonelife.physical_execution import prepare_physical_session
        from phonelife.longrun_state import sha
        with tempfile.TemporaryDirectory() as t:
            f=PhysicalFixture(t);source=Path(t)/'consistent-copy.sqlite3'
            old=Store(source);enable_life(old);old.start_task('history','emulator','offline history',{},run_kind='autonomous')
            old.close()
            try:
                result=prepare_physical_session(Path(t)/'proposed',source_copy=source,source_sha256=sha(source),source_device='emulator',
                    life_subject_id='independent-life',profile_path=f.profile_path,manifest_path=f.directory/'manifest.json',
                    lease_path=Path(f.state['config']['lease_path']),seconds=10)
                self.assertEqual(result['control_state'],'awaiting_approval');self.assertFalse(result['worker_launch_allowed'])
                self.assertEqual(result['admission_reason_code'],'PHYSICAL_OPERATOR_APPROVAL_REQUIRED')
                self.assertEqual(result['elapsed_seconds'],0)
            finally:f.close()
    async def test_prepare_source_mismatch_does_not_create_session(self):
        from phonelife.physical_execution import prepare_physical_session
        with tempfile.TemporaryDirectory() as t:
            f=PhysicalFixture(t)
            try:
                out=Path(t)/'not-created'
                with self.assertRaises(ValueError):prepare_physical_session(out,source_copy=f.profile_path,source_sha256='a'*64,source_device='emulator',
                    life_subject_id='life',profile_path=f.profile_path,manifest_path=f.directory/'manifest.json',lease_path=Path(f.state['config']['lease_path']),seconds=10)
                self.assertFalse(out.exists())
            finally:f.close()
    async def test_native_xml_missing_safety_attrs_stays_unavailable(self):
        from types import SimpleNamespace
        obs=ScreenObservation.from_png(mock_png(),'offline')
        xml='<?xml version="1.0"?><hierarchy rotation="0"><node bounds="[0,0][720,1600]" package="com.android.settings" class="android.widget.LinearLayout"/></hierarchy>'
        class Phone:
            async def hierarchy(self):return xml
            async def observe(self):return obs
            async def foreground(self):return 'com.android.settings/.Settings'
        s=await ColorOSSemanticProvider().collect(Phone(),obs)
        self.assertEqual(s.evidence_quality,'semantic_unavailable');self.assertEqual(s.controls,())
    async def test_native_windows_unknown_shape_denied(self):self.assertFalse(visible_windows_safe('mCurrentFocus=com.android.settings','com.android.settings'))
    async def test_native_foreign_visible_window_denied(self):
        self.assertFalse(visible_windows_safe(SAFE_WINDOWS+'Window #1 Window{x}\n mViewVisibility=0x0 mHasSurface=true ty=2038 package=foreign.app','com.android.settings'))
    async def test_native_observation_no_helper_deployment(self):
        source=Path(__import__('phonelife.physical_execution',fromlist=['x']).__file__).read_text('utf8')
        self.assertNotIn('ShellSnapshotBackend(',source);self.assertNotIn("'push'",source)
    async def test_app_version_change_denies_execution(self):
        from types import SimpleNamespace
        from phonelife.physical_execution import physical_read_check
        with tempfile.TemporaryDirectory() as t:
            f=PhysicalFixture(t)
            try:
                def run(argv,**kw):
                    args=tuple(argv[3:]);raw=b''
                    if args==('get-state',):raw=b'device'
                    elif args[:2]==('shell','getprop'):raw=f.adb.props[args[2]].encode()
                    else:raw={('shell','dumpsys','power'):b'mInteractive=true',('shell','dumpsys','window','policy'):b'mShowingLockscreen=false',
                        ('shell','dumpsys','window','windows'):SAFE_WINDOWS.encode(),('shell','dumpsys','input'):b'SurfaceOrientation: 0',
                        ('shell','pm','list','packages','--show-versioncode'):b'package:com.android.settings versionCode:999'}[args]
                    return SimpleNamespace(returncode=0,stdout=raw)
                with patch('phonelife.physical_execution.subprocess.run',side_effect=run):self.assertFalse(physical_read_check(f.contract)['execution_allowed'])
            finally:f.close()

class DeadlineBoundaryMatrix(unittest.TestCase):
    def scenario(self,start,now,state='running',stop=None,legacy=False):
        with tempfile.TemporaryDirectory() as t:
            path=Path(t)/'session';prepare_session(path,seconds=86400)
            l=SessionLedger(path/'soak-control.sqlite3')
            try:
                with patch.object(l.clock,'monotonic',return_value=start):l.start()
                if legacy:
                    v=l.load();v.pop('deadline_monotonic');l.save(v)
                if stop=='unknown':l.latch_review({'sources':[]})
                if stop=='emergency':l.operator('emergency_stopped')
                with patch.object(l.clock,'monotonic',return_value=now):return l.tick(state),l.remaining(),l.status()
            finally:l.close()
    def test_one_float_before_deadline_does_not_expire(self):
        s=110000.00000399999;v,r,_=self.scenario(s,math.nextafter(s+86400,-math.inf),'sleeping');self.assertEqual(v['state'],'sleeping');self.assertGreater(r,0)
    def test_exact_deadline_running_expires(self):
        s=110000.00000399999;self.assertEqual(self.scenario(s,s+86400)[0]['state'],'completed_duration')
    def test_after_deadline_expires(self):
        s=110000.00000399999;self.assertEqual(self.scenario(s,math.nextafter(s+86400,math.inf))[0]['state'],'completed_duration')
    def test_large_monotonic_deadline(self):
        s=2.0**40+.125;self.assertEqual(self.scenario(s,s+86400)[0]['state'],'completed_duration')
    def test_sleeping_before_deadline_unchanged(self):self.assertEqual(self.scenario(110000,110010,'sleeping')[0]['state'],'sleeping')
    def test_legacy_format_uses_identical_absolute_operands(self):
        s=110000.00000399999;self.assertEqual(self.scenario(s,s+86400,legacy=True)[0]['state'],'completed_duration')
    def test_unknown_stop_not_overridden(self):
        s=110000.00000399999;self.assertEqual(self.scenario(s,s+86400,stop='unknown')[0]['state'],'failed')
    def test_emergency_not_overridden(self):
        s=110000.00000399999;self.assertEqual(self.scenario(s,s+86400,stop='emergency')[0]['state'],'emergency_stopped')
    def test_changed_boot_fails_closed(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'session';prepare_session(p,seconds=86400);l=SessionLedger(p/'soak-control.sqlite3')
            try:
                l.start()
                with patch.object(l.clock,'boot_id',return_value='different-boot'):
                    self.assertEqual(l.tick('running')['reason'],'MONOTONIC_CLOCK_DISCONTINUITY')
            finally:l.close()


class DeadlinePrecisionTests(unittest.TestCase):
    def test_exact_rounded_absolute_deadline(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'session';prepare_session(p,seconds=86400)
            l=SessionLedger(p/'soak-control.sqlite3')
            try:
                with patch.object(l.clock,'monotonic',return_value=110000.00000399999):l.start()
                with patch.object(l.clock,'monotonic',return_value=110000.00000399999+86400):
                    self.assertEqual(l.remaining(),0)
                    self.assertEqual(l.tick('sleeping')['state'],'completed_duration')
            finally:l.close()
