"""offline_fixture: fake clocks, HTTP faults and isolated mock subprocesses only."""
import asyncio
from dataclasses import asdict
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import uuid
from unittest.mock import patch
from phonelife.longrun_state import Clock,SessionLedger,SoakPolicy,SupervisorLock,runtime_manifest
from phonelife.longrun_health import ReadOnlyDeviceProbe,readonly_db_health,resource_sample,resource_violations
from phonelife.longrun import prepare_session,LongRunSoakController,control_session,export_cycles,wait_state,verify_freeze
from phonelife.longrun import run_worker
from phonelife.longrun_api import SoakAPI
from phonelife.storage import Store
from phonelife.life_storage import enable_life
from phonelife.budgets import RequestBudget,MeteredAPI,LifeLimits
from acceptance.verify_public import verify_archive

class FakeClock:
    def __init__(self):self.mono=100.;self.utc=10000.;self.boot='test-boot'
    def monotonic(self):return self.mono
    def wall(self):return self.utc
    def boot_id(self):return self.boot
    def advance(self,n):self.mono+=n;self.utc+=n

class ClockTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'control.db';self.clock=FakeClock()
        self.ledger=SessionLedger(self.path,self.clock)
        self.ledger.create({'source_kind':'offline_fixture'},100)
    def tearDown(self):self.ledger.close();self.temp.cleanup()
    def test_global_deadline_counts_wait_without_model_calls(self):
        self.ledger.start();self.ledger.tick('sleeping',sleep_class='model_rest')
        self.clock.advance(100);v=self.ledger.tick('sleeping',sleep_class='model_rest')
        self.assertEqual(v['state'],'completed_duration');self.assertEqual(v['autonomous_rest_seconds'],100)
    def test_restart_preserves_deadline_and_accounts_unavailable(self):
        self.ledger.start();self.clock.advance(20);self.ledger.tick('running');self.ledger.close()
        self.clock.advance(30);self.ledger=SessionLedger(self.path,self.clock);self.ledger.start()
        self.assertEqual(self.ledger.remaining(),50);self.assertEqual(self.ledger.load()['process_unavailable_seconds'],30)
    def test_resume_after_deadline_never_starts_new_window(self):
        self.ledger.start();self.clock.advance(105);self.ledger.start()
        self.assertEqual(self.ledger.load()['state'],'completed_duration');self.assertEqual(self.ledger.remaining(),0)
    def test_wall_clock_jump_never_shortens_monotonic_acceptance(self):
        self.ledger.start();self.clock.utc+=864000;self.clock.advance(2)
        self.assertEqual(self.ledger.tick('running')['elapsed_seconds'],2)
    def test_boot_change_fails_closed(self):
        self.ledger.start();self.clock.boot='different'
        self.assertEqual(self.ledger.tick('running')['reason'],'MONOTONIC_CLOCK_DISCONTINUITY')
    def test_backwards_monotonic_fails_closed(self):
        self.ledger.start();self.clock.mono-=1
        self.assertEqual(self.ledger.tick('running')['state'],'failed')
    def test_boot_change_after_prepare_requires_new_preflight(self):
        self.clock.boot='new-boot'
        with self.assertRaises(ValueError):self.ledger.start()
    def test_terminal_time_is_not_recounted(self):
        self.ledger.start();self.clock.advance(100);first=self.ledger.tick('running')
        self.clock.advance(40);second=self.ledger.tick('running')
        self.assertEqual(first['elapsed_seconds'],second['elapsed_seconds'])
    def test_time_categories_do_not_double_count_device_secondary(self):
        self.ledger.start();self.clock.advance(10);self.ledger.tick('recovering',device_unavailable=True)
        self.clock.advance(5);v=self.ledger.tick('budget_wait')
        self.assertEqual(sum(v['phase_seconds'].values()),v['elapsed_seconds']);self.assertEqual(v['device_unavailable_seconds'],5)
    def test_emergency_stop_is_sticky_across_restart(self):
        self.ledger.start();self.ledger.operator('emergency_stopped');self.ledger.tick('running')
        with self.assertRaises(ValueError):self.ledger.operator('running')
        self.ledger.close();self.ledger=SessionLedger(self.path,self.clock)
        with self.assertRaises(ValueError):self.ledger.start()
    def test_pause_resume_does_not_reset_clock(self):
        self.ledger.start();self.ledger.operator('paused');self.ledger.tick('paused')
        self.clock.advance(25);self.ledger.operator('running');v=self.ledger.tick('running')
        self.assertEqual(v['phase_seconds']['paused'],25);self.assertEqual(self.ledger.remaining(),75)
    def test_immutable_config_cannot_be_updated_as_heartbeat(self):
        with self.assertRaises(ValueError):self.ledger.update(config={})
    def test_readonly_status_never_creates_missing_database(self):
        missing=Path(self.temp.name)/'absent.db'
        with self.assertRaises(sqlite3.Error):SessionLedger(missing,readonly=True)
        self.assertFalse(missing.exists())
    def test_formal_live_start_needs_exact_explicit_approval(self):
        self.ledger.close();self.path=Path(self.temp.name)/'formal.db';self.ledger=SessionLedger(self.path,self.clock)
        value=self.ledger.create({'source_kind':'live_api_natural_occurrence'},86400)
        with self.assertRaises(PermissionError):self.ledger.start()
        with self.assertRaises(PermissionError):self.ledger.start('wrong')
        self.assertIsNone(self.ledger.load()['started_monotonic'])
        self.ledger.start(value['fingerprint']);self.assertEqual(self.ledger.remaining(),86400)
    def test_source_categories_are_part_of_fingerprint(self):
        a=self.ledger.load()['fingerprint']
        other=SessionLedger(Path(self.temp.name)/'other.db',self.clock)
        try:self.assertNotEqual(a,other.create({'source_kind':'live_api_controlled_injection'},100)['fingerprint'])
        finally:other.close()

