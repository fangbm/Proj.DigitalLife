"""Model-led autonomous lifecycle inside PhoneLife's existing protected session."""
from __future__ import annotations

import hashlib
import asyncio
import json
import time
import uuid
from datetime import datetime
from dataclasses import asdict, replace

from .semantic_stability import trusted
from .autonomy_policy import AutonomousActionGate
from .budgets import LifeLimits, MeteredAPI, RequestBudget
from .life_models import LifeContext, LifeDecision, GoalCandidate
from .life_state import LifeState, clamp
from .life_storage import control_mode, enable_life
from .memory import LifeMemory
from .models import Decision, utc_now
from .policy import PolicyDenied
from .progress import ActionProgressDetector
from .recovery import recover_interrupted
from .verification_registry import GoalVerifierRegistry


def confirmed_action_units(payload):
    """Count actual fixed commands for compound all-app actions, including restart.

    Old/strict attempts remain one unit. New search typing plus Enter records two
    confirmed subdispatches; it cannot gain another allowance via continuation.
    Unknown dispatches are not counted as confirmed and remain review-blocked.
    """
    if payload.get('execution_status')!='executed':return 0
    execution=payload.get('app_execution');subdispatch=payload.get('app_subdispatch')
    if isinstance(execution,dict) and execution.get('scope')=='installed_launchable' and isinstance(subdispatch,dict):
        units=subdispatch.get('confirmed_commands')
        if type(units)is int and 1<=units<=2:return units
    return 1


