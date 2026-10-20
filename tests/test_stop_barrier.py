"""offline_fixture: deterministic supervisor safety-stop regressions; no API/ADB."""
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch,Mock
from phonelife.longrun import LongRunSoakController,prepare_session,control_session,export_cycles,run_worker,main
from phonelife.longrun_state import SessionLedger,SoakPolicy
from phonelife.storage import Store
from phonelife.longrun_health import readonly_db_health

class Clock:
    def __init__(self):self.now=100.
    def monotonic(self):return self.now
    def wall(self):return 10000.+self.now
    def boot_id(self):return 'offline-fixed-boot'

class StopBoundaryReproduction(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'session'
        prepare_session(self.path,seconds=4,policy=SoakPolicy(min_free_bytes=1))
        self.clock=Clock();self.c=LongRunSoakController(self.path,self.clock)
        v=self.c.ledger.load();v['boot_id']=self.clock.boot_id();v['last_monotonic']=100.;self.c.ledger.save(v)
    async def asyncTearDown(self):self.c.close();self.temp.cleanup()
    async def fast_sleep(self,seconds):self.clock.now+=max(1.,seconds)
    def health(self,review=False):
        return {'database_readable':True,'needs_review':review,'unknown_dispatches':int(review),
            'operator':'running','warnings':[],'next_wake_at':0,'lease':[],
            'review_sources':[{'task_run_id':'fixture-task','attempt_id':'fixture-attempt',
                'execution_status':'uncertain_after_transport_error','original_task_status':'uncertain'}] if review else []}
    async def test_unknown_marker_cleared_must_not_start_worker_or_complete(self):
        calls=0
        def health(*a,**kw):
            nonlocal calls
            calls+=1;return self.health(calls==1)
        child=Mock(pid=12345);child.poll.return_value=0
        with patch('phonelife.longrun.readonly_db_health',side_effect=health),patch('phonelife.longrun.subprocess.Popen',return_value=child) as launch,patch('phonelife.longrun.asyncio.sleep',side_effect=self.fast_sleep):
            result=await self.c.run()
        self.assertEqual(result['state'],'failed')
        self.assertEqual(result['reason'],'UNKNOWN_DISPATCH_NEEDS_REVIEW')
        launch.assert_not_called()
    async def test_failure_at_deadline_must_not_be_completed_duration(self):
        self.c.ledger.start();self.clock.now+=4
        value=self.c.ledger.tick('failed',reason='UNKNOWN_DISPATCH_NEEDS_REVIEW')
        self.assertEqual(value['state'],'failed')
    def seed(self,status='uncertain',execution='uncertain_after_transport_error'):
        s=Store(self.path/'agent.sqlite3');run='offline-unknown-task'
        try:
            s.start_task(run,'mock','offline stop fixture',{},run_kind='mock_test')
            attempt=s.attempt(run,1,1,{'task_run_id':run,'candidate_action':{'type':'tap'},'execution_status':execution})
            s.conn.execute('UPDATE task_runs SET status=? WHERE task_run_id=?',(status,run))
        finally:s.close()
        return run,attempt
    async def trigger(self):
        with patch('phonelife.longrun.subprocess.Popen') as launch:
            value=await self.c.run();launch.assert_not_called()
        return value
    async def test_unknown_stop_is_durable_and_has_typed_audit(self):
        run,attempt=self.seed();v=await self.trigger()
        self.assertEqual(v['state'],'failed');self.assertTrue(v['requires_manual_review'])
        a=json.loads((self.path/'review-stop-result.json').read_text())
        self.assertEqual(a['sources'][0]['task_run_id'],run);self.assertEqual(a['sources'][0]['attempt_id'],attempt)
        self.assertEqual(a['sources'][0]['execution_status'],'uncertain_after_transport_error')
        self.assertTrue(a['worker_stopped']);self.assertFalse(a['new_actions_dispatched'])
        self.assertEqual(a['old_actions_replayed'],0);self.assertIn('lease_after_stop',a)
    async def test_clearing_agent_marker_cannot_resume_stopped_session(self):
        self.seed();await self.trigger();s=Store(self.path/'agent.sqlite3')
        s.conn.execute("UPDATE task_runs SET status='uncertain'");s.conn.execute("UPDATE action_attempts SET payload='{}'");s.close()
        with self.assertRaisesRegex(ValueError,'SESSION_TERMINATED'):control_session(self.path,'running')
        self.assertEqual((await self.trigger())['state'],'failed')
    async def test_restart_preserves_stop_and_status_is_readonly(self):
        self.seed();await self.trigger();before=(self.path/'agent.sqlite3').read_bytes()
        other=LongRunSoakController(self.path,self.clock)
        try:
            with patch('phonelife.longrun.subprocess.Popen') as launch:v=await other.run()
            launch.assert_not_called();self.assertFalse(v['resume_allowed']);self.assertTrue(v['session_terminated'])
            with self.assertRaises(ValueError):other.ledger.start()
            read=SessionLedger(self.path/'soak-control.sqlite3',readonly=True)
            try:self.assertTrue(read.status()['safety_terminated'])
            finally:read.close()
        finally:other.close()
        self.assertEqual(before,(self.path/'agent.sqlite3').read_bytes())
    async def test_force_cli_cannot_bypass_review_barrier(self):
        self.seed();await self.trigger()
        with self.assertRaises(SystemExit) as error:main(['run','--session',str(self.path),'--force'])
        self.assertEqual(error.exception.code,2)
    async def test_direct_worker_cannot_bypass_existing_stop(self):
        self.c.ledger.start();self.seed();await self.trigger()
        with patch('phonelife.devices.AdbPhone',side_effect=AssertionError('no adapter')),patch('phonelife.longrun_api.SoakAPI',side_effect=AssertionError('no API')):
            await run_worker(self.path,os.getpid())
    async def test_unknown_at_expired_restart_does_not_complete(self):
        self.c.ledger.start();self.clock.now+=4;self.seed(status='running')
        value=await self.trigger();self.assertEqual(value['state'],'failed')
        self.assertEqual(value['reason'],'UNKNOWN_DISPATCH_NEEDS_REVIEW')
        s=Store.open_readonly(self.path/'agent.sqlite3')
        try:
            self.assertEqual(s.task('offline-unknown-task')['status'],'needs_review')
            self.assertEqual(s.conn.execute('SELECT COUNT(*) FROM action_attempts').fetchone()[0],1)
        finally:s.close()
    async def test_unknown_and_emergency_preserve_both_with_emergency_priority(self):
        self.seed();self.c.ledger.operator('emergency_stopped');self.clock.now+=4
        v=await self.trigger();self.assertEqual(v['state'],'emergency_stopped')
        self.assertTrue(v['requires_manual_review']);self.assertEqual(v['safety_stop']['reason_code'],'UNKNOWN_DISPATCH_NEEDS_REVIEW')
        with self.assertRaises(ValueError):self.c.ledger.operator('running')
    async def test_emergency_wins_normal_expiry(self):
        self.c.ledger.start();self.clock.now+=4;self.c.ledger.operator('emergency_stopped')
        self.assertEqual(self.c.ledger.tick('completed_duration')['state'],'emergency_stopped')
    async def test_unknown_overrides_pause_and_resume(self):
        self.seed();self.c.ledger.operator('paused');v=await self.trigger()
        self.assertEqual(v['state'],'failed')
        with self.assertRaises(ValueError):self.c.ledger.operator('running')
    async def test_resolved_device_offline_cannot_clear_review(self):
        self.seed();await self.trigger()
        self.c.probe=Mock();self.c.probe.sample=Mock(side_effect=AssertionError('terminal session never probes'))
        self.assertEqual((await self.trigger())['state'],'failed')
    async def test_legacy_terminal_db_stop_also_rejects_resume(self):
        self.c.ledger.start();self.c.ledger.tick('failed',reason='UNKNOWN_DISPATCH_NEEDS_REVIEW')
        with self.assertRaises(ValueError):self.c.ledger.operator('running')
    async def test_control_db_write_failure_leaves_independent_stop(self):
        self.seed()
        with patch.object(self.c.ledger,'save',side_effect=sqlite3.OperationalError('offline disk full')):
            with self.assertRaises(sqlite3.OperationalError):await self.c.run()
        self.assertTrue(self.c.ledger.review_path.exists())
        self.assertEqual(self.c.ledger.load()['state'],'failed')
        with self.assertRaises(ValueError):self.c.ledger.start()
    async def test_append_once_barrier_ignores_db_state_reclassification(self):
        self.seed();await self.trigger();marker=self.c.ledger.review_path.read_bytes()
        raw=json.loads(self.c.ledger.conn.execute('SELECT payload FROM session').fetchone()[0]);raw.update(state='running',operator='running',reason='external-clear')
        self.c.ledger.conn.execute('UPDATE session SET payload=?',(json.dumps(raw),))
        self.assertEqual((await self.trigger())['state'],'failed');self.assertEqual(marker,self.c.ledger.review_path.read_bytes())
    async def test_normal_86400_elapsed_still_completes_without_fault(self):
        v=self.c.ledger.load();v['duration_seconds']=86400;self.c.ledger.save(v)
        self.c.ledger.start();self.clock.now+=86400
        self.assertEqual(self.c.ledger.tick('sleeping',sleep_class='model_rest')['state'],'completed_duration')
    async def test_exports_and_status_never_rewrite_original_attempt(self):
        self.seed();await self.trigger();s=Store.open_readonly(self.path/'agent.sqlite3')
        try:before='\n'.join(s.conn.iterdump())
        finally:s.close()
        export_cycles(self.path,self.c.ledger);self.c.ledger.status();export_cycles(self.path,self.c.ledger)
        s=Store.open_readonly(self.path/'agent.sqlite3')
        try:self.assertEqual(before,'\n'.join(s.conn.iterdump()))
        finally:s.close()
    async def test_new_linked_session_preserves_old_stop_and_requires_reference(self):
        self.seed();await self.trigger();marker=self.c.ledger.review_path.read_bytes()
        with self.assertRaises(ValueError):prepare_session(self.path.parent/'bad',reviewed_session=self.path)
        new=prepare_session(self.path.parent/'new',reviewed_session=self.path,review_reference='offline_fixture/manual-review-001')
        self.assertNotEqual(new['session_id'],self.c.ledger.load()['session_id'])
        self.assertEqual(new['config']['prior_reviewed_fault']['session_id'],self.c.ledger.load()['session_id'])
        self.assertEqual(marker,self.c.ledger.review_path.read_bytes())
    async def test_live_predispatch_journal_is_not_a_confirmed_transport_failure(self):
        self.seed(status='running');leases=Store(Path(self.c.config['lease_path']));leases.acquire('mock','live-owner',30)
        try:
            h=readonly_db_health(self.path/'agent.sqlite3',self.c.config['lease_path'],'mock',worker_alive=True)
            self.assertFalse(h['safety_review_required'])
        finally:leases.release('mock','live-owner');leases.close()
    async def test_owned_worker_crash_latches_unknown_even_with_live_lease(self):
        self.seed(status='running');leases=Store(Path(self.c.config['lease_path']));leases.acquire('mock','crashed-owner',30)
        try:
            self.c.worker=Mock(pid=123456);self.c.worker.poll.return_value=-9
            v=await self.trigger();self.assertEqual(v['state'],'failed')
            self.assertEqual(leases.conn.execute('SELECT owner FROM lease').fetchone()[0],'crashed-owner')
        finally:leases.release('mock','crashed-owner');leases.close()
    async def test_graceful_owned_child_cancels_and_releases_lease(self):
        ready=self.path/'ready';script='''import sys,time
from pathlib import Path
from phonelife.longrun_state import SessionLedger
from phonelife.storage import Store
p=Path(sys.argv[1]);l=SessionLedger(p/'soak-control.sqlite3',readonly=True);s=Store(Path(l.load()['config']['lease_path']))
s.acquire('mock','owned-stop-fixture',30)
try:
 (p/'ready').write_text('ready')
 while l.load()['worker_signal']!='stop':time.sleep(.02)
finally:s.release('mock','owned-stop-fixture');s.close();l.close()
'''
        child=subprocess.Popen([sys.executable,'-c',script,str(self.path)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            until=time.monotonic()+8
            while not ready.exists() and time.monotonic()<until:await asyncio.sleep(.05)
            self.assertTrue(ready.exists());self.c.worker=child;self.seed()
            v=await self.trigger();self.assertEqual(v['state'],'failed');self.assertIsNotNone(child.poll())
            s=Store.open_readonly(Path(self.c.config['lease_path']))
            try:self.assertEqual(s.conn.execute('SELECT COUNT(*) FROM lease').fetchone()[0],0)
            finally:s.close()
        finally:
            if child.poll()is None:child.kill();child.wait(timeout=5)
    async def test_unresponsive_owned_worker_has_bounded_kill(self):
        class Child:
            pid=123456;code=None
            def poll(self):return self.code
            def kill(self):self.code=-9
        child=Child();self.c.worker=child;tick=0
        def mono():
            nonlocal tick
            tick+=70;return tick
        with patch('phonelife.longrun.time.monotonic',side_effect=mono):result=await self.c._stop_owned_worker()
        self.assertTrue(result['worker_stopped']);self.assertEqual(result['worker_stop_result'],'killed')
    async def test_expiry_health_race_still_checks_unknown_after_cancel(self):
        calls=0
        def health(*a,**kw):
            nonlocal calls
            calls+=1
            if calls==2:self.clock.now+=4
            return self.health(calls>=3)
        with patch('phonelife.longrun.readonly_db_health',side_effect=health),patch('phonelife.longrun.subprocess.Popen') as launch:
            value=await self.c.run()
        launch.assert_not_called();self.assertEqual(value['state'],'failed')
    async def test_controller_heartbeat_cannot_bypass_final_health_audit(self):
        self.c.ledger.start();self.clock.now+=4
        self.assertEqual(self.c._tick('running',reason='MODEL_CYCLE')['state'],'running')
        self.assertEqual(self.c._tick('failed',reason='DATABASE_UNREADABLE')['state'],'failed')
    async def test_repeated_review_cannot_replace_original_trigger(self):
        self.seed();await self.trigger();before=self.c.ledger.review_path.read_bytes()
        self.c.ledger.latch_review({'sources':[],'worker_pid':999})
        self.assertEqual(before,self.c.ledger.review_path.read_bytes())
    async def test_unreadable_barrier_fails_closed_without_repair(self):
        self.c.ledger.review_path.write_bytes(b'{partial')
        before=self.c.ledger.review_path.read_bytes();v=self.c.ledger.status()
        self.assertEqual(v['state'],'failed');self.assertFalse(v['resume_allowed'])
        with self.assertRaises(ValueError):self.c.ledger.start()
        self.assertEqual(before,self.c.ledger.review_path.read_bytes())
    async def test_resume_cannot_race_supervisor_before_latch(self):
        self.seed()
        with self.assertRaisesRegex(ValueError,'SESSION_TERMINATED'):control_session(self.path,'running')
        self.assertEqual(self.c.ledger.load()['state'],'failed')
    async def test_historical_pending_under_live_lease_requires_review_before_deadline(self):
        self.seed(status='running');leases=Store(Path(self.c.config['lease_path']));leases.acquire('mock','previous-worker',30)
        try:
            self.c.ledger.start();self.clock.now+=4
            h=self.c._health();self.assertTrue(h['safety_review_required'])
            value=await self.trigger();self.assertEqual(value['state'],'failed')
            self.assertEqual(value['reason'],'UNKNOWN_DISPATCH_NEEDS_REVIEW')
            self.assertEqual(leases.conn.execute('SELECT owner FROM lease').fetchone()[0],'previous-worker')
        finally:leases.release('mock','previous-worker');leases.close()

if __name__=='__main__':unittest.main()
