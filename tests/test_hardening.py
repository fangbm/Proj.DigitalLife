"""Offline regressions for Phase 1.1.1, including actual file/IPC checks."""
import asyncio
import hashlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageDraw

from phonelife import cli
from phonelife.llm import APIError, MockPlanner
from phonelife.loop import PhoneLife
from phonelife.models import Action, Decision, ScreenObservation, VerificationResult
from phonelife.policy import ActionPolicy
from phonelife.progress import ActionProgressDetector, StagnationGuard
from phonelife.replay import replay_task
from phonelife.storage import Store
from phonelife.verification import AboutPhoneVerifier
from test_phase11 import SequenceGui, FixturePhone, FINISH, ACHIEVED, TAP

SWIPE = '{"type":"swipe","x":360,"y":1300,"x2":360,"y2":500,"duration_ms":400,"reason":"scroll"}'


def frame(offset=0, seed=0, bar=0, animation=None):
    image = Image.new('RGB', (720, 1600), 'white')
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 719, 100), fill=(bar, bar, bar))
    draw.rectangle((0, 1500, 719, 1599), fill=(bar, bar, bar))
    for row in range(16):
        y = 160 + row * 135 - offset
        if 130 < y < 1430:
            draw.rectangle((80, y, 180 + (row * 73 + seed * 47) % 400, y + 35), fill='black')
    if animation is not None:
        draw.rectangle((280, 650, 440, 850), fill=(animation, animation, animation))
    data = io.BytesIO(); image.save(data, format='PNG')
    return ScreenObservation.from_png(data.getvalue(), 'fixture')


class MovingPhone(FixturePhone):
    def __init__(self, moving=True):
        super().__init__(); self.state = 0; self.moving = moving

    async def observe(self):
        return frame(offset=self.state * 75)

    async def act(self, action):
        result = await super().act(action)
        if self.moving:
            self.state += 1
        return result


class ProgressTests(unittest.TestCase):
    def test_same_swipe_with_continued_body_scroll_is_progress(self):
        detector=ActionProgressDetector();action=Action.from_dict(json.loads(SWIPE));guard=StagnationGuard()
        for i in range(4):
            self.assertFalse(guard.should_stop(action))
            assessment=detector.assess(action,(frame(i*75),frame(i*75)),(frame((i+1)*75),frame((i+1)*75)))
            self.assertEqual(assessment.verdict,'progressed');guard.record(action,assessment)

    def test_same_swipe_with_unchanged_body_stops_before_third_dispatch(self):
        detector=ActionProgressDetector();action=Action.from_dict(json.loads(SWIPE));guard=StagnationGuard()
        screen=frame()
        for _ in range(2):
            self.assertFalse(guard.should_stop(action))
            guard.record(action,detector.assess(action,(screen,screen),(screen,screen)))
        self.assertTrue(guard.should_stop(action))

    def test_clock_notification_battery_and_nav_changes_are_excluded(self):
        assessment=ActionProgressDetector().assess(Action.from_dict(json.loads(SWIPE)),
            (frame(bar=0),frame(bar=80)),(frame(bar=160),frame(bar=255)))
        self.assertEqual(assessment.verdict,'stagnant')
        self.assertEqual(assessment.changed_fraction,0)

    def test_temporal_body_animation_does_not_count_as_progress(self):
        assessment=ActionProgressDetector().assess(Action.from_dict(json.loads(SWIPE)),
            (frame(animation=0),frame(animation=255)),(frame(animation=255),frame(animation=0)))
        self.assertNotEqual(assessment.verdict,'progressed')

    def test_local_slow_animation_between_stable_samples_is_not_progress(self):
        assessment=ActionProgressDetector().assess(Action.from_dict(json.loads(SWIPE)),
            (frame(animation=0),frame(animation=0)),(frame(animation=255),frame(animation=255)))
        self.assertNotEqual(assessment.verdict,'progressed')

    def test_same_tap_to_different_pages_is_allowed(self):
        detector=ActionProgressDetector();action=Action.from_dict(json.loads(TAP));guard=StagnationGuard()
        for i in range(4):
            self.assertFalse(guard.should_stop(action))
            assessment=detector.assess(action,(frame(seed=i),frame(seed=i)),(frame(seed=i+1),frame(seed=i+1)))
            self.assertEqual(assessment.verdict,'progressed');guard.record(action,assessment)

    def test_same_tap_without_progress_is_bounded(self):
        action=Action.from_dict(json.loads(TAP));guard=StagnationGuard();screen=frame()
        assessment=ActionProgressDetector().assess(action,(screen,screen),(screen,screen))
        guard.record(action,assessment);guard.record(action,assessment)
        self.assertTrue(guard.should_stop(action))


class ManagedTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'state.db';self.store=Store(self.path)
        self.phone=MovingPhone();self.gui=SequenceGui([FINISH])

    def tearDown(self):
        self.store.close();self.tmp.cleanup()

    def life(self, responses=None, phone=None, **kwargs):
        self.gui=SequenceGui(responses or [FINISH]);self.phone=phone or self.phone
        return PhoneLife(self.store,'mock',self.phone,MockPlanner(),self.gui,
            ActionPolicy('mock',frozenset({'com.android.chrome'})),action_delay=0,**kwargs)

    def test_repeated_valid_swipes_execute_four_times(self):
        life=self.life([SWIPE]*4+[FINISH])
        result=asyncio.run(life.execute_goal('scroll'))
        self.assertEqual(result.steps,4);self.assertEqual(len(self.phone.actions),4)
        self.assertNotEqual(result.reason_code,'STAGNATION')

    def test_stagnant_candidate_never_dispatches_and_events_are_exact(self):
        life=self.life([TAP]*4,phone=MovingPhone(moving=False))
        result=asyncio.run(life.execute_goal('tap'))
        self.assertEqual(result.reason_code,'STAGNATION');self.assertEqual(result.steps,2)
        self.assertEqual(len(self.phone.actions),2)
        task=self.store.task(result.task_run_id)
        self.assertEqual(len(task['attempts']),3)
        self.assertEqual(task['attempts'][-1]['payload']['execution_status'],'not_executed')
        events=self.store.conn.execute("SELECT payload FROM events WHERE event_type='device_action'").fetchall()
        self.assertEqual(len(events),2)

    def test_audit_holds_lease_renews_and_releases(self):
        life=self.life(lease_seconds=.12,session_timeout=1)
        async def audit(result,observation):
            other=Store(self.path)
            try:
                self.assertFalse(other.acquire('mock','intruder'))
                await asyncio.sleep(.18)
                self.assertFalse(other.acquire('mock','intruder'))
            finally:other.close()
            return {'verification':asdict(VerificationResult('uncertain',[],'fixture',observation_sha256=observation.sha256))}
        result=asyncio.run(life.execute_goal('observe',post_audit=audit))
        self.assertTrue(result.post_audit['lease_protected'])
        self.assertEqual(result.post_audit['task_run_id'],result.task_run_id)
        self.assertTrue(self.store.acquire('mock','after'));self.store.release('mock','after')

    def test_audit_observation_and_result_are_linked(self):
        life=self.life()
        async def audit(result,observation):
            self.assertIsNotNone(self.store.conn.execute('SELECT * FROM lease').fetchone())
            return {'verification':asdict(VerificationResult('uncertain',[],'fixture',observation_sha256=observation.sha256))}
        result=asyncio.run(life.execute_goal('observe',post_audit=audit));task=self.store.task(result.task_run_id)
        self.assertEqual(task['result']['post_audit']['observation_sha256'],result.post_audit['observation_sha256'])
        self.assertIn(result.post_audit['observation_id'],[o['observation_id'] for o in task['observations']])

    def test_audit_blocks_an_actual_second_process(self):
        life=self.life()
        async def audit(result,observation):
            code='from pathlib import Path; from phonelife.storage import Store; import sys; s=Store(Path(sys.argv[1])); print(s.acquire("mock","other-process")); s.close()'
            env=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parents[1]/'src'))
            child=subprocess.run([sys.executable,'-c',code,str(self.path)],env=env,capture_output=True,text=True,timeout=20)
            self.assertEqual(child.returncode,0,child.stderr);self.assertEqual(child.stdout.strip(),'False')
            return {'verification':asdict(VerificationResult('uncertain',[],'fixture',observation_sha256=observation.sha256))}
        asyncio.run(life.execute_goal('observe',post_audit=audit,run_kind='benchmark'))

    def test_audit_cannot_attach_a_different_observation(self):
        life=self.life()
        async def audit(result,observation):
            return {'verification':asdict(VerificationResult('verified',[],'wrong',observation_sha256='another-image'))}
        result=asyncio.run(life.execute_goal('observe',post_audit=audit))
        self.assertEqual(result.reason_code,'AUDIT_ERROR');self.assertEqual(result.status,'failed')

    def test_audit_exception_releases_and_persists_failure(self):
        life=self.life()
        async def audit(*args):raise RuntimeError('audit fixture')
        result=asyncio.run(life.execute_goal('observe',post_audit=audit))
        self.assertEqual(result.reason_code,'AUDIT_ERROR')
        self.assertEqual(self.store.task(result.task_run_id)['status'],'failed')
        self.assertEqual(self.store.conn.execute('SELECT COUNT(*) FROM lease').fetchone()[0],0)

    def test_cancel_during_audit_releases_lease(self):
        life=self.life()
        async def scenario():
            entered=asyncio.Event()
            async def audit(*args):entered.set();await asyncio.sleep(10)
            task=asyncio.create_task(life.execute_goal('observe',post_audit=audit))
            await entered.wait();task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
        asyncio.run(scenario())
        self.assertEqual(self.store.conn.execute('SELECT COUNT(*) FROM lease').fetchone()[0],0)
        self.assertEqual(self.store.conn.execute('SELECT status FROM task_runs').fetchone()[0],'failed')

    def test_audit_api_timeout_releases_and_backs_off_in_own_namespace(self):
        self.store.set_wake('mock',12345,'normal')
        life=self.life()
        async def audit(*args):raise APIError('API_TIMEOUT','fixture')
        result=asyncio.run(life.execute_goal('observe',post_audit=audit,run_kind='benchmark'))
        self.assertEqual(result.reason_code,'API_TIMEOUT')
        self.assertEqual(self.store.next_wake('mock'),12345)
        self.assertGreater(self.store.next_wake('mock',run_kind='benchmark'),time.time()+590)
        self.assertEqual(self.store.conn.execute('SELECT COUNT(*) FROM lease').fetchone()[0],0)

    def test_total_device_session_deadline_includes_audit(self):
        life=self.life(session_timeout=.05)
        async def audit(*args):await asyncio.sleep(5)
        result=asyncio.run(life.execute_goal('observe',post_audit=audit))
        self.assertEqual(result.reason_code,'SESSION_TIMEOUT')
        self.assertEqual(self.store.conn.execute('SELECT COUNT(*) FROM lease').fetchone()[0],0)

    def test_autonomous_agent_cannot_cross_control_during_benchmark_audit(self):
        life=self.life();other_gui=SequenceGui([TAP])
        other=PhoneLife(self.store,'mock',MovingPhone(),MockPlanner(),other_gui,
            ActionPolicy('mock',frozenset({'com.android.chrome'})),action_delay=0)
        async def audit(result,observation):
            self.assertEqual((await other.run_once(force=True))['status'],'busy')
            self.assertEqual(other_gui.calls,[])
            return {'verification':asdict(VerificationResult('uncertain',[],'fixture',observation_sha256=observation.sha256))}
        asyncio.run(life.execute_goal('observe',run_kind='benchmark',post_audit=audit))

    def test_benchmark_and_manual_do_not_pollute_life_memory_or_schedule(self):
        self.store.remember('mock','episode','life memory')
        self.store.set_wake('mock',12345,'normal')
        for source in ('benchmark','manual_targeted'):
            result=asyncio.run(self.life().execute_goal('test-only goal',run_kind=source))
            self.assertEqual(self.store.task(result.task_run_id)['run_kind'],source)
            self.assertEqual(self.store.recent_memories('mock'),['life memory'])
            self.assertEqual(self.store.next_wake('mock'),12345)
            self.assertGreater(self.store.count_events('mock',run_kind=source),0)
        self.store.close();self.store=Store(self.path)
        self.assertEqual(self.store.next_wake('mock'),12345)
        self.assertEqual(self.store.recent_memories('mock'),['life memory'])

    def test_mock_test_has_separate_memory_and_wake(self):
        life=self.life(run_kind='mock_test')
        class RestPlanner:
            async def decide(self,*args):return Decision('rest','','test',30)
        life.planner=RestPlanner()
        result=asyncio.run(life.run_once(force=True))
        self.assertEqual(result['run_kind'],'mock_test')
        self.assertEqual(self.store.recent_memories('mock'),[])
        self.assertEqual(len(self.store.recent_memories('mock',run_kind='mock_test')),1)
        self.assertEqual(self.store.next_wake('mock'),0)

    def test_autonomous_planner_only_receives_autonomous_namespace(self):
        self.store.remember('mock','episode','life memory')
        asyncio.run(self.life().execute_goal('repeated benchmark task',run_kind='benchmark'))
        class Capture:
            memories=None
            async def decide(self,description,memories,event_count):
                self.memories=memories;return Decision('rest','','test',30)
        capture=Capture();life=self.life();life.planner=capture
        result=asyncio.run(life.run_once(force=True))
        self.assertEqual(capture.memories,['life memory'])
        self.assertEqual(result['status'],'rested')
        self.assertGreater(self.store.next_wake('mock'),time.time())

    def test_crashed_process_lease_expires_and_new_executor_acquires(self):
        code='from pathlib import Path; from phonelife.storage import Store; import sys,os; s=Store(Path(sys.argv[1])); assert s.acquire("mock","crashed",1); os._exit(17)'
        env=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parents[1]/'src'))
        child=subprocess.run([sys.executable,'-c',code,str(self.path)],env=env,timeout=20)
        self.assertEqual(child.returncode,17)
        self.assertFalse(self.store.acquire('mock','new'))
        time.sleep(1.05)
        self.assertTrue(self.store.acquire('mock','new'));self.store.release('mock','new')

    def test_expired_owner_cannot_renew_or_release_new_owners_lease(self):
        self.assertTrue(self.store.acquire('mock','old',.01));time.sleep(.02)
        with self.assertRaises(RuntimeError):self.store.renew('mock','old')
        self.assertTrue(self.store.acquire('mock','new'))
        self.store.release('mock','old')
        self.assertEqual(self.store.conn.execute('SELECT owner FROM lease').fetchone()[0],'new')
        self.store.release('mock','new')