class PolicyTests(unittest.TestCase):
    def test_every_existing_api_and_action_cap_is_hard(self):
        for field,value in [('hourly_requests',121),('daily_requests',501),('max_goal_requests',33),('max_actions',16),('task_timeout_seconds',301)]:
            with self.subTest(field=field),self.assertRaises(ValueError):SoakPolicy(**{field:value})
    def test_stricter_actual_configuration_is_preserved(self):
        p=SoakPolicy(hourly_requests=10,daily_requests=20,max_goal_requests=2,max_actions=1,task_timeout_seconds=10)
        self.assertEqual(p.hourly_requests,10);self.assertEqual(p.max_actions,1)
    def test_wal_disk_rss_and_audit_limits_all_stop_without_deletion(self):
        p=SoakPolicy();sample={'total_bytes':0,'audit_bytes':0,'database_bytes':0,'wal_bytes':0,
            'worker_rss_bytes':0,'supervisor_rss_bytes':0,'free_bytes':p.min_free_bytes}
        self.assertEqual(resource_violations(sample,p),[])
        for key,limit in [('total_bytes',p.evidence_bytes),('audit_bytes',p.audit_bytes),('database_bytes',p.database_bytes),
                          ('wal_bytes',p.wal_bytes),('worker_rss_bytes',p.rss_bytes),('supervisor_rss_bytes',p.rss_bytes)]:
            self.assertTrue(resource_violations({**sample,key:limit+1},p))
        self.assertIn('DISK_FREE_BELOW_RESERVE',resource_violations({**sample,'free_bytes':0},p))
    def test_budget_wait_and_failure_backoff_are_separate_from_model_rest(self):
        self.assertEqual(wait_state({'next_wake_at':200,'last_task':{'status':'failed','result':{'reason_code':'API_TIMEOUT'}}},100)[0],'fault_backoff')
        self.assertEqual(wait_state({'next_wake_at':0,'budget_until':300},100)[0],'budget_wait')
        self.assertEqual(wait_state({'next_wake_at':200,'last_task':{'status':'rested'},'model_kind':'rest'},100),('sleeping','model_rest'))
        self.assertEqual(wait_state({'next_wake_at':200,'last_task':{'status':'rested'},'guard_reason':'LOW_ENERGY'},100),('sleeping','forced_rest'))
    def test_session_supervisor_lock_prevents_duplicate_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            with SupervisorLock(Path(directory)/'owner.lock'):
                with self.assertRaises(RuntimeError):
                    with SupervisorLock(Path(directory)/'owner.lock'):pass
            with SupervisorLock(Path(directory)/'owner.lock'):pass

class HealthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.store=Store(self.root/'agent.db');enable_life(self.store);self.leases=Store(self.root/'lease.db')
    async def asyncTearDown(self):self.store.close();self.leases.close();self.temp.cleanup()
    def health(self,**kw):return readonly_db_health(self.root/'agent.db',self.root/'lease.db',**kw)
    async def test_health_probe_only_sends_fixed_readonly_commands(self):
        seen=[]
        async def run(args):seen.append(args);return {('get-state',):'device',('shell','getprop','ro.kernel.qemu'):'1',('shell','getprop','ro.build.version.sdk'):'35'}[args]
        result=await ReadOnlyDeviceProbe('emulator-test',run).sample()
        self.assertTrue(result['qemu']);self.assertEqual(seen,list(ReadOnlyDeviceProbe.ALLOWED))
    async def test_adb_disconnect_and_offline_are_structured_no_reconnect(self):
        async def disconnected(args):raise TimeoutError()
        self.assertEqual((await ReadOnlyDeviceProbe('emulator-test',disconnected).sample())['reason'],'ADB_UNAVAILABLE')
        async def offline(args):return 'offline'
        self.assertFalse((await ReadOnlyDeviceProbe('emulator-test',offline).sample())['online'])
    async def test_non_qemu_identity_is_never_accepted(self):
        async def physical(args):return 'device' if args==('get-state',) else '0'
        self.assertFalse((await ReadOnlyDeviceProbe('serial',physical).sample())['qemu'])
    async def test_health_reads_wal_without_modifying_content(self):
        before='\n'.join(self.store.conn.iterdump());result=self.health()
        self.assertTrue(result['database_readable']);self.assertEqual(before,'\n'.join(self.store.conn.iterdump()))
    async def test_unknown_finished_dispatch_requires_review(self):
        run=str(uuid.uuid4());self.store.start_task(run,'emulator','test',{})
        self.store.attempt(run,1,1,{'task_run_id':run,'execution_status':'uncertain_after_transport_error'})
        self.store.conn.execute("UPDATE task_runs SET status='needs_review' WHERE task_run_id=?",(run,))
        self.assertTrue(self.health()['needs_review'])
    async def test_inflight_unknown_is_not_mistaken_for_completed_unknown(self):
        run=str(uuid.uuid4());self.store.start_task(run,'emulator','test',{})
        self.store.attempt(run,1,1,{'task_run_id':run,'execution_status':'uncertain_after_transport_error'})
        self.assertFalse(self.health()['needs_review'])
    async def test_quota_window_waits_until_request_expiry(self):
        for i in range(2):self.store.conn.execute('INSERT INTO life_requests(request_id,device,run_kind,task_run_id,phase,requested_at,status) VALUES(?,?,?,?,?,?,?)',
            (str(uuid.uuid4()),'emulator','autonomous','test','brain',990+i,'failed_or_cancelled'))
        h=self.health(now=1000,policy=SoakPolicy(hourly_requests=2))
        self.assertEqual(h['budget_until'],4591)
    async def test_private_and_mock_sources_do_not_share_wake_schedule(self):
        self.store.set_wake('emulator',123,run_kind='autonomous',result='rest')
        self.store.set_wake('emulator',456,run_kind='mock_test',result='rest')
        self.assertEqual(self.health()['next_wake_at'],123)
        self.assertEqual(self.health(run_kind='mock_test')['next_wake_at'],456)
    async def test_sqlite_read_failure_does_not_create_or_repair_database(self):
        missing=self.root/'missing.db';result=readonly_db_health(missing,self.root/'lease.db')
        self.assertFalse(result['database_readable']);self.assertFalse(missing.exists())

class APIUsageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(Path(self.temp.name)/'state.db');enable_life(self.store)
        self.run=str(uuid.uuid4());self.store.start_task(self.run,'emulator','offline',{})
        self.budget=RequestBudget(self.store,'emulator',LifeLimits());self.budget.run_id=self.run
        self.api=SoakAPI('https://api.deepseek.com','offline-secret',store=self.store,budget=lambda:self.budget,source_kind='offline_fixture')
        self.metered=MeteredAPI(self.api,self.budget,'brain')
    async def asyncTearDown(self):self.store.close();self.temp.cleanup()
    def events(self):return [json.loads(r[0]) for r in self.store.conn.execute("SELECT payload FROM events WHERE event_type='longrun_api_exchange'")]
    async def test_provider_token_usage_is_recorded_and_cost_not_fabricated(self):
        reply={'id':str(uuid.uuid4()),'choices':[{'message':{'content':'{}'},'finish_reason':'stop'}],
            'usage':{'prompt_tokens':15,'completion_tokens':5,'total_tokens':20,'private_extra':'must-not-copy'}}
        with patch('urllib.request.urlopen',return_value=io.BytesIO(json.dumps(reply).encode())):
            await self.metered.generate('offline','private persona',json.dumps({'life_context':{}}))
        e=self.events()[0];self.assertEqual(e['provider_usage']['total_tokens'],20)
        self.assertIsNone(e['estimated_cost']);self.assertNotIn('offline-secret',json.dumps(e));self.assertNotIn('private_extra',json.dumps(e))
    async def test_429_is_counted_once_and_never_retried(self):
        with patch('urllib.request.urlopen',side_effect=urllib.error.HTTPError('offline',429,'x',{},None)) as request:
            with self.assertRaises(Exception):await self.metered.generate('offline','system','{}')
        self.assertEqual(request.call_count,1);self.assertEqual(self.events()[0]['error_code'],'API_RATE_LIMIT')
        self.assertEqual(self.budget.snapshot()['goal_used'],1)
    async def test_5xx_is_counted_once_and_never_retried(self):
        with patch('urllib.request.urlopen',side_effect=urllib.error.HTTPError('offline',503,'x',{},None)) as request:
            with self.assertRaises(Exception):await self.metered.generate('offline','system','{}')
        self.assertEqual(request.call_count,1);self.assertEqual(self.events()[0]['error_code'],'API_SERVER_ERROR')
    async def test_timeout_keeps_failed_request_and_no_fake_response(self):
        with patch('urllib.request.urlopen',side_effect=TimeoutError):
            with self.assertRaises(Exception):await self.metered.generate('offline','system','{}')
        self.assertEqual(self.events()[0]['status'],'failed_or_cancelled');self.assertNotIn('response_sha256',self.events()[0])
    async def test_sqlite_audit_write_failure_never_retries_provider(self):
        reply={'choices':[{'message':{'content':'{}'},'finish_reason':'stop'}]}
        with patch('urllib.request.urlopen',return_value=io.BytesIO(json.dumps(reply).encode())) as request,patch.object(self.store,'event',side_effect=sqlite3.OperationalError('offline disk error')):
            with self.assertRaises(sqlite3.Error):await self.metered.generate('offline','system','{}')
        self.assertEqual(request.call_count,1);self.assertEqual(self.budget.snapshot()['goal_used'],1)

class SupervisorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
    async def asyncTearDown(self):self.temp.cleanup()
    def prepare(self,name='session',seconds=3,**kw):
        path=self.base/name;prepare_session(path,seconds=seconds,policy=SoakPolicy(min_free_bytes=1024),**kw);return path
    async def test_bounded_mock_process_stops_and_export_is_idempotent(self):
        path=self.prepare();controller=LongRunSoakController(path)
        try:
            result=await controller.run();self.assertEqual(result['state'],'completed_duration');self.assertGreaterEqual(result['elapsed_seconds'],3)
            export_cycles(path,controller.ledger);before=(path/'cycles.jsonl').read_bytes();export_cycles(path,controller.ledger)
            self.assertEqual(before,(path/'cycles.jsonl').read_bytes())
            ids=[json.loads(s)['task_run_id'] for s in before.decode().splitlines()];self.assertEqual(len(ids),len(set(ids)))
        finally:controller.close()
    async def test_pending_crash_cycle_is_exported_then_finalized_without_duplicates(self):
        path=self.prepare();c=LongRunSoakController(path);db=Store(path/'agent.sqlite3');run=str(uuid.uuid4())
        try:
            db.start_task(run,'mock','pending offline crash fixture',{},run_kind='mock_test')
            export_cycles(path,c.ledger)
            rows=[json.loads(x) for x in (path/'cycles.jsonl').read_text().splitlines()]
            self.assertEqual(len(rows),1);self.assertTrue(rows[0]['pending_interruption_review'])
            db.finish_task(run,{'status':'interrupted','reason_code':'PROCESS_INTERRUPTED','task_run_id':run,'steps':0},0,'Offline interruption')
            export_cycles(path,c.ledger);rows=[json.loads(x) for x in (path/'cycles.jsonl').read_text().splitlines()]
            self.assertEqual(len(rows),1);self.assertEqual(rows[0]['status'],'interrupted')
            self.assertFalse(rows[0]['pending_interruption_review'])
        finally:db.close();c.close()
    async def test_runtime_change_prevents_any_worker_start(self):
        path=self.prepare();c=LongRunSoakController(path)
        try:
            with patch('phonelife.longrun.runtime_manifest',return_value={'changed':'x'}):
                with self.assertRaises(ValueError):await c.run()
            self.assertIsNone(c.worker)
        finally:c.close()
    async def test_worker_cannot_bypass_unstarted_approval_session(self):
        path=self.prepare()
        with patch('phonelife.devices.AdbPhone',side_effect=AssertionError('No device before start')):
            with self.assertRaises(PermissionError):await run_worker(path,os.getpid())
    async def test_low_disk_stops_without_deleting_inputs(self):
        path=self.prepare();marker=path/'retain.bin';marker.write_bytes(b'keep');c=LongRunSoakController(path)
        original=resource_sample(path,path/'agent.sqlite3');original['free_bytes']=0
        try:
            with patch('phonelife.longrun.resource_sample',return_value=original):result=await c.run()
            self.assertEqual(result['state'],'failed');self.assertIsNone(c.worker);self.assertEqual(marker.read_bytes(),b'keep')
        finally:c.close()
    async def test_wal_threshold_stops_without_truncation(self):
        path=self.prepare();c=LongRunSoakController(path);sample=resource_sample(path,path/'agent.sqlite3');sample['wal_bytes']=c.policy.wal_bytes+1
        try:
            with patch('phonelife.longrun.resource_sample',return_value=sample):result=await c.run()
            self.assertEqual(result['reason'],'RESOURCE_LIMIT');self.assertIsNone(c.worker)
        finally:c.close()
    async def test_corrupt_database_stops_and_still_saves_summary(self):
        path=self.prepare(seconds=5);(path/'agent.sqlite3').write_bytes(b'controlled broken SQLite fixture')
        c=LongRunSoakController(path)
        try:
            result=await c.run();self.assertEqual(result['state'],'failed')
            self.assertTrue((path/'summary.json').exists());self.assertIsNone(c.worker)
        finally:c.close()
    async def test_device_lease_competitor_prevents_worker_launch(self):
        path=self.prepare();c=LongRunSoakController(path);leases=Store(Path(c.config['lease_path']))
        leases.acquire('mock','competitor',30)
        try:
            result=await c.run();self.assertEqual(result['state'],'completed_duration');self.assertIsNone(c.worker)
            self.assertEqual(leases.conn.execute('SELECT owner FROM lease').fetchone()[0],'competitor')
        finally:leases.close();c.close()
    async def test_pause_resume_emergency_stop_never_self_restarts(self):
        path=self.prepare(seconds=15);c=LongRunSoakController(path)
        async def operator():
            await asyncio.sleep(1);control_session(path,'paused');await asyncio.sleep(1)
            control_session(path,'running');await asyncio.sleep(1);control_session(path,'emergency_stopped')
        control=asyncio.create_task(operator())
        try:
            result=await c.run();await control;self.assertEqual(result['state'],'emergency_stopped')
            with self.assertRaises(ValueError):control_session(path,'running')
        finally:c.close()
    async def test_source_kind_injection_cannot_be_claimed_natural(self):
        with self.assertRaises(ValueError):self.prepare(source_kind='live_api_controlled_injection')
    async def test_config_tampering_is_detected_before_worker(self):
        path=self.prepare();c=LongRunSoakController(path);v=c.ledger.load();v['config']['policy']['max_actions']=1;c.ledger.save(v)
        try:
            # Reload so both the supplied config and durable fingerprint are compared.
            c.config=c.ledger.load()['config']
            with self.assertRaises(ValueError):await c.run()
            self.assertIsNone(c.worker)
        finally:c.close()
    async def test_control_db_write_error_still_stops_owned_child(self):
        path=self.prepare();c=LongRunSoakController(path)
        class Child:
            pid=123456789;terminated=False
            def poll(self):return 0 if self.terminated else None
            def terminate(self):self.terminated=True
            def wait(self):return 0
        child=Child();c.worker=child
        try:
            with patch.object(c.ledger,'update',side_effect=sqlite3.OperationalError('offline full disk')):
                await c._stop_owned_worker()
            self.assertTrue(child.terminated)
        finally:c.close()
    async def test_mid_session_config_change_cancels_without_new_window(self):
        path=self.prepare(seconds=8);c=LongRunSoakController(path)
        async def tamper():
            await asyncio.sleep(1);other=SessionLedger(path/'soak-control.sqlite3')
            try:
                v=other.load();v['config']['brain_model']='changed-after-freeze';other.save(v)
            finally:other.close()
        change=asyncio.create_task(tamper())
        try:
            with self.assertRaises(ValueError):await c.run()
            await change;self.assertIsNotNone(c.ledger.load()['started_monotonic'])
            if c.worker:self.assertIsNotNone(c.worker.poll())
        finally:c.close()
    async def test_killed_process_unknown_dispatch_is_reviewed_never_replayed(self):
        path=self.prepare(seconds=5);c=LongRunSoakController(path);run=str(uuid.uuid4());ready=path/'crash-ready'
        script='''import sys,time
from pathlib import Path
from phonelife.storage import Store
from phonelife.life_storage import enable_life
s=Store(Path(sys.argv[1]));enable_life(s);leases=Store(Path(sys.argv[2]));run=sys.argv[3]
leases.acquire('mock',run,1)
s.start_task(run,'mock','controlled crash fixture',{},run_kind='mock_test')
s.attempt(run,1,1,{'task_run_id':run,'candidate_action':{'type':'tap'},'execution_status':'uncertain_after_transport_error'})
Path(sys.argv[4]).write_text('ready')
time.sleep(60)
'''
        child=subprocess.Popen([sys.executable,'-c',script,str(path/'agent.sqlite3'),c.config['lease_path'],run,str(ready)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            for _ in range(100):
                if ready.exists():break
                await asyncio.sleep(.05)
            self.assertTrue(ready.exists());child.kill();await asyncio.to_thread(child.communicate);await asyncio.sleep(1.1)
            result=await c.run();self.assertEqual(result['state'],'failed')
            self.assertEqual(result['reason'],'UNKNOWN_DISPATCH_NEEDS_REVIEW')
            db=Store.open_readonly(path/'agent.sqlite3')
            try:
                self.assertEqual(db.task(run)['status'],'needs_review')
                self.assertEqual(db.conn.execute('SELECT COUNT(*) FROM action_attempts').fetchone()[0],1)
                review=db.conn.execute('SELECT review FROM life_recovery WHERE task_run_id=?',(run,)).fetchone()
                self.assertEqual(json.loads(review[0])['old_actions_replayed'],0)
                self.assertEqual(db.conn.execute('SELECT COUNT(*) FROM life_requests').fetchone()[0],0)
            finally:db.close()
        finally:
            if child.poll()is None:child.kill();await asyncio.to_thread(child.communicate)
            c.close()

if __name__=='__main__':unittest.main()
