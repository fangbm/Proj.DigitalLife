"""offline_fixture: status truthfulness and read-only safety admission."""
import asyncio,hashlib,json,os,sqlite3,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from phonelife.longrun import LongRunSoakController,prepare_session,control_session,run_worker
from phonelife.longrun_state import SessionLedger,REVIEW_STOP,canonical
from phonelife.storage import Store

FIELDS=['state','control_state','effective_state','safety_terminated','persistent_barrier_present','requires_manual_review',
        'resume_allowed','effective_resume_allowed','worker_launch_allowed','admission_reason_code','admission_evidence',
        'formal_approval_required','initial_start_allowed_without_approval']
CHILD_STATUS='''import json,sys
from pathlib import Path
from phonelife.longrun_state import SessionLedger
l=SessionLedger(Path(sys.argv[1])/'soak-control.sqlite3',readonly=True)
try:v=l.status();print(json.dumps({k:v[k] for k in json.loads(sys.argv[2]) if k in v}))
finally:l.close()
'''
CHILD_RESUME='''import json,sys
from pathlib import Path
from unittest.mock import patch
from phonelife.longrun import control_session
from phonelife.longrun_state import SessionLedger
p=Path(sys.argv[1]);original_init=SessionLedger.__init__;original_open=Path.open
def init(self,*a,**kw):
 original_init(self,*a,**kw)
 if not self.readonly:self.conn.execute('PRAGMA query_only=ON')
def opening(path,*a,**kw):
 if path.name.endswith('.review-stop.json') and a and a[0]=='x':raise PermissionError('offline denied marker')
 return original_open(path,*a,**kw)
with patch.object(SessionLedger,'__init__',init),patch.object(Path,'open',opening):
 try:control_session(p,'running');print(json.dumps({'resume_denied':False}))
 except (ValueError,OSError,Exception) as e:print(json.dumps({'resume_denied':True,'error_type':type(e).__name__}))
'''

class StatusAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'session'
        prepare_session(self.path,seconds=10);self.c=LongRunSoakController(self.path)
    async def asyncTearDown(self):
        if self.c:self.c.close()
        self.temp.cleanup()
    def seed(self,status='uncertain',execution='outcome_unknown'):
        s=Store(self.path/'agent.sqlite3')
        try:
            s.start_task('status-source','mock','offline status fixture',{},run_kind='mock_test')
            s.attempt('status-source',1,1,{'candidate_action':{'type':'tap'},'execution_status':execution})
            s.conn.execute('UPDATE task_runs SET status=? WHERE task_run_id=?',(status,'status-source'))
        finally:s.close()
    def snapshot(self):
        rows={}
        for name in ['soak-control.sqlite3','agent.sqlite3','mock-lease.sqlite3']:
            p=self.path/name
            if not p.exists():continue
            c=sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True);c.execute('PRAGMA query_only=ON')
            try:rows[name]='\n'.join(c.iterdump())
            finally:c.close()
        # Capture hashes AFTER this observer's own RO SQLite reads. Opening a
        # WAL-mode DB can materialize empty sidecars; do not blame Status for
        # bookkeeping created later by the baseline collector itself.
        files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.path.iterdir() if p.is_file() and not p.name.endswith('-shm')}
        return files,rows
    def child(self,script):
        r=subprocess.run([sys.executable,'-c',script,str(self.path),json.dumps(FIELDS)],capture_output=True,text=True,timeout=15)
        self.assertEqual(r.returncode,0,r.stderr);return json.loads(r.stdout)
    def barrier(self):
        return {'session_id':self.c.ledger.load()['session_id'],'reason_code':REVIEW_STOP,'requires_manual_review':True,
            'triggered_utc':'2026-01-01T00:00:00Z','triggered_monotonic':1.,'sources':[],'old_actions_replayed':0}
    async def double_failure(self):
        self.seed();original=Path.open;marker=self.c.ledger.review_path
        def opening(path,*a,**kw):
            if path==marker and a and a[0]=='x':raise PermissionError('offline dual-write fixture')
            return original(path,*a,**kw)
        self.c.ledger.conn.execute('PRAGMA query_only=ON')
        try:
            with patch.object(Path,'open',opening):
                with self.assertRaises(sqlite3.Error):await self.c._review_stop(self.c._health())
        finally:self.c.ledger.conn.execute('PRAGMA query_only=OFF')
        self.c.close();self.c=None
    async def test_dual_write_status_and_resume_cross_process_agree(self):
        await self.double_failure();before=self.snapshot();v=self.child(CHILD_STATUS);res=self.child(CHILD_RESUME)
        self.assertEqual(before,self.snapshot())
        raw=sqlite3.connect(self.path/'soak-control.sqlite3')
        try:control=json.loads(raw.execute('SELECT payload FROM session').fetchone()[0])
        finally:raw.close()
        evidence={'classification':'offline_fixture','status':v,'resume':res,'raw_control_state':control['state'],
            'marker_exists':(self.path/'soak-control.sqlite3.review-stop.json').exists(),'snapshot_unchanged':before==self.snapshot()}
        out=os.environ.get('STATUS_CONTRACT_EVIDENCE_DIR')
        if out:
            p=Path(out);p.mkdir(parents=True,exist_ok=True)
            with (p/'dual-write-cross-process.json').open('x',encoding='utf8') as f:json.dump(evidence,f,indent=2)
        self.assertEqual(control['state'],'prepared');self.assertTrue(res['resume_denied'])
        self.assertFalse(v.get('effective_resume_allowed',v['resume_allowed']))
        self.assertEqual(v['control_state'],'prepared');self.assertEqual(v['effective_state'],'blocked')
        self.assertFalse(v['safety_terminated']);self.assertTrue(v['requires_manual_review']);self.assertFalse(v['worker_launch_allowed'])
    async def test_sqlite_only_barrier_is_persistent(self):
        self.seed();original=Path.open
        def opening(path,*a,**kw):
            if path==self.c.ledger.review_path and a and a[0]=='x':raise PermissionError('offline marker')
            return original(path,*a,**kw)
        with patch.object(Path,'open',opening):self.c.ledger.latch_review({'sources':[]})
        before=self.snapshot();v=self.c.ledger.status();self.assertEqual(before,self.snapshot())
        self.assertTrue(v['safety_terminated']);self.assertFalse(v['effective_resume_allowed'])
    async def test_marker_only_state_preserves_raw_prepared(self):
        self.c.ledger.review_path.write_text(canonical(self.barrier()),encoding='utf8');before=self.snapshot()
        v=self.c.ledger.status();self.assertEqual(before,self.snapshot())
        self.assertEqual(v['control_state'],'prepared');self.assertTrue(v['safety_terminated']);self.assertFalse(v['worker_launch_allowed'])
    async def test_valid_marker_overrides_completed_control(self):
        v=self.c.ledger.load();v['state']='completed_duration';self.c.ledger.save(v)
        self.c.ledger.review_path.write_text(canonical(self.barrier()),encoding='utf8')
        v=self.c.ledger.status();self.assertEqual(v['control_state'],'completed_duration');self.assertFalse(v['effective_resume_allowed'])
    async def test_corrupt_marker_is_blocked_without_invented_committed_terminal(self):
        self.c.ledger.review_path.write_bytes(b'{broken');before=self.snapshot();v=self.c.ledger.status()
        self.assertEqual(before,self.snapshot());self.assertFalse(v['safety_terminated']);self.assertTrue(v['persistent_barrier_present'])
        self.assertEqual(v['effective_state'],'blocked');self.assertFalse(v['resume_allowed'])
    async def test_missing_history_is_unavailable_and_not_created(self):
        (self.path/'agent.sqlite3').unlink();v=self.c.ledger.status()
        self.assertEqual(v['admission_reason_code'],'SAFETY_EVIDENCE_UNAVAILABLE');self.assertFalse(v['worker_launch_allowed'])
        self.assertFalse((self.path/'agent.sqlite3').exists())
    async def test_corrupt_history_does_not_get_repaired(self):
        p=self.path/'agent.sqlite3';p.write_bytes(b'not sqlite');raw=p.read_bytes();v=self.c.ledger.status()
        self.assertFalse(v['effective_resume_allowed']);self.assertEqual(v['admission_reason_code'],'SAFETY_EVIDENCE_UNAVAILABLE');self.assertEqual(raw,p.read_bytes())
    async def test_missing_control_read_status_is_bounded_and_no_create(self):
        p=self.path/'absent.sqlite3';v=SessionLedger.read_status(p)
        self.assertFalse(v['effective_resume_allowed']);self.assertFalse(p.exists());self.assertFalse(v['safety_terminated'])
    async def test_corrupt_control_read_status_no_new_fact(self):
        self.c.close();self.c=None;p=self.path/'soak-control.sqlite3';p.write_bytes(b'invalid');raw=p.read_bytes()
        v=SessionLedger.read_status(p);self.assertFalse(v['worker_launch_allowed']);self.assertFalse(v['safety_terminated']);self.assertEqual(raw,p.read_bytes())
    async def test_locked_control_returns_explicit_unavailable(self):
        db=sqlite3.connect(self.path/'soak-control.sqlite3');db.execute('BEGIN EXCLUSIVE')
        try:
            started=time.monotonic();v=self.c.ledger.status();self.assertLess(time.monotonic()-started,3.)
            self.assertEqual(v['admission_reason_code'],'SAFETY_EVIDENCE_UNAVAILABLE');self.assertFalse(v['effective_resume_allowed'])
        finally:db.rollback();db.close()
    async def test_emergency_has_priority_over_unknown(self):
        self.seed();self.c.ledger.operator('emergency_stopped');v=self.c.ledger.status()
        self.assertEqual(v['effective_state'],'emergency_stopped');self.assertEqual(v['admission_reason_code'],'OPERATOR_STOP')
        self.assertTrue(v['requires_manual_review']);self.assertFalse(v['worker_launch_allowed'])
    async def test_healthy_paused_resume_agrees(self):
        self.c.ledger.start();self.c.ledger.operator('paused');self.c.ledger.tick('paused')
        v=self.c.ledger.status();self.assertTrue(v['effective_resume_allowed']);self.assertFalse(v['worker_launch_allowed'])
        control_session(self.path,'running');self.assertEqual(self.c.ledger.load()['operator'],'running')
    async def test_prepared_is_not_a_worker_launch(self):
        v=self.c.ledger.status();self.assertEqual(v['control_state'],'prepared');self.assertTrue(v['effective_resume_allowed'])
        self.assertFalse(v['worker_launch_allowed']);self.assertFalse(v['formal_approval_required'])
    async def test_formal_awaiting_requires_approval_not_resume(self):
        v=self.c.ledger.load();v['config']['source_kind']='live_api_natural_occurrence';v['state']='awaiting_approval'
        v['fingerprint']=hashlib.sha256(canonical(v['config']).encode()).hexdigest();self.c.ledger.save(v)
        v=self.c.ledger.status();self.assertTrue(v['formal_approval_required']);self.assertFalse(v['worker_launch_allowed'])
        self.assertFalse(v['initial_start_allowed_without_approval'])
        with self.assertRaises(PermissionError):self.c.ledger.start()
    async def test_healthy_running_reports_no_persistent_termination(self):
        self.c.ledger.start();v=self.c.ledger.status()
        self.assertEqual(v['effective_state'],'running');self.assertFalse(v['safety_terminated']);self.assertTrue(v['worker_launch_allowed'])
    async def test_completed_duration_stays_terminal(self):
        self.c.ledger.start();v=self.c.ledger.tick('completed_duration');v=self.c.ledger.status()
        self.assertEqual(v['control_state'],'completed_duration');self.assertFalse(v['safety_terminated']);self.assertFalse(v['effective_resume_allowed'])
    async def test_repeated_status_does_not_change_rows_or_hashes(self):
        self.seed();before=self.snapshot()
        with patch('phonelife.longrun.subprocess.Popen') as launch,patch('phonelife.devices.AdbPhone') as device,patch('phonelife.longrun_api.SoakAPI') as api:
            for _ in range(3):self.assertFalse(self.c.ledger.status()['effective_resume_allowed'])
            launch.assert_not_called();device.assert_not_called();api.assert_not_called()
        self.assertEqual(before,self.snapshot());self.assertFalse(self.c.ledger.review_path.exists())
    async def test_status_and_supervisor_refuse_same_unknown(self):
        self.seed();v=self.c.ledger.status();self.assertFalse(v['worker_launch_allowed'])
        with patch('phonelife.longrun.subprocess.Popen') as launch:
            r=await self.c.run();launch.assert_not_called()
        self.assertEqual(r['state'],'failed')
    async def test_status_and_direct_worker_refuse_same_unknown(self):
        self.c.ledger.start();self.seed();self.assertFalse(self.c.ledger.status()['worker_launch_allowed'])
        with patch('phonelife.devices.MockPhone') as device,patch('phonelife.longrun_api.SoakAPI') as api:
            await run_worker(self.path,os.getpid());device.assert_not_called();api.assert_not_called()
    async def test_status_and_resume_reject_unreadable_history(self):
        (self.path/'agent.sqlite3').unlink();self.assertFalse(self.c.ledger.status()['effective_resume_allowed'])
        with self.assertRaises(ValueError):control_session(self.path,'running')
        self.assertFalse((self.path/'agent.sqlite3').exists())
    async def test_wal_database_status_is_readonly(self):
        store=Store(self.path/'agent.sqlite3')
        try:
            store.event('mock','offline_wal_fixture',{});before=self.snapshot();self.c.ledger.status()
            self.assertEqual(before,self.snapshot())
        finally:store.close()
    async def test_status_does_not_latch_a_history_only_stop(self):
        self.seed();v=self.c.ledger.status();self.assertFalse(v['safety_terminated']);self.assertTrue(v['requires_manual_review'])
        self.assertEqual(v['control_state'],'prepared');self.assertEqual(self.c.ledger.load()['state'],'prepared')
        self.assertFalse(self.c.ledger.review_path.exists())
    async def test_active_lease_and_future_wake_do_not_grant_worker_launch(self):
        self.c.ledger.start();store=Store(Path(self.c.config['lease_path']))
        try:
            store.acquire('mock','offline-other-owner',30);v=self.c.ledger.status()
            self.assertFalse(v['worker_launch_allowed']);self.assertTrue(v['effective_resume_allowed'])
        finally:store.release('mock','offline-other-owner');store.close()
    async def test_readonly_snapshot_collector_is_self_consistent(self):
        self.assertEqual(self.snapshot(),self.snapshot())
    async def test_supervisor_cannot_launch_when_control_snapshot_unavailable(self):
        blocked=SessionLedger._unavailable_status(sqlite3.OperationalError('offline locked'))
        with patch.object(self.c.ledger,'status',return_value=blocked),patch('phonelife.longrun.subprocess.Popen') as launch:
            await self.c.run();launch.assert_not_called()
        self.assertEqual(self.c.ledger.load()['state'],'failed')
    async def test_cli_missing_control_returns_blocked_json_without_creating_files(self):
        missing=self.path.parent/'missing-session'
        r=subprocess.run([sys.executable,'-m','phonelife.longrun','status','--session',str(missing)],capture_output=True,text=True,timeout=15)
        self.assertEqual(r.returncode,0,r.stderr);v=json.loads(r.stdout)
        self.assertFalse(v['effective_resume_allowed']);self.assertFalse(missing.exists())
    async def test_healthy_worker_passes_the_same_safety_gate_before_adapter(self):
        self.c.ledger.start();self.assertTrue(self.c.ledger.status()['worker_launch_allowed'])
        class ReachedAdapter(Exception):pass
        with patch('phonelife.devices.MockPhone',side_effect=ReachedAdapter('offline boundary')) as adapter:
            with self.assertRaises(ReachedAdapter):await run_worker(self.path,os.getpid())
            adapter.assert_called_once()

if __name__=='__main__':unittest.main()