def legacy_database(path):
    c=sqlite3.connect(path)
    c.executescript('''
      CREATE TABLE task_runs(task_run_id TEXT PRIMARY KEY,device TEXT,goal TEXT,status TEXT,schema_version TEXT,started_wall_utc TEXT,started_monotonic_ns INTEGER,ended_wall_utc TEXT,elapsed_ns INTEGER,metadata TEXT,result TEXT);
      CREATE TABLE action_attempts(attempt_id TEXT PRIMARY KEY,task_run_id TEXT,step_index INTEGER,generation_attempt INTEGER,payload TEXT);
      CREATE TABLE observations(observation_id TEXT PRIMARY KEY,task_run_id TEXT,metadata TEXT,evidence_path TEXT);
      CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT,device TEXT,event_type TEXT,payload TEXT,created_at REAL);
      CREATE TABLE memories(id INTEGER PRIMARY KEY AUTOINCREMENT,device TEXT,kind TEXT,summary TEXT,created_at REAL);
      CREATE TABLE schedule(device TEXT PRIMARY KEY,next_wake_at REAL,last_result TEXT);
      CREATE TABLE lease(device TEXT PRIMARY KEY,owner TEXT,expires_at REAL);
      INSERT INTO task_runs VALUES('old','mock','legacy','completed','1.1','old',123,'old',1,'{}','{}');
      INSERT INTO memories VALUES(1,'mock','episode','unclassified legacy',123);
      INSERT INTO schedule VALUES('mock',12345,'completed');
      INSERT INTO events VALUES(18,'mock','outcome','{"status":"completed"}',123);
      PRAGMA user_version=11;
    ''');c.commit();return c


class ReadonlyMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'legacy.db'
        self.writer=legacy_database(self.path)

    def tearDown(self):self.writer.close();self.tmp.cleanup()

    def test_real_replay_does_not_change_file_schema_or_logical_content(self):
        before=self.path.read_bytes();dump='\n'.join(self.writer.iterdump())
        with patch.object(Store,'_migrate',side_effect=AssertionError('No migration')):
            reader=Store.open_readonly(self.path)
            try:
                result=replay_task(reader,'old')
                with self.assertRaises(sqlite3.OperationalError):reader.conn.execute('DELETE FROM memories')
            finally:reader.close()
        self.assertEqual(result['interpreted_status'],'legacy_unverified')
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual('\n'.join(self.writer.iterdump()),dump)
        self.assertEqual(self.writer.execute('PRAGMA user_version').fetchone()[0],11)

    def test_missing_database_does_not_create_parent_or_file(self):
        missing=Path(self.tmp.name)/'missing'/'state.db'
        with self.assertRaises(FileNotFoundError):Store.open_readonly(missing)
        self.assertFalse(missing.parent.exists())

    def test_wal_replay_reads_committed_wal_without_changing_database_or_wal(self):
        self.writer.execute('PRAGMA journal_mode=WAL')
        self.writer.execute("UPDATE task_runs SET goal='committed WAL goal' WHERE task_run_id='old'");self.writer.commit()
        wal=Path(str(self.path)+'-wal');before=(self.path.read_bytes(),wal.read_bytes())
        reader=Store.open_readonly(self.path)
        try:self.assertEqual(reader.task('old')['goal'],'committed WAL goal')
        finally:reader.close()
        self.assertEqual((self.path.read_bytes(),wal.read_bytes()),before)

    def test_cli_replay_never_constructs_model_or_device_or_write_store(self):
        args=SimpleNamespace(print_schema=False,replay='old')
        before=self.path.read_bytes()
        with patch.dict(os.environ,{'PHONE_LIFE_DB':str(self.path)}),patch.object(Store,'__init__',side_effect=AssertionError('No write store')),patch.object(cli,'AdbPhone',side_effect=AssertionError('No device')),patch.object(cli,'ChatAPI',side_effect=AssertionError('No API')):
            with patch('builtins.print'):self.assertEqual(asyncio.run(cli.run(args)),0)
        self.assertEqual(self.path.read_bytes(),before)

    def test_migration_keeps_legacy_rows_null_and_does_not_reclassify(self):
        originals={table:(list(self.writer.execute(f'PRAGMA table_info({table})')),self.writer.execute(f'SELECT * FROM {table}').fetchall()) for table in ('events','memories','schedule','task_runs')}
        store=Store(self.path)
        try:
            for table,(columns,rows) in originals.items():
                names=','.join(c[1] for c in columns)
                self.assertEqual([tuple(r) for r in store.conn.execute(f'SELECT {names} FROM {table}')],rows)
            self.assertIsNone(store.task('old')['run_kind'])
            self.assertEqual(store.recent_memories('mock'),[])
            self.assertEqual(store.recent_memories('mock',run_kind=None),['unclassified legacy'])
            self.assertEqual(store.conn.execute('PRAGMA user_version').fetchone()[0],12)
        finally:store.close()


if __name__=='__main__':unittest.main()