class AutonomousLife:
    def __init__(self, store, device, brain, limits=None, run_kind='autonomous'):
        enable_life(store)
        if run_kind not in {'autonomous', 'mock_test'}:
            raise ValueError('Autonomy requires autonomous or isolated mock_test provenance')
        self.store, self.device, self.brain, self.kind = store, device, brain, run_kind
        from .life_subject import scope,predicate
        self.devices=scope(store,device,run_kind);self.scope=predicate(self.devices)
        binding=getattr(store,'life_binding',None)
        self.state_key=binding.namespace if binding and run_kind=='autonomous' else device
        self.limits = limits or LifeLimits()
        self.budget = RequestBudget(store, device, self.limits, run_kind)
        self.memory = LifeMemory(store, device, run_kind)
        self.gate = AutonomousActionGate()
        self.started = time.monotonic()
        self.run_id = ''

    def attach(self, life):
        self.gate=getattr(life.phone,'goal_gate',self.gate)
        if getattr(life.phone,'app_scope',None):self.memory.environment_device=self.device
        life.step_limit = min(life.step_limit, self.limits.max_actions)
        life.session_timeout = min(life.session_timeout, self.limits.task_timeout_seconds)
        api = getattr(life.gui, 'api', None)
        if api:
            base = api.api if isinstance(api, MeteredAPI) else api
            life.gui.api = MeteredAPI(base, self.budget, 'gui')
            verify_api = MeteredAPI(base, self.budget, 'verification')
        else:
            verify_api = None
        if hasattr(self.brain, 'api'):
            base = self.brain.api.api if isinstance(self.brain.api, MeteredAPI) else self.brain.api
            self.brain.api = MeteredAPI(base, self.budget, 'brain')
            self.brain.reflection_api = MeteredAPI(base, self.budget, 'reflection')
        self.registry = GoalVerifierRegistry(life.phone, verify_api, getattr(life.gui, 'model', ''), life.semantic_provider,
            life._use_capability if life.semantic_provider else None)
        life.gui.life_context_provider = lambda: {
            'verification': self.decision.selected.verification if getattr(self, 'decision', None) and self.decision.selected else None,
            'read_only_navigation_controls': sorted(self.gate.settings_routes),
            'text_entry': 'search_only' if getattr(life.phone,'app_scope',None) else 'disabled', 'unknown_controls': 'denied'} if self.run_id else None

    def load_state(self):
        row = self.store.conn.execute('SELECT state FROM life_states WHERE device=? AND run_kind=?', (self.state_key, self.kind)).fetchone()
        state=LifeState(**json.loads(row[0])) if row else LifeState()
        if state.consecutive_failures and state.last_failure_at is None:
            # Read-only JSON compatibility: state may predate last_failure_at.
            # Only finalized life cycles of this subject/provenance can anchor it;
            # later rests, targeted tests and unrelated devices must not renew it.
            failure=self.store.conn.execute(f'''SELECT ended_wall_utc FROM task_runs WHERE {self.scope}
                AND run_kind=? AND status IN ('failed','blocked','step_limit','interrupted','needs_review')
                AND ended_wall_utc IS NOT NULL AND task_run_id IN (SELECT task_run_id FROM life_cycles)
                ORDER BY julianday(ended_wall_utc) DESC, rowid DESC LIMIT 1''',(*self.devices,self.kind)).fetchone()
            if failure:
                try:
                    ended=datetime.fromisoformat(failure[0].replace('Z','+00:00'))
                    if ended.tzinfo is not None:state.last_failure_at=ended.timestamp()
                except (ValueError,OverflowError,OSError):pass
            if state.last_failure_at is None:state.last_failure_at=state.last_update or time.time()
        return state

    async def review_startup(self, life):
        pending = self.store.conn.execute("SELECT COUNT(*) FROM task_runs WHERE device=? AND status='running'",(self.device,)).fetchone()[0]
        if not pending:
            return {'pending':0,'old_actions_replayed':0}
        if self.operator_error():
            return {'pending':pending,'deferred':'operator_control','old_actions_replayed':0}
        run_id = str(uuid.uuid4())
        if not life.leases.acquire(self.device,run_id,120):
            return {'pending':pending,'deferred':'device_busy','old_actions_replayed':0}
        wake = self.store.next_wake(self.device,run_kind=self.kind)
        started = False
        try:
            self.store.start_task(run_id,self.device,'Startup interruption review',{'mode':'startup_review','life_contract_version':'1.2'},run_kind=self.kind)
            started = True
            recovery = recover_interrupted(self.store,self.device,run_id)
            observation = await asyncio.wait_for(life._observe(run_id),timeout=min(30,life.session_timeout))
            result = {'status':'rested','reason_code':'STARTUP_REVIEW','summary':'Interrupted history reviewed; fresh screen captured; no old actions replayed',
                      'steps':0,'task_run_id':run_id,'verification':None,'final_observation_sha256':observation.sha256,
                      'recoveries':recovery,'old_actions_replayed':0,'run_kind':self.kind,'next_wake_at':wake}
        except BaseException as error:
            if started:
                wake = max(wake,time.time()+600)
                self.store.finish_task(run_id,{'status':'interrupted','reason_code':'STARTUP_REVIEW_INCOMPLETE',
                    'summary':type(error).__name__,'steps':0,'task_run_id':run_id,'old_actions_replayed':0},wake,
                    'Startup recovery requires review; no GUI action was replayed')
            raise
        else:
            self.store.finish_task(run_id,result,wake,result['summary'])
            return {'pending':pending,'review_task':self.store.task(run_id),'old_actions_replayed':0}
        finally:
            life.leases.release(self.device,run_id)

    def begin(self, life, run_id):
        self.run_id = self.budget.run_id = run_id
        self.last_run_id = run_id
        self.decision = self.reflection = self.context = self.last_observation = None
        self.reflection_error = ''
        self.decision_rejections=[]
        self.wake_override = 0
        self.forced_reason = ''
        self.state = self.load_state()
        binding=getattr(self.store,'life_binding',None)
        if binding and self.kind=='autonomous':binding.bind_run(self.store,run_id)
        self.recoveries = recover_interrupted(self.store, self.device, run_id)
        for recovery in self.recoveries:
            if recovery['run_kind'] == self.kind:
                self.memory.add('episodic', 'Interrupted previous task',
                    {'objective': {'status': recovery['status'], 'reason_code': 'PROCESS_INTERRUPTED'},
                     'recovery': recovery}, recovery['task_run_id'], .9, 'interruption_review')
        self.store.conn.execute('INSERT INTO life_cycles(task_run_id,device,run_kind) VALUES(?,?,?)', (run_id, self.device, self.kind))

    def on_observation(self, observation):
        self.last_observation = observation
        self.memory.working(self.run_id, current_screen=observation.metadata())

    def on_action(self, action):
        self.memory.working(self.run_id, last_confirmed_action=action.wire(), next_intent='await_new_observation')

    def operator_error(self):
        mode = control_mode(self.store, self.device)
        if mode != 'running':
            from .llm import APIError
            return APIError('OPERATOR_' + mode.upper(), 'Autonomous operator control: ' + mode)
        return None

    def forced_rest(self, reason, seconds):
        self.forced_reason = reason
        self.decision = LifeDecision('rest', [], None, reason, max(self.limits.min_rest_seconds, min(86400, int(seconds))))
        self.store.event(self.device, 'life_guard_rest', {'task_run_id': self.run_id, 'reason': reason})
        return Decision('rest', '', reason, self.decision.wake_after_seconds)

    @staticmethod
    def contract_key(candidate):
        spec = candidate.verification
        value = spec if spec['kind'] not in {'observe', 'visual'} else {'description': candidate.description.strip().casefold(), **spec}
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def repeated_goal_cooldown(self,candidate,now=None):
        # Same 12 summaries, two occurrences and one-hour window as the dispatch
        # admission rule. Expose its result before asking the brain to choose.
        now=time.time() if now is None else now
        signature=self.contract_key(candidate)
        previous=[json.loads(r[0]) for r in self.store.conn.execute(f'SELECT summary FROM life_cycles WHERE {self.scope} AND run_kind=? AND summary IS NOT NULL ORDER BY rowid DESC LIMIT 12',(*self.devices,self.kind))]
        matches=sorted((p['finished_at'] for p in previous if p.get('contract_key')==signature and p.get('finished_at',0)>now-3600),reverse=True)
        return max(0,matches[1]+3600-now) if len(matches)>=2 else 0

    async def plan(self, life):
        # A fresh observation is mandatory after any interrupted task, never old-action replay.
        obs = await life._observe(self.run_id)
        if life.semantic_provider:
            obs=await life._recover_semantics(self.run_id,life._session_owner,obs)
        foreground = await life.phone.foreground()
        description = await life.phone.describe()
        unfinished=[]
        for row in self.store.conn.execute(f"SELECT goal_id,candidate,status,source_run FROM life_goals WHERE {self.scope} AND run_kind=? AND status IN ('partial','proposed') ORDER BY updated_at DESC LIMIT 8", (*self.devices,self.kind)):
            candidate=json.loads(row['candidate'])
            count,ancestor,seen=0,dict(row),set()
            while ancestor and ancestor['source_run'] not in seen:
                seen.add(ancestor['source_run'])
                if ancestor['status']!='proposed':
                    count+=sum(confirmed_action_units(json.loads(a[0])) for a in self.store.conn.execute(
                        'SELECT payload FROM action_attempts WHERE task_run_id=?',(ancestor['source_run'],)))
                parent=json.loads(ancestor['candidate']).get('continuation_of')
                ancestor=self.store.conn.execute(f'SELECT goal_id,candidate,status,source_run FROM life_goals WHERE goal_id=? AND {self.scope} AND run_kind=?',
                    (parent,*self.devices,self.kind)).fetchone() if parent else None
            # A continuation never receives a fresh copy of the spent action allowance.
            remaining=max(0,self.limits.max_actions-count)
            def needs_gui(spec):
                return spec['kind'] in {'page','controls','about_phone','new_scroll_content'} or any(needs_gui(s) for s in spec.get('checks',[]))
            foreground_ok=(not needs_gui(candidate['verification']) or not life.semantic_provider or
                (self.registry._usable(life.semantic_observation) and life.semantic_observation.foreground_package in self.gate.packages))
            permitted=candidate['risk_class']=='low' and not self.gate.unsafe.search(candidate['description'])
            def allowed_scope(spec):
                return ('package' not in spec or spec['package'] in self.gate.packages) and all(allowed_scope(s) for s in spec.get('checks',[]))
            permitted=permitted and allowed_scope(candidate['verification'])
            repeat_wait=self.repeated_goal_cooldown(GoalCandidate.from_dict(candidate))
            unfinished.append({**candidate,'summary':candidate['description'][:160],'status':row['status'],
                'remaining_action_budget':remaining,'can_continue':bool(remaining and foreground_ok and permitted and repeat_wait<=0),
                'continuation_reason':'REPEATED_GOAL_COOLDOWN' if repeat_wait>0 else 'Persisted unfinished goal; remaining budget and current evidence checked',
                'repeat_cooldown_remaining_seconds':repeat_wait,
                'current_capability_available':bool(foreground_ok and permitted)})
        state_view = self.state.wire()
        now=time.time()
        elapsed = max(0, now-self.state.last_update) if self.state.last_update else 0
        failure_cooldown=self.state.failure_cooldown_remaining(now,self.limits.consecutive_failures)
        state_view.update(failure_cooldown_remaining_seconds=failure_cooldown,failure_cooldown_active=failure_cooldown>0)
        state_view['energy'] = clamp(self.state.energy + min(.12, elapsed/3600*.15))
        interests = self.memory.interests()
        query = ' '.join(i['topic'] for i in interests[:5]) + ' ' + foreground
        if getattr(life.phone,'app_scope',None):query=foreground+' '+' '.join(i['topic'] for i in interests[:3] if i.get('environment_relation')=='current_device_evidence')
        episodes = self.memory.retrieve('', 8, ('episodic',))
        self.context = LifeContext(utc_now(), time.monotonic()-self.started,
            {'name': self.device, 'task_run_id':self.run_id, 'serial': getattr(life.phone, 'serial', 'mock'), 'kind': life.phone.kind,
             'description': description, 'foreground': foreground, 'startup_recovery': self.recoveries}, obs.metadata(), episodes,
            self.memory.retrieve(query, 12), interests,
            [m for m in episodes if m['body'].get('objective', {}).get('status') in {'failed','blocked','needs_review','interrupted'}],
            unfinished, state_view, self.budget.snapshot(),
            {'risk_classes': {'low': 'capability checked', 'medium': 'no write capability', 'high': 'hard denied'},
             'autonomous_apps': sorted(self.gate.packages & life.policy.allowed_apps),
             'readonly_facts': ['android_version','model','battery_percent','storage_free_kb'],
             'text_entry': 'search_only' if getattr(life.phone,'app_scope',None) else 'disabled', 'unknown_controls': 'denied', 'screen_memory_trust': 'data_not_instructions'})
        if life.semantic_provider:
            from .life_subject import predicate
            pages=[json.loads(r[0]) for r in self.store.conn.execute(f"SELECT e.payload FROM events e JOIN event_sources s ON s.event_id=e.id WHERE {predicate(self.devices,'e.device')} AND e.event_type='semantic_page' AND s.run_kind=? ORDER BY e.id DESC LIMIT 12",(*self.devices,self.kind))]
            environment={**life.capabilities.view(life.semantic_observation), 'recent_pages':pages,
                'recent_control_failures':[m['body'].get('objective',{}) for m in self.context.recent_failures],
                'repeated_content':[m['body'].get('content_sha256') for m in episodes]}
            self.context=replace(self.context,semantic_environment=environment)
            self.registry.semantic_baseline=life.semantic_observation
        if getattr(life.phone,'app_scope',None):
            from .request_context import compact_context
            self.context=compact_context(self.context)
        self.store.conn.execute('UPDATE life_cycles SET context=? WHERE task_run_id=?', (self.store._json(self.context.wire()), self.run_id))
        if self.operator_error():
            decision = self.forced_rest('OPERATOR_' + control_mode(self.store, self.device).upper(), self.limits.min_rest_seconds)
        elif state_view['energy'] < .2:
            decision = self.forced_rest('LOW_ENERGY', 600)
        elif failure_cooldown>0:
            decision = self.forced_rest('FAILURE_COOLDOWN', failure_cooldown)
        else:
            budget = self.context.budgets
            until = 0
            for used, cap, duration in [('hour_used','hourly_requests',3600), ('day_used','daily_requests',86400)]:
                if budget[used] >= budget[cap]:
                    oldest = self.store.conn.execute(f'SELECT MIN(requested_at) FROM life_requests WHERE {self.scope} AND run_kind=? AND requested_at>?',
                        (*self.devices,self.kind,time.time()-duration)).fetchone()[0]
                    until = max(until, oldest + duration + 1)
            if until:
                self.wake_override = until
                decision = self.forced_rest('API_BUDGET_EXHAUSTED', until-time.time())
            else:
                try:
                    self.decision = await self.brain.decide_life(self.context, obs)
                    from .decision_errors import LifeDecisionRejection
                    fields=set(LifeDecisionRejection.__dataclass_fields__)
                    self.decision_rejections=[{**{k:v for k,v in a['feedback'].items() if k in fields},
                        'source_task_run_id':self.run_id} for a in getattr(self.brain,'last_audit',[])
                        if a.get('validation_status')=='rejected' and fields<=set(a.get('feedback',{}))]
                except BaseException as error:
                    self.decision_rejections=[{**r,'source_task_run_id':self.run_id} for r in getattr(error,'rejections',[])]
                    self.store.event(self.device,'life_decision_rejected',{'task_run_id':self.run_id,
                        'error_code':getattr(error,'code',type(error).__name__),
                        'rejections':self.decision_rejections,
                        'audit':getattr(self.brain,'last_audit',[])})
                    raise
                # Do not trust an adapter returning an unchecked instance.
                self.decision = LifeDecision.from_dict(asdict(self.decision))
                from .brain import validate_references
                try:
                    validate_references(self.decision,self.context)
                except ValueError as error:
                    from .decision_errors import LifeDecisionRejected, rejection
                    from .audit import response_audit
                    self.decision_rejections=[rejection(error,self.context,response_audit(json.dumps(asdict(self.decision))),1,self.decision)]
                    self.decision=None
                    self.store.event(self.device,'life_decision_rejected',{'task_run_id':self.run_id,
                        'error_code':self.decision_rejections[-1]['reason_code'],'rejections':self.decision_rejections})
                    denied=PolicyDenied(str(error));denied.code=self.decision_rejections[-1]['reason_code']
                    raise denied from None
                model_decision = asdict(self.decision)
                # Candidate IDs are local to a model proposal. Persist globally unique IDs without
                # requiring a language model to manage database identities across restarts.
                bindings = {g.goal_id: str(uuid.uuid5(uuid.UUID(self.run_id), g.goal_id)) for g in self.decision.candidates}
                self.decision = replace(self.decision,
                    candidates=[replace(g, goal_id=bindings[g.goal_id]) for g in self.decision.candidates],
                    selected_goal_id=bindings.get(self.decision.selected_goal_id))
                self.store.event(self.device, 'life_model_choice', {'task_run_id': self.run_id,
                    'model_decision': model_decision, 'candidate_id_bindings': bindings,
                    'decision': asdict(self.decision), 'audit': getattr(self.brain, 'last_audit', [])})
                selected = self.decision.selected
                if selected:
                    self.gate.check_goal(selected, self.limits, {g['goal_id'] for g in unfinished})
                    if self.decision.kind == 'continue' and not selected.continuation_of:
                        raise PolicyDenied('Continue must reference a persisted unfinished goal')
                    if self.repeated_goal_cooldown(selected)>0:
                        decision = self.forced_rest('REPEATED_GOAL_COOLDOWN', 600)
                    else:
                        for candidate in self.decision.candidates:
                            self.store.conn.execute('INSERT INTO life_goals VALUES(?,?,?,?,?,?,?)',
                                (candidate.goal_id, self.device, self.kind, self.store._json(asdict(candidate)),
                                 'active' if candidate.goal_id == selected.goal_id else 'proposed', self.run_id, utc_now()))
                        life.verifier = self.registry.bind(selected, require_consistency=True)
                        if selected.continuation_of:
                            life.step_limit=min(life.step_limit,next(g['remaining_action_budget'] for g in unfinished if g['goal_id']==selected.continuation_of))
                        self.memory.working(self.run_id, current_goal=asdict(selected), next_intent='new_GUI_proposal')
                        decision = Decision('explore', selected.description, self.decision.reason, self.decision.wake_after_seconds)
                else:
                    decision = Decision('rest', '', self.decision.reason, self.decision.wake_after_seconds)
        self.store.conn.execute('UPDATE life_cycles SET decision=? WHERE task_run_id=?', (self.store._json(asdict(self.decision)), self.run_id))
        return Decision(decision.kind, decision.goal, decision.reason, max(self.limits.min_rest_seconds, decision.wake_after_seconds))

    async def reflect(self, result):
        if not self.decision or self.forced_reason or (self.decision.kind == 'rest'):
            return
        experience = {'selected_decision': asdict(self.decision), 'authoritative_execution': asdict(result),
                      'relevant_memory': self.context.relevant_memories[:8] if self.context else [],
                      'note': 'Reflection is a subjective proposal, never authoritative verification or safety policy'}
        experience['execution_trace'] = [{key: attempt['payload'].get(key) for key in
            ('candidate_action','validation_status','execution_status','field_errors','progress')}
            for attempt in self.store.task(self.run_id)['attempts']]
        try:
            self.reflection = await self.brain.reflect(experience, self.last_observation)
            from .life_models import Reflection
            self.reflection = Reflection.from_dict(asdict(self.reflection))
            data = asdict(self.reflection)
        except Exception as error:
            self.reflection_error = getattr(error, 'code', type(error).__name__)
            data = {'unavailable': True, 'error_code': self.reflection_error}
        self.store.conn.execute('UPDATE life_cycles SET reflection=? WHERE task_run_id=?', (self.store._json(data), self.run_id))
        self.store.event(self.device, 'life_reflection', {'task_run_id': self.run_id, 'subjective_proposal': data,
            'authoritative_status': result.status, 'cannot_change_verification': True,
            'audit': getattr(self.brain, 'last_audit', [])})

    def finalize(self, payload):
        run_id = self.run_id
        attempts = self.store.task(run_id)['attempts']
        actions = sum(confirmed_action_units(a['payload']) for a in attempts)
        api_calls = self.budget.snapshot()['goal_used']
        selected = self.decision.selected if self.decision else None
        verified = payload['status'] == 'succeeded' and (payload.get('verification') or {}).get('verdict') == 'verified'
        observation = self.last_observation
        body_hash = hashlib.sha256(ActionProgressDetector()._body(observation)[0]).hexdigest() if observation else ''
        evidence = (payload.get('verification') or {}).get('evidence', [])
        prior = [json.loads(r[0]) for r in self.store.conn.execute(f'SELECT summary FROM life_cycles WHERE {self.scope} AND run_kind=? AND summary IS NOT NULL ORDER BY rowid DESC LIMIT 50', (*self.devices,self.kind))]
        novelty = bool(body_hash and not any(p.get('content_sha256') == body_hash for p in prior))
        if verified and evidence:
            novelty |= not any(p.get('verified_evidence') == evidence for p in prior)
        signature = self.contract_key(selected) if selected else ''
        repeated = bool(signature and any(p.get('contract_key') == signature for p in prior))
        transition = self.state.evolve(now=time.time(), status=payload['status'], actions=actions, api_calls=api_calls,
            novelty=novelty, repeated=repeated, resting=bool(self.decision and self.decision.kind == 'rest'))
        preferences = self.memory.update_interests(self.reflection.topics if self.reflection else [], run_id,
                                                   verified=verified, novelty=novelty)
        objective = {'status': payload['status'], 'reason_code': payload['reason_code'], 'confirmed_actions': actions,
                     'decision_rejections':self.decision_rejections,
                     'api_calls': api_calls, 'verification': payload.get('verification'),
                     'detail': payload.get('summary',''),
                     'control_failures':[a['payload'].get('field_errors',[]) for a in attempts if a['payload'].get('validation_status')!='valid']}
        summary = {'objective': objective, 'goal': selected.description if selected else '', 'contract_key': signature,
                   'finished_at': time.time(),
                   'content_sha256': body_hash, 'verified_evidence': evidence if verified else [],
                   'decision_reason': self.decision.reason if self.decision else 'Decision unavailable',
                   'model_reflection': asdict(self.reflection) if self.reflection else {'unavailable': True, 'error': self.reflection_error},
                   'interest_changes': preferences, 'provenance': self.kind}
        episode_id = self.memory.add('episodic', selected.description if selected else 'Rest / reflect / guard', summary, run_id,
                                     .8 if verified else .6)
        semantic_id = None
        if verified and evidence:
            semantic_id = self.memory.add('semantic', 'Independently verified environment contract',
                            {'evidence': evidence, 'evidence_type': payload['verification']['evidence_type']}, run_id, .8)
        elif payload['reason_code']:
            semantic_id = self.memory.add('semantic', 'Measured execution limitation',
                            {'reason_code': payload['reason_code'], 'status': payload['status']}, run_id, .5, 'measured_failure_or_stop')
        if semantic_id:
            self.store.conn.execute('INSERT OR IGNORE INTO life_memory_links VALUES(?,?,?)',(episode_id,semantic_id,'supports'))
        for row in self.store.conn.execute("SELECT m.memory_id FROM life_memories m JOIN life_memory_sources s USING(memory_id) WHERE s.task_run_id=? AND m.layer='preference' LIMIT 5", (run_id,)):
            self.store.conn.execute('INSERT OR IGNORE INTO life_memory_links VALUES(?,?,?)',(episode_id,row[0],'influences_preference'))
        if selected:
            review = payload['status'] in {'needs_review','interrupted'} or payload['reason_code'] in {'ADB_OUTCOME_UNKNOWN','TASK_INTERRUPTED'}
            continuing = self.reflection is not None and self.reflection.worth_continuing
            goal_status = ('done' if verified else 'needs_review' if review else 'partial'
                if continuing and payload['status'] in {'uncertain','step_limit','exploration_observed','partially_met'}
                else 'observed' if payload['status']=='exploration_observed' else 'abandoned')
            self.store.conn.execute('UPDATE life_goals SET status=?,updated_at=? WHERE goal_id=? AND source_run=?', (goal_status,utc_now(),selected.goal_id,run_id))
            if selected.continuation_of and verified:
                self.store.conn.execute(f"UPDATE life_goals SET status='done',updated_at=? WHERE goal_id=? AND {self.scope} AND run_kind=?", (utc_now(),selected.continuation_of,*self.devices,self.kind))
        self.memory.working(run_id, task_status=payload['status'], next_intent='sleep_then_new_observation')
        consolidation = self.memory.consolidate()
        self.store.conn.execute('INSERT INTO life_states VALUES(?,?,?) ON CONFLICT(device,run_kind) DO UPDATE SET state=excluded.state', (self.state_key,self.kind,self.store._json(self.state.wire())))
        self.store.conn.execute('UPDATE life_cycles SET transition=?,summary=? WHERE task_run_id=?',
                                (self.store._json(transition),self.store._json(summary),run_id))
        self.store.event(self.device, 'life_state_transition', {'task_run_id': run_id, **transition,
                            'interest_changes': preferences, 'consolidation': consolidation})

    def end(self):
        self.budget.run_id = ''
        self.run_id = ''
