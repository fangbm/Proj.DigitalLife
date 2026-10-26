"""offline_fixture: failure-anchored cooldown; isolated SQLite, no ADB or API."""
import asyncio
from datetime import datetime,timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from phonelife.autonomy import AutonomousLife
from phonelife.budgets import LifeLimits
from phonelife.devices import MockPhone
from phonelife.life_state import LifeState
from phonelife.llm import MockPlanner
from phonelife.loop import PhoneLife
from phonelife.policy import ActionPolicy
from phonelife.storage import Store
import test_autonomy as fixture

class FailureCooldownTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'state.sqlite3';self.store=Store(self.path);self.addCleanup(lambda:self.store.close())
        self.base=int(time.time());self.brain=fixture.ContextBrain(fixture.decision(kind='rest'));self.life=self.agent()
    def agent(self):
        controller=AutonomousLife(self.store,'mock',self.brain,LifeLimits(min_rest_seconds=1))
        return PhoneLife(self.store,'mock',MockPhone(),MockPlanner(),fixture.MockLifeGui(),ActionPolicy('mock',frozenset({'com.android.settings'})),autonomy=controller,action_delay=0)
    def history(self,run,ended,*,kind='autonomous',device='mock',status='failed'):
        self.store.start_task(run,device,'Synthetic failure evidence',{},run_kind=kind)
        with patch('phonelife.storage.utc_now',return_value=datetime.fromtimestamp(ended,timezone.utc).isoformat()):
            self.store.finish_task(run,{'status':status,'reason_code':'OFFLINE_FAILURE'},0,'offline fixture')
        if kind in {'autonomous','mock_test'}:
            self.store.conn.execute('INSERT INTO life_cycles(task_run_id,device,run_kind,summary) VALUES(?,?,?,?)',(run,device,kind,json.dumps({'finished_at':ended})))
    def legacy(self,*,failure_at,last_update=None):
        self.history('source-failure',failure_at)
        data=LifeState(consecutive_failures=3,cycles=19,last_update=failure_at if last_update is None else last_update).wire()
        data.pop('last_failure_at',None)
        self.store.conn.execute('INSERT OR REPLACE INTO life_states VALUES(?,?,?)',('mock','autonomous',json.dumps(data)))
    def cycle(self,now):
        with patch('phonelife.autonomy.time.time',return_value=now):return asyncio.run(self.life.run_once(force=True))
    def test_old_failure_model_rest_does_not_start_new_failure_cooldown(self):
        self.legacy(failure_at=self.base-3*86400)
        self.cycle(self.base);self.cycle(self.base+74)
        self.assertEqual(len(self.brain.contexts),2,'A normal rest must not turn a three-day-old failure into a new 526-second cooldown')
        self.assertNotEqual(self.life.autonomy.forced_reason,'FAILURE_COOLDOWN')
        self.assertEqual(self.life.phone.actions,[])
    def test_recent_failure_wait_does_not_renew_and_expires_at_original_deadline(self):
        self.legacy(failure_at=self.base-100)
        self.cycle(self.base);self.assertEqual(self.life.autonomy.decision.wake_after_seconds,500)
        self.cycle(self.base+200);self.assertEqual(self.life.autonomy.decision.wake_after_seconds,300)
        self.cycle(self.base+500);self.assertEqual(len(self.brain.contexts),1)
        self.assertEqual(self.life.autonomy.state.consecutive_failures,3)
        self.assertFalse(self.brain.contexts[-1].internal_state['failure_cooldown_active'])
    def test_legacy_reconstruction_ignores_rest_timestamps_other_sources_and_devices(self):
        failure_at=self.base-86400;self.legacy(failure_at=failure_at,last_update=self.base-1)
        self.history('later-rest',self.base-1,status='rested')
        self.history('benchmark-failure',self.base,kind='benchmark')
        self.history('manual-failure',self.base,kind='manual_targeted')
        self.history('other-device',self.base,device='other')
        original=list(self.store.conn.iterdump());state=self.life.autonomy.load_state()
        self.assertEqual(state.last_failure_at,failure_at);self.assertEqual(list(self.store.conn.iterdump()),original)
    def test_close_reopen_and_subprocess_keep_original_failure_clock(self):
        self.legacy(failure_at=self.base-100);self.cycle(self.base)
        self.store.close();self.store=Store(self.path);self.life=self.agent()
        self.cycle(self.base+500);self.assertEqual(len(self.brain.contexts),1)
        script=('import json,sys;from pathlib import Path;from phonelife.storage import Store;from phonelife.autonomy import AutonomousLife;'
                'from phonelife.brain import MockLifeBrain;s=Store(Path(sys.argv[1]));'
                'v=AutonomousLife(s,"mock",MockLifeBrain()).load_state();print(json.dumps(v.wire()));s.close()')
        value=json.loads(subprocess.check_output([sys.executable,'-c',script,str(self.path)],text=True,timeout=20))
        self.assertEqual(value['last_failure_at'],self.base-100);self.assertEqual(value['consecutive_failures'],3)
    def test_new_failure_anchors_new_cooldown_rest_and_observation_do_not(self):
        state=LifeState(consecutive_failures=2,last_update=100)
        state.evolve(now=200,status='failed',actions=0,api_calls=0,novelty=False,repeated=False)
        self.assertEqual(state.last_failure_at,200)
        for status in ('rested','uncertain','exploration_observed'):
            state.evolve(now=300,status=status,actions=0,api_calls=0,novelty=False,repeated=False)
            self.assertEqual(state.last_failure_at,200)
        state.evolve(now=400,status='blocked',actions=0,api_calls=0,novelty=False,repeated=False)
        self.assertEqual(state.last_failure_at,400)
        state.evolve(now=500,status='succeeded',actions=0,api_calls=0,novelty=True,repeated=False)
        self.assertEqual(state.consecutive_failures,0);self.assertIsNone(state.last_failure_at)
    def test_legacy_without_source_evidence_uses_one_conservative_fallback(self):
        data=LifeState(consecutive_failures=3,last_update=self.base-100).wire();data.pop('last_failure_at',None)
        self.store.conn.execute('INSERT INTO life_states VALUES(?,?,?)',('mock','autonomous',json.dumps(data)))
        self.cycle(self.base);self.assertEqual(self.life.autonomy.decision.wake_after_seconds,500)
        self.cycle(self.base+200);self.assertEqual(self.life.autonomy.decision.wake_after_seconds,300)
        self.assertEqual(self.life.autonomy.state.last_failure_at,self.base-100)
