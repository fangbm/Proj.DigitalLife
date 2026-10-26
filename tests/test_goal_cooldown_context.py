"""offline_fixture: continuation eligibility must agree with repeat protection."""
import json,time
from dataclasses import asdict
from unittest.mock import patch
from test_autonomy import Fixture,ContextBrain,decision,goal
from phonelife.autonomy import AutonomousLife
from phonelife.models import utc_now
from phonelife.request_context import compact_context
from phonelife.storage import Store

class GoalCooldownContextTests(Fixture):
    def setup_history(self,times,*,history_kind='autonomous',history_device='mock'):
        self.chosen=goal('Read Android version',{'kind':'readonly_fact','facts':['android_version']})
        self.brain=ContextBrain(decision(kind='rest'));self.life=self.agent(self.brain)
        for i,finished in enumerate(times):
            rid='history-'+str(i);self.store.start_task(rid,history_device,'prior bounded fact',{},run_kind=history_kind)
            self.store.finish_task(rid,{'status':'exploration_observed','summary':'offline observation'},0,'offline')
            self.store.conn.execute('insert into life_cycles(task_run_id,device,run_kind,summary) values(?,?,?,?)',
                (rid,history_device,history_kind,json.dumps({'contract_key':AutonomousLife.contract_key(self.chosen),'finished_at':finished})))
        self.store.conn.execute('insert into life_goals values(?,?,?,?,?,?,?)',
            (self.chosen.goal_id,'mock','autonomous',json.dumps(asdict(self.chosen)),'partial','history-0',utc_now()))
    def entry(self):return next(g for g in self.brain.contexts[-1].unfinished_goals if g['goal_id']==self.chosen.goal_id)
    def test_active_repeat_cooldown_is_advertised_as_ineligible(self):
        now=time.time();self.setup_history([now-50,now-20]);self.cycle(self.life)
        item=self.entry();self.assertFalse(item['can_continue'])
        self.assertEqual(item['continuation_reason'],'REPEATED_GOAL_COOLDOWN')
        self.assertGreater(item['repeat_cooldown_remaining_seconds'],3500)
        self.assertTrue(item['current_capability_available']);self.assertEqual(item['remaining_action_budget'],12)
    def test_single_prior_attempt_still_allows_continuation(self):
        self.setup_history([time.time()-20]);self.cycle(self.life)
        self.assertTrue(self.entry()['can_continue']);self.assertEqual(self.entry()['repeat_cooldown_remaining_seconds'],0)
    def test_cooldown_expires_without_rest_renewing_it(self):
        now=time.time();self.setup_history([now-3590,now-20])
        with patch('phonelife.autonomy.time.time',return_value=now):self.cycle(self.life)
        self.assertFalse(self.entry()['can_continue']);self.assertEqual(self.entry()['repeat_cooldown_remaining_seconds'],10)
        with patch('phonelife.autonomy.time.time',return_value=now+11):self.cycle(self.life)
        self.assertTrue(self.entry()['can_continue']);self.assertEqual(self.entry()['repeat_cooldown_remaining_seconds'],0)
    def test_other_run_kind_does_not_block_current_goal(self):
        self.setup_history([time.time()-50,time.time()-20],history_kind='mock_test');self.cycle(self.life)
        self.assertTrue(self.entry()['can_continue'])
    def test_new_goal_cannot_bypass_original_repeat_guard(self):
        self.setup_history([time.time()-50,time.time()-20]);self.brain.chosen=decision(self.chosen)
        result=self.cycle(self.life);self.assertEqual(result['status'],'rested')
        self.assertEqual(self.life.autonomy.forced_reason,'REPEATED_GOAL_COOLDOWN')
        self.assertGreater(result['next_wake_at']-time.time(),590);self.assertEqual(self.life.phone.actions,[])
    def test_reopen_and_compaction_keep_actual_eligibility(self):
        self.setup_history([time.time()-50,time.time()-20]);self.store.close();self.store=Store(self.path)
        self.life=self.agent(self.brain);self.cycle(self.life)
        item=next(g for g in compact_context(self.brain.contexts[-1]).unfinished_goals if g['goal_id']==self.chosen.goal_id)
        self.assertFalse(item['can_continue']);self.assertGreater(item['repeat_cooldown_remaining_seconds'],3500)
