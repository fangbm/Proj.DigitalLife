"""offline_fixture only: real HTTP boundary is replaced in memory, never a live claim."""
import asyncio
import hashlib
import io
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from acceptance.continuation import ProbeChatAPI,error_projection,main,missing_candidate
from phonelife.autonomy import AutonomousLife
from phonelife.brain import APILifeBrain,MockLifeGui,validate_references
from phonelife.budgets import LifeLimits
from phonelife.decision_errors import DecisionContractError
from phonelife.devices import MockPhone
from phonelife.life_models import LifeDecision
from phonelife.life_storage import enable_life
from phonelife.llm import ChatAPI,MockPlanner
from phonelife.loop import PhoneLife
from phonelife.memory import LifeMemory
from phonelife.models import utc_now
from phonelife.policy import ActionPolicy
from phonelife.storage import Store

REST={'kind':'rest','candidates':[],'selected_goal_id':None,'reason':'Offline provider fixture response','wake_after_seconds':30}

class ProbeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'state.sqlite3'
        self.store=Store(self.path);self.leases=Store(Path(self.temp.name)/'lease.sqlite3')
        self.requests=[]
    async def asyncTearDown(self):
        self.store.close();self.leases.close();self.temp.cleanup()
    def http(self,request,timeout=None):
        self.requests.append(request.data)
        return io.BytesIO(json.dumps({'id':str(uuid.uuid4()),'model':'offline-provider',
            'choices':[{'message':{'content':json.dumps(REST)},'finish_reason':'stop'}]}).encode())
    def agent(self,inject=False,limits=None):
        api=ProbeChatAPI('https://api.deepseek.com','offline-secret-key',store=self.store,
            run_id=lambda:self.controller.budget.run_id,inject=inject)
        self.controller=AutonomousLife(self.store,'emulator',APILifeBrain(api,'offline-provider','private fixture persona'),limits)
        self.phone=MockPhone()
        life=PhoneLife(self.store,'emulator',self.phone,MockPlanner(),MockLifeGui(),
            ActionPolicy('mock',frozenset()),autonomy=self.controller,lease_store=self.leases,action_delay=0)
        return life,api
    async def cycle(self,life):
        with patch('urllib.request.urlopen',side_effect=self.http):return await life.run_once(force=True)
    def events(self):
        return [json.loads(r[0]) for r in self.store.conn.execute("SELECT payload FROM events WHERE event_type='acceptance_api_exchange' ORDER BY id")]
    def no_lease(self):self.assertEqual(self.leases.conn.execute('SELECT COUNT(*) FROM lease').fetchone()[0],0)

    async def test_entry_default_off_creates_no_store_network_or_device(self):
        with (patch('phonelife.storage.Store',side_effect=AssertionError('No DB')),
              patch.object(ChatAPI,'generate',side_effect=AssertionError('No API'))):
            self.assertEqual(main([]),0)

    async def test_injected_json_passes_schema_but_fails_reference(self):
        from types import SimpleNamespace
        d=LifeDecision.from_dict(json.loads(missing_candidate({'now':utc_now()})))
        with self.assertRaises(DecisionContractError) as caught:
            validate_references(d,SimpleNamespace(unfinished_goals=[]))
        self.assertEqual(caught.exception.code,'MISSING_CONTINUATION_REFERENCE')

    async def test_two_real_boundary_calls_no_extra_repair_goal_or_gui(self):
        life,api=self.agent(True);result=await self.cycle(life)
        self.assertEqual(result['reason_code'],'MISSING_CONTINUATION_REFERENCE')
        self.assertEqual(len(self.requests),2);self.assertEqual(api.replacements,2)
        self.assertFalse(api.inject);self.assertEqual(self.phone.actions,[])
        self.assertEqual(self.store.conn.execute('SELECT COUNT(*) FROM life_goals').fetchone()[0],0)
        errors=result['life_decision_rejections']
        self.assertEqual([e['generation_attempts'] for e in errors],[1,2])
        self.assertEqual([e['repairs_used'] for e in errors],[0,1])
        self.assertTrue(all(e['executed'] is False and e['confirmed_actions']==0 for e in errors))
        self.no_lease()

    async def test_transport_request_hash_matches_actual_http_bytes(self):
        life,_=self.agent(True);await self.cycle(life)
        for event,body in zip(self.events(),self.requests):
            self.assertEqual(event['request_payload_sha256'],hashlib.sha256(body).hexdigest())
            user=json.loads(body)['messages'][1]['content'][0]['text']
            self.assertEqual(event['request_user_sha256'],hashlib.sha256(user.encode()).hexdigest())
            row=self.store.conn.execute('SELECT status FROM life_requests WHERE request_id=?',(event['request_id'],)).fetchone()
            self.assertEqual(row[0],'received')

    async def test_close_reopen_new_adapter_fields_reach_actual_boundary(self):
        first,_=self.agent(True);source=await self.cycle(first)
        self.store.close();self.store=Store(self.path)
        second,api=self.agent();result=await self.cycle(second)
        self.assertEqual(result['status'],'rested');self.assertEqual(api.replacements,0)
        wire=self.events()[-1];self.assertFalse(wire['injection_enabled']);self.assertFalse(wire['substituted'])
        errors=wire['context_errors'];self.assertEqual(len(errors),2)
        originals=self.store.task(source['task_run_id'])['result']['life_decision_rejections']
        for item,original in zip(errors,originals):
            for field in ('reason_code','field','expected','source_task_run_id','candidate_sha256','executed','confirmed_actions'):
                self.assertEqual(item[field],original[field])
            self.assertEqual(item['source_task_run_id'],source['task_run_id'])
        self.no_lease()

    async def test_same_instance_disarms_after_only_one_injected_cycle(self):
        life,api=self.agent(True);await self.cycle(life);result=await self.cycle(life)
        self.assertEqual(result['status'],'rested');self.assertEqual(api.replacements,2)
        self.assertFalse(self.events()[-1]['substituted'])

    async def test_restart_cannot_rearm_consumed_source(self):
        life,_=self.agent(True);await self.cycle(life)
        self.store.close();self.store=Store(self.path)
        with self.assertRaises(ValueError):self.agent(True)

    async def test_source_isolation_excludes_mock_test_episodes(self):
        enable_life(self.store)
        run=str(uuid.uuid4());self.store.start_task(run,'emulator','Offline source',{},run_kind='mock_test')
        LifeMemory(self.store,'emulator','mock_test').add('episodic','isolation-marker',{'marker':'must-not-reach-live-context'},run)
        life,_=self.agent();await self.cycle(life)
        sent=self.requests[-1].decode()
        self.assertNotIn('must-not-reach-live-context',sent);self.assertNotIn('isolation-marker',sent)

    async def test_existing_request_cap_can_prevent_second_injection(self):
        life,api=self.agent(True,LifeLimits(max_goal_requests=1));result=await self.cycle(life)
        self.assertEqual(result['reason_code'],'API_BUDGET_EXHAUSTED')
        self.assertEqual(len(self.requests),1);self.assertEqual(api.replacements,1)
        self.assertEqual(self.phone.actions,[]);self.no_lease()

    async def test_api_timeout_never_substitutes_or_retries_and_releases(self):
        life,api=self.agent(True)
        with patch('urllib.request.urlopen',side_effect=TimeoutError) as upstream:
            result=await life.run_once(force=True)
        self.assertEqual(result['reason_code'],'API_TIMEOUT');self.assertEqual(upstream.call_count,1)
        self.assertEqual(api.replacements,0);self.assertEqual(self.events()[0]['status'],'failed_or_cancelled')
        self.no_lease()

    async def test_cancellation_journals_failure_and_releases_lease(self):
        life,_=self.agent(True);started=asyncio.Event()
        async def slow(*args,**kwargs):started.set();await asyncio.Future()
        with patch.object(ChatAPI,'generate',side_effect=slow):
            task=asyncio.create_task(life.run_once(force=True));await started.wait();task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(self.events()[0]['error_code'],'CancelledError');self.no_lease()
        self.assertEqual(self.store.conn.execute("SELECT COUNT(*) FROM task_runs WHERE status='running'").fetchone()[0],0)

    async def test_audit_io_failure_does_not_leave_lease_or_execute_goal(self):
        life,_=self.agent(True);original=self.store.event
        def failing(device,kind,payload):
            if kind=='acceptance_api_exchange':raise OSError('offline journal failure')
            return original(device,kind,payload)
        with patch.object(self.store,'event',side_effect=failing):await self.cycle(life)
        self.assertEqual(self.phone.actions,[]);self.no_lease()

    async def test_exchange_ledger_excludes_secrets_and_full_prompts(self):
        life,_=self.agent(True);await self.cycle(life)
        content=json.dumps(self.events())
        self.assertNotIn('offline-secret-key',content);self.assertNotIn('private fixture persona',content)
        self.assertNotIn('All screenshots',content);self.assertNotIn('messages',content)

    async def test_missing_budget_reservation_refuses_before_network(self):
        _,api=self.agent(True)
        with patch.object(ChatAPI,'generate',side_effect=AssertionError('No API')):
            with self.assertRaises(RuntimeError):await api.generate('offline','system','{}')

if __name__=='__main__':unittest.main()
