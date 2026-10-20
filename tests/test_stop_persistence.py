"""offline_fixture: two-medium stop durability; temporary DBs/mock workers only."""
import asyncio
from contextlib import ExitStack,contextmanager
import errno
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from phonelife.longrun import LongRunSoakController,prepare_session,control_session,run_worker,main
from phonelife.longrun_state import SessionLedger,SoakPolicy,REVIEW_STOP,canonical
from phonelife.storage import Store

REAL_OPEN=Path.open
REOPEN_SCRIPT='''import json,sys
from pathlib import Path
from phonelife.longrun_state import SessionLedger
from phonelife.longrun_health import readonly_db_health
p=Path(sys.argv[1]);l=SessionLedger(p/'soak-control.sqlite3',readonly=True)
v=l.status();cfg=v['config'];h=readonly_db_health(cfg['agent_db'],cfg['lease_path'],cfg['device'],worker_alive=False)
print(json.dumps({'state':v['state'],'reason':v['reason'],'requires_manual_review':v['requires_manual_review'],
 'resume_allowed':v['resume_allowed'],'raw_unknown_requires_review':h['safety_review_required'],'database_readable':h['database_readable']}));l.close()
'''

class OwnedMockWorker:
    """Only exits on a persisted signal or explicit termination of this own handle."""
    pid=987654321
    def __init__(self,ledger):self.ledger=ledger;self.code=None;self.terminations=0;self.kills=0
    def poll(self):
        if self.code is None:
            row=self.ledger.conn.execute('SELECT payload FROM session').fetchone()
            if json.loads(row[0]).get('worker_signal')=='stop':self.code=0
        return self.code
    def terminate(self):self.terminations+=1;self.code=-15
    def kill(self):self.kills+=1;self.code=-9

class StopPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'session'
        prepare_session(self.path,seconds=10,policy=SoakPolicy(min_free_bytes=1))
        self.c=LongRunSoakController(self.path)
        self.child=OwnedMockWorker(self.c.ledger);self.c.worker=self.child
        self.run_id='offline-persistence-task'
        s=Store(self.path/'agent.sqlite3')
        try:
            s.start_task(self.run_id,'mock','offline durability fixture',{},run_kind='mock_test')
            self.attempt_id=s.attempt(self.run_id,1,1,{'task_run_id':self.run_id,'candidate_action':{'type':'tap'},
                'execution_status':'uncertain_after_transport_error','dispatch_monotonic_ns':1})
            s.conn.execute("UPDATE task_runs SET status='uncertain' WHERE task_run_id=?",(self.run_id,))
        finally:s.close()
        self.original_records=self.records()
        self.last_evidence=None
    async def asyncTearDown(self):
        self.assertIsNotNone(self.child.poll(),'owned mock worker must be stopped in every case')
        self.c.close();self.temp.cleanup()
    def records(self):
        s=Store.open_readonly(self.path/'agent.sqlite3')
        try:return {t:list(s.conn.execute('SELECT * FROM '+t)) for t in ['task_runs','action_attempts']}
        finally:s.close()
    def original_barrier(self,tag='first-event'):
        return {'session_id':self.c.ledger.load()['session_id'],'reason_code':REVIEW_STOP,'triggered_utc':'2026-01-01T00:00:00Z',
            'triggered_monotonic':1.,'requires_manual_review':True,'old_actions_replayed':0,'event_tag':tag,
            'sources':[{'task_run_id':self.run_id,'attempt_id':self.attempt_id,'execution_status':'uncertain_after_transport_error'}]}
    @contextmanager
    def faults(self,mode,db_fail=False):
        marker=self.c.ledger.review_path;hit={'value':False};real_fsync=os.fsync
        class Stream:
            def __init__(self,stream):self.stream=stream
            def __enter__(self):return self
            def __exit__(self,*args):self.stream.close()
            def write(self,s):
                if mode=='write':
                    self.stream.write('{partial');hit['value']=True;raise OSError(errno.ENOSPC,'offline injected partial write')
                return self.stream.write(s)
            def flush(self):
                if mode=='flush':hit['value']=True;raise OSError(errno.EIO,'offline injected flush')
                return self.stream.flush()
            def fileno(self):return self.stream.fileno()
        def opening(p,*a,**kw):
            if p==marker and (a[0] if a else kw.get('mode'))=='x':
                if mode=='permission':hit['value']=True;raise PermissionError(errno.EACCES,'offline injected marker create')
                if mode=='oserror':hit['value']=True;raise OSError(errno.ENOSPC,'offline injected marker create')
                stream=REAL_OPEN(p,*a,**kw)
                return Stream(stream) if mode in {'write','flush'} else stream
            return REAL_OPEN(p,*a,**kw)
        def syncing(fd):
            if mode=='fsync' and not hit['value']:
                hit['value']=True;raise OSError(errno.EIO,'offline injected fsync')
            return real_fsync(fd)
        if db_fail:self.c.ledger.conn.execute('PRAGMA query_only=ON')
        try:
            with patch.object(Path,'open',opening),patch('phonelife.longrun_state.os.fsync',side_effect=syncing):yield hit
        finally:
            if db_fail:self.c.ledger.conn.execute('PRAGMA query_only=OFF')
    def reopened(self):
        # Actual close/open in a different process; no session execution or device imports.
        result=subprocess.run([sys.executable,'-c',REOPEN_SCRIPT,str(self.path)],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        return json.loads(result.stdout)
    async def exercise(self,mode='normal',db_fail=False,durable=True,expected_state='failed'):
        started=time.monotonic();caught=None
        with self.faults(mode,db_fail) as hit,patch('phonelife.longrun.subprocess.Popen') as launch,\
             patch('phonelife.devices.AdbPhone') as device,patch('phonelife.longrun_api.SoakAPI') as api:
            try:await self.c._review_stop(self.c._health())
            except (OSError,sqlite3.Error) as e:caught=type(e).__name__
            self.assertIsNotNone(self.child.poll())
            launch.assert_not_called();device.assert_not_called();api.assert_not_called()
        self.assertLess(time.monotonic()-started,5.)
        if mode in {'permission','oserror','write','flush','fsync'}:self.assertTrue(hit['value'])
        self.assertFalse(self.c.ledger.conn.in_transaction)
        current=self.c.ledger.status()
        marker=self.c.ledger.review_path
        raw=marker.read_bytes() if marker.exists() else None
        try:parsed=json.loads(raw) if raw is not None else None;valid=isinstance(parsed,dict) and parsed.get('reason_code')==REVIEW_STOP
        except (ValueError,UnicodeError):parsed=None;valid=False
        sql=json.loads(self.c.ledger.conn.execute('SELECT payload FROM session').fetchone()[0])
        reopened=self.reopened()
        self.assertEqual(self.original_records,self.records())
        s=Store.open_readonly(self.path/'agent.sqlite3')
        try:self.assertEqual(s.conn.execute('SELECT COUNT(*) FROM life_requests').fetchone()[0],0)
        finally:s.close()
        result_path=self.path/'review-stop-result.json'
        audit=json.loads(result_path.read_text(encoding='utf8')) if result_path.exists() else None
        if audit:
            self.assertFalse(audit['new_actions_dispatched']);self.assertEqual(audit['old_actions_replayed'],0)
        # Every matrix case records physical SQL, actual marker, current worker and fresh-process status.
        self.last_evidence={'classification':'offline_fixture','case':self._testMethodName,'fault':mode,'sqlite_fault':db_fail,
            'worker_stopped':self.child.poll() is not None,'worker_terminations':self.child.terminations,
            'sqlite_state':sql['state'],'sqlite_requires_manual_review':sql.get('requires_manual_review',False),
            'marker_exists':raw is not None,'marker_json_valid_barrier':valid,'marker_bytes':len(raw) if raw is not None else 0,
            'marker_sha256':hashlib.sha256(raw).hexdigest() if raw is not None else None,
            'current_requires_manual_review':current['requires_manual_review'],'current_resume_allowed':current['resume_allowed'],
            'persistence':current.get('safety_stop',{}).get('persistence',{}),'exception_type':caught,'subprocess_reopen':reopened,
            'new_actions_dispatched':False,'old_actions_replayed':0,'original_task_attempt_rows_equal':True,'api_requests':0,
            'adb_adapters_created':0,'durable_guarantee_expected':durable,'stop_seconds':time.monotonic()-started}
        evidence=os.environ.get('STOP_PERSISTENCE_EVIDENCE_DIR')
        if evidence:
            p=Path(evidence);p.mkdir(parents=True,exist_ok=True)
            with (p/(self._testMethodName+'.json')).open('x',encoding='utf8') as f:json.dump(self.last_evidence,f,indent=2)
        self.assertEqual(current['state'],expected_state)
        self.assertTrue(current['requires_manual_review']);self.assertFalse(current['resume_allowed'])
        if durable:
            self.assertEqual(reopened['state'],expected_state);self.assertTrue(reopened['requires_manual_review']);self.assertFalse(reopened['resume_allowed'])
        else:
            self.assertFalse(raw);self.assertNotIn('safety_stop',sql)
            self.assertTrue(reopened['raw_unknown_requires_review'])
        return sql,raw,parsed
    async def test_marker_create_permission_sqlite_still_commits(self):
        sql,raw,_=await self.exercise('permission');self.assertEqual(sql['state'],'failed');self.assertIsNone(raw)
        self.assertTrue(sql['safety_stop']['persistence']['marker']['error_type']=='PermissionError')
    async def test_marker_create_oserror_sqlite_still_commits(self):
        sql,raw,_=await self.exercise('oserror');self.assertEqual(sql['state'],'failed');self.assertIsNone(raw)
    async def test_partial_write_is_preserved_and_sqlite_commits(self):
        sql,raw,_=await self.exercise('write');self.assertEqual(raw,b'{partial');self.assertEqual(sql['state'],'failed')
    async def test_flush_failure_still_commits_sqlite(self):
        sql,_,_=await self.exercise('flush');self.assertEqual(sql['state'],'failed')
        self.assertEqual(sql['safety_stop']['persistence']['marker']['operation'],'flush')
    async def test_fsync_failure_still_commits_sqlite(self):
        sql,_,_=await self.exercise('fsync');self.assertEqual(sql['state'],'failed')
        self.assertEqual(sql['safety_stop']['persistence']['marker']['operation'],'fsync')
    async def test_existing_valid_marker_first_event_is_immutable(self):
        raw=canonical(self.original_barrier()).encode();self.c.ledger.review_path.write_bytes(raw)
        sql,after,parsed=await self.exercise();self.assertEqual(after,raw);self.assertEqual(parsed['event_tag'],'first-event')
        self.assertEqual(sql['safety_stop']['event_tag'],'first-event')
    async def test_existing_corrupt_marker_is_not_repaired_and_fails_closed(self):
        self.c.ledger.review_path.write_bytes(b'{broken');sql,raw,_=await self.exercise()
        self.assertEqual(raw,b'{broken');self.assertEqual(sql['state'],'failed')
    async def test_absent_marker_and_writable_sqlite_commit_both(self):
        sql,raw,parsed=await self.exercise();self.assertIsNotNone(raw);self.assertEqual(sql['state'],'failed')
        self.assertEqual(parsed['sources'][0]['attempt_id'],self.attempt_id)
    async def test_marker_survives_readonly_sqlite_and_process_reopen(self):
        sql,raw,_=await self.exercise(db_fail=True);self.assertIsNotNone(raw);self.assertEqual(sql['state'],'prepared')
    async def test_conflicting_sqlite_emergency_and_marker_preserve_priority(self):
        self.c.ledger.operator('emergency_stopped');self.c.ledger.review_path.write_text(canonical(self.original_barrier()),encoding='utf8')
        await self.exercise(expected_state='emergency_stopped')
    async def test_double_failure_stops_owned_worker_without_claiming_durability(self):
        await self.exercise('permission',db_fail=True,durable=False)
        self.assertEqual(self.child.terminations,1)
    async def test_double_failure_restart_rechecks_original_unknown_before_launch(self):
        await self.exercise('oserror',db_fail=True,durable=False)
        other=LongRunSoakController(self.path)
        try:
            with patch('phonelife.longrun.subprocess.Popen') as launch:
                value=await other.run();launch.assert_not_called()
            self.assertEqual(value['state'],'failed');self.assertFalse(value['resume_allowed'])
        finally:other.close()
    async def test_sqlite_barrier_missing_marker_cannot_be_overridden_by_tick(self):
        await self.exercise('permission');self.c.close();self.c=LongRunSoakController(self.path)
        self.assertEqual(self.c.ledger.tick('completed_duration')['state'],'failed')
        with self.assertRaises(ValueError):self.c.ledger.operator('running')
    async def test_sqlite_barrier_wins_external_nonterminal_status_change(self):
        await self.exercise('permission');raw=json.loads(self.c.ledger.conn.execute('SELECT payload FROM session').fetchone()[0])
        raw.update(state='running',operator='running',reason='external-change')
        self.c.ledger.conn.execute('UPDATE session SET payload=?',(json.dumps(raw),))
        self.assertFalse(self.reopened()['resume_allowed']);self.assertEqual(self.reopened()['state'],'failed')
    async def test_repeated_permission_failure_keeps_first_sqlite_event(self):
        await self.exercise('permission');first=self.c.ledger.load()['safety_stop']['triggered_monotonic']
        with self.faults('permission'):self.c.ledger.latch_review({'event_tag':'later','sources':[]})
        self.assertEqual(self.c.ledger.load()['safety_stop']['triggered_monotonic'],first)
        self.assertNotIn('event_tag',self.c.ledger.load()['safety_stop'])
    async def test_sqlite_first_event_wins_conflicting_valid_marker(self):
        await self.exercise('permission');first=self.c.ledger.load()['safety_stop']
        self.c.ledger.review_path.write_text(canonical(self.original_barrier('different-event')),encoding='utf8')
        self.assertEqual(self.c.ledger.load()['safety_stop']['triggered_monotonic'],first['triggered_monotonic'])
        self.assertFalse(self.reopened()['resume_allowed'])
    async def test_corrupt_marker_with_sqlite_failure_still_blocks_reopen(self):
        self.c.ledger.review_path.write_bytes(b'');await self.exercise(db_fail=True)
    async def test_marker_semantic_corruption_empty_object_is_fail_closed(self):
        self.c.ledger.review_path.write_text('{}',encoding='utf8');await self.exercise()
        self.assertTrue(self.c.ledger.load()['safety_stop'].get('evidence_unreadable'))
    async def test_marker_wrong_session_is_rejected_as_unreadable_evidence(self):
        b=self.original_barrier();b['session_id']='wrong-session';self.c.ledger.review_path.write_text(canonical(b),encoding='utf8')
        await self.exercise();self.assertTrue(self.c.ledger.load()['safety_stop'].get('evidence_unreadable'))
    async def test_resume_and_force_reject_sqlite_only_stop(self):
        await self.exercise('permission')
        with self.assertRaises(ValueError):control_session(self.path,'running')
        with self.assertRaises(SystemExit) as e:main(['run','--session',str(self.path),'--force'])
        self.assertEqual(e.exception.code,2)
    async def test_direct_worker_refuses_sqlite_only_stop_before_adapter(self):
        self.c.ledger.start();await self.exercise('permission')
        with patch('phonelife.devices.AdbPhone') as device,patch('phonelife.longrun_api.SoakAPI') as api:
            await run_worker(self.path,os.getpid());device.assert_not_called();api.assert_not_called()
    async def test_direct_worker_refuses_unreadable_safety_db_before_adapter(self):
        self.c.ledger.start();self.child.terminate()
        health={'database_readable':False,'needs_review':False,'safety_review_required':False}
        with patch('phonelife.longrun.readonly_db_health',return_value=health),patch('phonelife.devices.MockPhone') as device,patch('phonelife.longrun_api.SoakAPI') as api:
            with self.assertRaisesRegex(PermissionError,'database unreadable'):await run_worker(self.path,os.getpid())
            device.assert_not_called();api.assert_not_called()
    async def test_begin_transaction_failure_rolls_back_and_marker_survives(self):
        await self.exercise(db_fail=True);self.assertFalse(self.c.ledger.conn.in_transaction)
    async def test_commit_failure_rolls_back_and_marker_survives(self):
        self.child.terminate()
        self.c.ledger.conn.set_authorizer(lambda action,a,b,db,trigger:sqlite3.SQLITE_DENY if action==sqlite3.SQLITE_TRANSACTION and a=='COMMIT' else sqlite3.SQLITE_OK)
        try:
            with self.assertRaises(sqlite3.DatabaseError):self.c.ledger.latch_review({'sources':[]})
            self.assertFalse(self.c.ledger.conn.in_transaction)
        finally:self.c.ledger.conn.set_authorizer(None)
        self.assertFalse(self.reopened()['resume_allowed'])
    async def test_independent_child_process_writes_sqlite_when_marker_denied(self):
        self.child.terminate()
        script='''import json,sys
from pathlib import Path
from unittest.mock import patch
from phonelife.longrun_state import SessionLedger
p=Path(sys.argv[1]);l=SessionLedger(p/'soak-control.sqlite3');real=Path.open
def fail(path,*a,**kw):
 if path==l.review_path and a and a[0]=='x':raise PermissionError('offline child deny')
 return real(path,*a,**kw)
with patch.object(Path,'open',fail):l.latch_review({'sources':[]})
l.close()
'''
        r=subprocess.run([sys.executable,'-c',script,str(self.path)],capture_output=True,text=True,timeout=15)
        self.assertEqual(r.returncode,0,r.stderr);self.assertFalse(self.reopened()['resume_allowed'])
    async def test_emergency_stop_after_sqlite_only_latch_remains_highest(self):
        await self.exercise('permission');self.c.ledger.operator('emergency_stopped')
        self.assertEqual(self.c.ledger.tick('completed_duration')['state'],'emergency_stopped')
        self.assertEqual(self.c.ledger.load()['safety_stop']['reason_code'],REVIEW_STOP)
    async def test_normal_86400_seconds_and_no_api_or_actions_unchanged(self):
        self.child.terminate();normal=self.path.parent/'normal-expiry';prepare_session(normal,seconds=86400)
        ledger=SessionLedger(normal/'soak-control.sqlite3')
        try:
            ledger.start();started=ledger.load()['started_monotonic']
            with patch.object(ledger.clock,'monotonic',return_value=started+86400):
                self.assertEqual(ledger.tick('sleeping')['state'],'completed_duration')
        finally:ledger.close()
        s=Store.open_readonly(normal/'agent.sqlite3')
        try:
            self.assertEqual(s.conn.execute('SELECT COUNT(*) FROM action_attempts').fetchone()[0],0)
            self.assertEqual(s.conn.execute('SELECT COUNT(*) FROM life_requests').fetchone()[0],0)
        finally:s.close()
        self.assertEqual(self.records(),self.original_records)
    async def test_strong_kill_of_offline_child_preserves_unknown_without_replay(self):
        self.child.terminate()
        ready=self.path/'crash-ready'
        script='''import sys,time
from pathlib import Path
from phonelife.storage import Store
p=Path(sys.argv[1]);s=Store(p/'agent.sqlite3')
s.attempt('offline-persistence-task',2,1,{'candidate_action':{'type':'tap'},'execution_status':'outcome_unknown','dispatch_monotonic_ns':2})
s.close();(p/'crash-ready').write_text('journal committed, no actual input')
time.sleep(30)
'''
        child=subprocess.Popen([sys.executable,'-c',script,str(self.path)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            until=time.monotonic()+8
            while not ready.exists() and child.poll() is None and time.monotonic()<until:await asyncio.sleep(.02)
            self.assertTrue(ready.exists());before_kill=self.records()
            child.kill();child.wait(timeout=5)
        finally:
            if child.poll() is None:child.kill();child.wait(timeout=5)
        other=LongRunSoakController(self.path)
        try:
            with patch('phonelife.longrun.subprocess.Popen') as launch:
                self.assertEqual((await other.run())['state'],'failed');launch.assert_not_called()
        finally:other.close()
        self.assertEqual(self.records(),before_kill)
        self.assertEqual(len(before_kill['action_attempts']),2)
    async def test_controller_marker_create_failure_returns_failed_not_interrupted(self):
        with self.faults('permission'),patch('phonelife.longrun.subprocess.Popen') as launch:
            value=await self.c.run();launch.assert_not_called()
        self.assertEqual(value['state'],'failed');self.assertFalse(value['resume_allowed'])
        self.assertIsNotNone(self.child.poll());self.assertEqual(self.records(),self.original_records)
        self.assertFalse(self.reopened()['resume_allowed'])
    async def test_supervisor_unreadable_source_cannot_launch_worker_at_deadline(self):
        self.child.terminate();self.c.ledger.start()
        health={'database_readable':False,'needs_review':False,'safety_review_required':False,'operator':'running','warnings':[]}
        with patch('phonelife.longrun.readonly_db_health',return_value=health),patch('phonelife.longrun.subprocess.Popen') as launch,\
             patch.object(self.c.ledger,'remaining',return_value=0):
            value=await self.c.run();launch.assert_not_called()
        self.assertEqual(value['state'],'failed');self.assertFalse(value['resume_allowed'])
    async def test_sqlite_only_stop_allows_new_explicit_review_link_without_resuming_old(self):
        await self.exercise('permission');old=self.c.ledger.load()
        new=prepare_session(self.path.parent/'new-reviewed',reviewed_session=self.path,review_reference='offline-reviewed-sqlite-only')
        self.assertEqual(new['config']['prior_reviewed_fault']['barrier_medium'],'sqlite')
        self.assertEqual(new['config']['prior_reviewed_fault']['session_id'],old['session_id'])
        self.assertFalse(self.reopened()['resume_allowed'])
    async def test_partial_marker_retry_never_replaces_first_bytes_or_trigger(self):
        await self.exercise('write');raw=self.c.ledger.review_path.read_bytes();first=self.c.ledger.load()['safety_stop']
        self.c.ledger.latch_review({'sources':[],'event_tag':'second'})
        self.assertEqual(raw,self.c.ledger.review_path.read_bytes())
        self.assertEqual(first['triggered_monotonic'],self.c.ledger.load()['safety_stop']['triggered_monotonic'])
    async def test_sqlite_only_stop_pause_cannot_clear_barrier(self):
        await self.exercise('permission')
        with self.assertRaises(ValueError):self.c.ledger.operator('paused')
        self.assertFalse(self.reopened()['resume_allowed'])
    async def test_completed_sqlite_conflicts_with_marker_and_cannot_win(self):
        self.c.ledger.review_path.write_text(canonical(self.original_barrier()),encoding='utf8')
        raw=json.loads(self.c.ledger.conn.execute('SELECT payload FROM session').fetchone()[0]);raw.update(state='completed_duration',reason='GLOBAL_DEADLINE')
        self.c.ledger.conn.execute('UPDATE session SET payload=?',(json.dumps(raw),))
        sql,_,_=await self.exercise();self.assertEqual(sql['state'],'failed')
    async def test_interleaved_sqlite_writer_keeps_first_event_under_transaction(self):
        self.child.terminate();first=self.original_barrier('first-committed-event');marker=self.c.ledger.review_path
        def interleave(path,*a,**kw):
            if path==marker and a and a[0]=='x':
                db=sqlite3.connect(self.path/'soak-control.sqlite3')
                try:
                    raw=json.loads(db.execute('SELECT payload FROM session').fetchone()[0])
                    raw.update(safety_stop=first,state='failed',reason=REVIEW_STOP,requires_manual_review=True,worker_signal='stop')
                    db.execute('UPDATE session SET payload=?',(json.dumps(raw),));db.commit()
                finally:db.close()
                raise PermissionError('offline interleaved marker denial')
            return REAL_OPEN(path,*a,**kw)
        with patch.object(Path,'open',interleave),patch('phonelife.longrun.subprocess.Popen') as launch:
            self.c.ledger.latch_review({'event_tag':'later-event','sources':[]});launch.assert_not_called()
        self.assertEqual(self.c.ledger.load()['safety_stop']['event_tag'],'first-committed-event')
        self.assertFalse(self.reopened()['resume_allowed']);self.assertEqual(self.records(),self.original_records)

if __name__=='__main__':unittest.main()
