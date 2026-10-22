"""One leased, audited lifecycle for autonomous and manually targeted tasks."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import asdict, replace

from .audit import EvidenceSink, redact, response_audit, scrub
from .devices import DeviceTransportError
from .llm import APIError, ModelOutputError, parse_object
from .models import Action, ActionRecord, ExecutionResult, ValidationError, VerificationResult, utc_now
from .policy import PolicyDenied
from .progress import ActionProgressDetector, StagnationGuard
from .verification import AboutPhoneVerifier, ConservativeVerifier, IndependentVisionVerifier, MockVerifier


class PhoneLife:
    def __init__(self, store, device_name, phone, planner, gui, policy,
                 step_limit=15, action_delay=1.0, verifier=None, evidence_dir=None,
                 run_kind="autonomous", session_timeout=600, lease_seconds=120, autonomy=None, lease_store=None,
                 semantic_provider=None):
        self.store, self.name, self.phone = store, device_name, phone
        self._lease_store = lease_store
        self.planner, self.gui, self.policy = planner, gui, policy
        self.step_limit, self.action_delay, self.verifier = step_limit, action_delay, verifier
        self.evidence = EvidenceSink(evidence_dir, phone.kind)
        self._observation_count = 0
        if run_kind not in self.store.RUN_KINDS or not 0 < session_timeout <= 600 or not 0 < lease_seconds <= 600:
            raise ValueError("Invalid run kind or bounded device session budget")
        self.run_kind, self.session_timeout, self.lease_seconds = run_kind, session_timeout, lease_seconds
        self.progress_detector = ActionProgressDetector()
        self.semantic_provider, self.semantic_observation = semantic_provider, None
        self.capability_usage = {}
        self._semantic_recovery_used, self._transport_unknown = False, False
        if semantic_provider:
            from .semantic_storage import enable_semantics
            from .capabilities import CapabilityRegistry
            enable_semantics(store)
            self.capabilities = getattr(phone,'capabilities',None) or CapabilityRegistry()
            self.gui.semantic_context_provider = lambda: self.capabilities.view(self.semantic_observation) if self.semantic_observation else None
        self.autonomy, self._life_active = autonomy, False
        if autonomy:
            if autonomy.kind != run_kind or autonomy.device != device_name:
                raise ValueError('Autonomous controller provenance/device mismatch')
            autonomy.attach(self)

    @property
    def leases(self):
        # Preserve programmatic reopen compatibility when no separate coordinator was supplied.
        return self._lease_store or self.store

    def _use_capability(self, capability):
        count=self.capability_usage.get(capability,0)
        if count>=self.capabilities.entries[capability].maximum_usage:
            raise PolicyDenied('Capability usage budget exhausted: '+capability)
        self.capability_usage[capability]=count+1

    async def _observe(self, run_id):
        if self.semantic_provider:
            self._use_capability('observe_screen')
            self._use_capability('inspect_controls')
        observation = await self.phone.observe()
        self._observation_count += 1
        path = self.evidence.save(run_id, f"observation-{self._observation_count:04d}", observation)
        self.store.observation(run_id, observation, path)
        if self.semantic_provider:
            from .semantic_storage import record_semantics
            self.semantic_observation = await self.semantic_provider.collect(self.phone, observation)
            record_semantics(self.store, run_id, self.semantic_observation, self.evidence.directory)
            view=self.capabilities.view(self.semantic_observation)
            self.store.event(self.name,'semantic_page',{'task_run_id':run_id,
                'observation_id':observation.observation_id,'screenshot_sha256':observation.sha256,
                'quality':view['evidence_quality'],'known_page_labels':view['known_page_labels'],
                'content_sha256':self.semantic_observation.content_sha256})
        if self._life_active:
            self.autonomy.on_observation(observation)
        return observation

    async def _perform(self, goal: str, run_id: str, owner: str) -> ExecutionResult:
        history: list[ActionRecord] = []
        guard = StagnationGuard()
        progress_feedback = None
        def result(status, summary, code="", verification=None, observation=None):
            return ExecutionResult(status, summary, len(history), code, run_id, verification,
                                   observation.sha256 if observation else "")

        for step in range(1, self.step_limit + 1):
            observation = await self._observe(run_id)
            if self.semantic_provider:
                observation=await self._recover_semantics(run_id,owner,observation)
            semantic = self.semantic_observation
            feedback, format_repairs, coordinate_repairs, reference_repairs = progress_feedback, 0, 0, 0
            for generation in range(1, 4):
                self.leases.renew(self.name, owner, self.lease_seconds)
                started = time.monotonic_ns()
                attempt_id = self.store.attempt(run_id, step, generation, {
                    "task_run_id": run_id, "step_index": step, "generation_attempt": generation,
                    "observation_id": observation.observation_id, "observation_sha256": observation.sha256,
                    "width": observation.width, "height": observation.height,
                    "wall_time_utc": utc_now(), "monotonic_ns": started,
                    "validation_status": "pending", "execution_status": "not_executed",
                    "provider": getattr(getattr(self.gui, "api", None), "provider", "offline"),
                    "model": getattr(self.gui, "model", "unknown"), "field_errors": [], 'feedback_to_model':feedback})
                response = None
                try:
                    response = await self.gui.propose(goal, observation,
                        [{**r.action.wire(), "execution_status": r.execution_status} for r in history], feedback)
                    audit = response_audit(response.content)
                    self.store.update_attempt(attempt_id, **audit, api_request_id=response.api_request_id,
                                              provider=response.provider, model=response.model)
                    if response.finish_reason != "stop":
                        raise ModelOutputError("Model output was incomplete")
                    candidate = parse_object(response.content, allow_fences=False)
                    self.store.update_attempt(attempt_id, candidate_action=candidate)
                    binding = None
                    if self.semantic_provider:
                        action, binding = self.capabilities.ground(candidate, semantic)
                        self.store.update_attempt(attempt_id, semantic_binding=binding, proposed_intent=candidate)
                    else:
                        action = Action.from_dict(candidate)
                    action = action.bind(observation)
                    self.policy.check(action, observation)
                    if action.type != "finish":
                        current = await self._observe(run_id)
                        current_semantic = self.semantic_observation
                        if (current.width, current.height) != (observation.width, observation.height):
                            raise ValidationError([{"code": "STALE_OBSERVATION", "field": "screen",
                                "value": f"{observation.width}x{observation.height}",
                                "expected": f"current pixels {current.width}x{current.height}"}])
                        if self.semantic_provider:
                            checked = self.capabilities.revalidate(binding, semantic, self.semantic_observation)
                            self.store.update_attempt(attempt_id, semantic_binding=checked)
                        if self._life_active:
                            operator_error = self.autonomy.operator_error()
                            if operator_error:
                                raise operator_error
                            capability = checked if self.semantic_provider else await self.autonomy.gate.check(self.phone, action, current)
                            self.store.event(self.name, 'autonomous_capability', {
                                'task_run_id': run_id, 'attempt_id': attempt_id, **capability})
                except (ValidationError, ModelOutputError) as error:
                    errors = getattr(error, "errors", [{"code": "INVALID_JSON", "field": "$",
                                                        "value": None, "expected": str(error)}])
                    self.store.update_attempt(attempt_id, validation_status="invalid", field_errors=errors,
                                              generation_elapsed_ns=time.monotonic_ns() - started)
                    from .capabilities import GroundingError
                    reference = isinstance(error, GroundingError)
                    coordinate = isinstance(error, ValidationError) and error.coordinate_error
                    if reference:
                        if reference_repairs >= 1 or generation >= 3:
                            return result('failed', str(error), 'REFERENCE_REPAIR_EXHAUSTED', observation=observation)
                        reference_repairs += 1
                        observation = await self._observe(run_id)
                        semantic = self.semantic_observation
                    elif coordinate:
                        if coordinate_repairs >= 1 or generation >= 3:
                            return result("failed", "Coordinate repair budget exhausted", "INVALID_COORDINATE", observation=observation)
                        coordinate_repairs += 1
                        # Reobserve before regenerating a coordinate-bound action. Never replay the prior action.
                        observation = await self._observe(run_id)
                        semantic = self.semantic_observation
                    else:
                        if format_repairs >= 2 or generation >= 3:
                            return result("failed", "Model output repair budget exhausted", "MODEL_OUTPUT_INVALID", observation=observation)
                        format_repairs += 1
                    feedback = {"field_errors": scrub(errors), "previous_response_excerpt":
                        response_audit(response.content)["model_response_excerpt"] if response else "",
                        "instruction": "No action was executed for this candidate. Generate one NEW complete JSON action."}
                    continue
                except PolicyDenied as error:
                    self.store.update_attempt(attempt_id, validation_status="policy_denied",
                                              field_errors=[{"code": error.code, "field": "action", "expected": str(error)}],
                                              generation_elapsed_ns=time.monotonic_ns() - started)
                    self.store.event(self.name, "policy_denied", {"task_run_id": run_id,
                        "attempt_id": attempt_id, "action": candidate.get("type"), "why": str(error)})
                    if getattr(error,'skip_action',False):
                        feedback={'code':'ACTION_RISK_SKIPPED','previous_action_executed':False,
                            'instruction':str(error)+'; choose another safe control, open_app, back, or finish uncertain.'}
                        if generation<3:continue
                        return result('uncertain','No safe candidate within bounded repair budget','ACTION_RISK_SKIPPED',observation=observation)
                    return result("blocked", str(error), error.code, observation=observation)
                except Exception as error:
                    self.store.update_attempt(attempt_id, validation_status="request_failed",
                        error_code=getattr(error, "code", "MODEL_REQUEST_ERROR"),
                        error=redact(str(error)), generation_elapsed_ns=time.monotonic_ns() - started)
                    raise

                self.store.update_attempt(attempt_id, validation_status="valid",
                    candidate_action=action.wire(), bound_observation_id=action.observation_id,
                    generation_elapsed_ns=time.monotonic_ns() - started)
                if action.type == "finish":
                    final = await self._observe(run_id)
                    if action.outcome == "uncertain":
                        verification = VerificationResult("uncertain", [], "Executor declared uncertainty",
                            "executor_stop", final.sha256)
                        status, code = "uncertain", "EXECUTOR_UNCERTAIN"
                    elif action.outcome == "cannot_proceed":
                        verification = VerificationResult("not_met", [], "Executor cannot proceed", "executor_stop", final.sha256)
                        status, code = "blocked", "EXECUTOR_CANNOT_PROCEED"
                    else:
                        verifier = self.verifier
                        if verifier is None:
                            verifier = (MockVerifier(self.phone) if self.phone.kind == "mock" else
                                AboutPhoneVerifier(self.phone, IndependentVisionVerifier(self.gui.api, self.gui.model))
                                if AboutPhoneVerifier.supports(goal) and hasattr(self.gui, "api") else ConservativeVerifier())
                            if self.semantic_provider:
                                from .goal_contracts import KNOWN_PAGES, consistent
                                from .verification_registry import GoalVerifierRegistry
                                from types import SimpleNamespace
                                verifier=ConservativeVerifier()
                                for title in sorted(KNOWN_PAGES,key=len,reverse=True):
                                    spec={'kind':'page','package':'com.android.settings','title':title}
                                    if consistent(goal,spec)[0]:
                                        verifier=GoalVerifierRegistry(self.phone,semantic_provider=self.semantic_provider).bind(
                                            SimpleNamespace(description=goal,verification=spec),require_consistency=True)
                                        break
                        verification = await verifier.verify(goal, final, history)
                        if verification.verdict == "verified" and verification.observation_sha256 != final.sha256:
                            verification = VerificationResult("uncertain", [], "Verifier evidence bound to another observation",
                                verification.evidence_type, final.sha256)
                        status = "succeeded" if verification.verdict == "verified" else "uncertain"
                        if self._life_active and verification.verdict in {'exploration_observed', 'novelty_detected', 'goal_partially_met'}:
                            status = 'partially_met' if verification.verdict == 'goal_partially_met' else 'exploration_observed'
                        code = "GOAL_VERIFIED" if status == "succeeded" else "GOAL_NOT_VERIFIED"
                    self.store.event(self.name, "verification", {"task_run_id": run_id, **asdict(verification)})
                    return result(status, verification.reason, code, verification, final)

                if guard.should_stop(action):
                    self.store.update_attempt(attempt_id, validation_status="stagnation_blocked",
                                              ineffective_dispatches=guard.ineffective)
                    self.store.event(self.name, "stagnation_blocked", {"task_run_id": run_id,
                        "attempt_id": attempt_id, "ineffective_dispatches": guard.ineffective})
                    return result("failed", "Repeated action without reliable content progress", "STAGNATION", observation=observation)
                self.leases.renew(self.name, owner, self.lease_seconds)
                if self.semantic_provider:
                    self._use_capability(binding['capability'])
                if hasattr(self.phone,'prepare_dispatch'):
                    try:
                        self.phone.prepare_dispatch(action,current_semantic,checked,self.store,self.leases,run_id,owner,attempt_id,self.step_limit)
                    except (PolicyDenied,PermissionError,ValueError) as error:
                        self.store.update_attempt(attempt_id,execution_status='not_executed',validation_status='policy_denied',error_code='PHYSICAL_DISPATCH_DENIED')
                        return result('blocked',str(error),'PHYSICAL_DISPATCH_DENIED',observation=observation)
                # Persist the conservative state BEFORE dispatch. A process death cannot cause blind replay.
                self.store.update_attempt(attempt_id, execution_status="uncertain_after_transport_error",
                                          dispatch_wall_utc=utc_now(), dispatch_monotonic_ns=time.monotonic_ns())
                try:
                    device_result = await self.phone.act(action)
                except PolicyDenied as error:
                    # Physical preflight failed before the sole input call; honest no-dispatch audit.
                    self.store.update_attempt(attempt_id,execution_status='not_executed',validation_status='policy_denied',error_code='PHYSICAL_PRE_DISPATCH_DENIED')
                    return result('blocked',str(error),'PHYSICAL_PRE_DISPATCH_DENIED',observation=observation)
                except (DeviceTransportError, RuntimeError, OSError) as error:
                    self._transport_unknown=True
                    self.store.update_attempt(attempt_id, transport_error=redact(str(error)))
                    try:
                        final = await self._observe(run_id)
                        self.store.event(self.name, "transport_reobserved", {"task_run_id": run_id,
                            "attempt_id": attempt_id, "observation_sha256": final.sha256})
                    except Exception:
                        final = observation
                    return result("uncertain", "Device action may have occurred; no automatic resend",
                                  "ADB_OUTCOME_UNKNOWN", observation=final)
                self.store.update_attempt(attempt_id, execution_status="executed",
                                          execution_result=redact(device_result, 200),
                                          finished_monotonic_ns=time.monotonic_ns())
                history.append(ActionRecord(attempt_id, step, action, observation.sha256, "executed"))
                self.store.event(self.name, "device_action", {"task_run_id": run_id, "attempt_id": attempt_id,
                    "step_index": step, "type": action.type, "action": action.wire(), "observation": redact(device_result, 200)})
                if self._life_active:
                    self.autonomy.on_action(action)
                if self.action_delay:
                    await asyncio.sleep(self.action_delay)
                post = await self._observe(run_id)
                if self.phone.kind != "mock":
                    await asyncio.sleep(.2)
                settled = await self._observe(run_id)
                assessment = self.progress_detector.assess(action, (observation, current), (post, settled))
                if self.semantic_provider and action.type in {'tap','swipe'}:
                    from .semantic import semantic_content_progress
                    semantic_progress=getattr(self.capabilities,'content_progress',semantic_content_progress)(current_semantic,self.semantic_observation)
                    self.store.update_attempt(attempt_id,semantic_progress=semantic_progress)
                    if assessment.verdict=='progressed' and not semantic_progress['changed']:
                        assessment=replace(assessment,verdict='stagnant' if semantic_progress['reliable'] else 'uncertain',
                            reason='Pixel change lacks corresponding reliable semantic content progress')
                guard.record(action, assessment)
                self.store.update_attempt(attempt_id, progress=asdict(assessment))
                self.store.event(self.name, "action_progress", {"task_run_id": run_id,
                    "attempt_id": attempt_id, **asdict(assessment)})
                progress_feedback={'previous_action_executed':True,'progress':asdict(assessment),
                    'instruction':'Measured feedback for the executed previous action. If no progress, change intent/direction or stop. '
                    'toward_end reveals lower content; toward_start reveals upper content. Do not repeat ineffective inputs.'}
                break
        final = await self._observe(run_id)
        return result("step_limit", "Reached per-goal executed action budget", "STEP_LIMIT", observation=final)

    async def _recover_semantics(self,run_id,owner,observation):
        from .semantic_recovery import SemanticRecoveryController
        return await SemanticRecoveryController().recover(self,run_id,owner,observation)

    async def _managed(self, *, goal=None, force=False, owner=None, prepare=None,
                       run_kind=None, post_audit=None) -> dict:
        run_kind = run_kind or self.run_kind
        life_cycle = self.autonomy is not None and goal is None and run_kind == self.autonomy.kind
        if run_kind not in self.store.RUN_KINDS:
            raise ValueError("Unknown run_kind")
        due = self.store.next_wake(self.name, run_kind=run_kind)
        if due > time.time() and not force:
            return {"status": "sleeping", "next_wake_at": due, "run_kind": run_kind}
        run_id = str(uuid.uuid4())
        owner = owner or getattr(self.phone,'preheld_lease_owner',None) or run_id
        if not self.leases.acquire(self.name, owner, self.lease_seconds):
            return {"status": "busy", "reason_code": "DEVICE_BUSY", "reason": "Another process owns the device lease"}
        started, interrupt, decision, audit_phase = False, None, None, False
        wake_after, self._observation_count = 300, 0
        self.capability_usage = {}
        self._semantic_recovery_used, self._transport_unknown = False, False
        self._session_owner=owner
        lease_errors, control_errors = [], []
        original_verifier, original_step_limit = self.verifier, self.step_limit
        controlling_task = asyncio.current_task()

        async def heartbeat():
            last_renewal = time.monotonic()
            while True:
                await asyncio.sleep(min(.25 if life_cycle else 30, self.lease_seconds / 3))
                try:
                    if time.monotonic() - last_renewal >= min(30, self.lease_seconds / 3):
                        self.leases.renew(self.name, owner, self.lease_seconds)
                        last_renewal = time.monotonic()
                except Exception as error:
                    lease_errors.append(error)
                    controlling_task.cancel()
                    return
                if life_cycle:
                    error = self.autonomy.operator_error()
                    if error:
                        control_errors.append(error)
                        controlling_task.cancel()
                        return

        async def body():
            nonlocal started, goal, decision, wake_after, audit_phase
            self.store.start_task(run_id, self.name, goal or "", {
                "mode": "targeted" if goal is not None else "autonomous", "step_limit": self.step_limit,
                "session_timeout_seconds": self.session_timeout, "lease_seconds": self.lease_seconds,
                "max_generations_per_step": 3, "max_coordinate_repairs": 1,
                "device_serial": getattr(self.phone, "serial", "mock"), "gui_model": getattr(self.gui, "model", "unknown"),
                "allowed_apps": sorted(self.policy.allowed_apps), "device_kind": self.phone.kind}, run_kind=run_kind)
            started = True
            if self.semantic_provider:
                self.store.event(self.name,'capability_registry',{'task_run_id':run_id,**self.capabilities.wire()})
            if life_cycle:
                self._life_active = True
                self.autonomy.begin(self, run_id)
            if prepare:
                setup = await prepare()
                self.store.event(self.name, "test_setup", {"task_run_id": run_id, "setup": setup})
            if goal is None:
                decision = (await self.autonomy.plan(self) if life_cycle else
                    await self.planner.decide(await self.phone.describe(),
                        self.store.recent_memories(self.name, run_kind=run_kind),
                        self.store.count_events(self.name, run_kind=run_kind)))
                wake_after = decision.wake_after_seconds
                goal = decision.goal
                self.store.update_goal(run_id, goal)
                self.store.event(self.name, "decision", {"task_run_id": run_id, "kind": decision.kind,
                    "goal": goal, "reason": decision.reason})
            if decision and decision.kind == "rest":
                result = ExecutionResult("rested", "The agent chose to rest", 0, "REST_DECISION", run_id)
            else:
                result = await self._perform(goal, run_id, owner)
            if life_cycle and result.reason_code == 'ADB_OUTCOME_UNKNOWN':
                result = replace(result,status='needs_review')
            if life_cycle:
                await self.autonomy.reflect(result)
            if post_audit:
                audit_phase = True
                self.leases.renew(self.name, owner, self.lease_seconds)
                final = await self._observe(run_id)
                self.store.event(self.name, "post_audit_started", {"task_run_id": run_id,
                    "observation_id": final.observation_id, "observation_sha256": final.sha256})
                audit = await post_audit(result, final)
                verdict = audit.get("verification", {})
                if verdict.get("observation_sha256") != final.sha256:
                    raise ValueError("Post-audit evidence does not match its leased observation")
                audit = {**audit, "task_run_id": run_id, "observation_id": final.observation_id,
                    "observation_sha256": final.sha256, "task_result_observation_sha256": result.final_observation_sha256,
                    "primary_status": result.status, "lease_protected": True}
                self.store.event(self.name, "post_audit", {"task_run_id": run_id, **audit})
                result = replace(result, post_audit=audit)
                if result.status == "succeeded" and verdict.get("verdict") != "verified":
                    result = replace(result, status="uncertain", reason_code="POST_AUDIT_NOT_VERIFIED",
                                     summary="Independent post-audit did not confirm the primary result")
                audit_phase = False
            return result

        from .semantic_storage import COLLECTION_SINK, record_additional_semantics
        journal_token=COLLECTION_SINK.set(lambda semantic:record_additional_semantics(self.store,self.leases,
            self.name,owner,run_id,semantic,self.evidence.directory,self.lease_seconds)) if self.semantic_provider else None
        heartbeat_task = asyncio.create_task(heartbeat())
        try:
            # This deadline includes setup, execution, final screenshots and post-audit.
            result = await asyncio.wait_for(body(), timeout=self.session_timeout)
        except BaseException as error:
            if not started:
                raise
            if not isinstance(error, Exception) and not lease_errors and not control_errors:
                interrupt = error
            code = (control_errors[0].code if control_errors else "LEASE_LOST" if lease_errors else "SESSION_TIMEOUT" if isinstance(error, TimeoutError)
                    else getattr(error, "code", "TASK_INTERRUPTED" if interrupt else "AUDIT_ERROR" if audit_phase else "EXECUTION_ERROR"))
            result = ExecutionResult("blocked" if isinstance(error, PolicyDenied) else "failed", redact(f"{type(error).__name__}: {error}", 500), 0, code, run_id)
            if life_cycle and hasattr(error, 'until'):
                self.autonomy.wake_override = error.until
            attempts = self.store.task(run_id)["attempts"]
            executed = sum(a["payload"].get("execution_status") == "executed" for a in attempts)
            if life_cycle:
                unknown = self._transport_unknown or any(a['payload'].get('execution_status') == 'uncertain_after_transport_error' for a in attempts)
                if unknown or interrupt or control_errors or lease_errors:
                    result = replace(result, status='needs_review' if unknown or executed else 'interrupted')
                    self.store.event(self.name, 'life_interruption', {'task_run_id': run_id,
                        'cause': code, 'dispatch_outcome_unknown': unknown, 'confirmed_actions': executed,
                        'old_actions_replayed': 0})
            last = self.store.conn.execute(
                "SELECT metadata FROM observations WHERE task_run_id=? ORDER BY rowid DESC LIMIT 1", (run_id,)).fetchone()
            final_hash = json.loads(last[0])["sha256"] if last else ""
            if not interrupt and not lease_errors and not control_errors and code != "SESSION_TIMEOUT":
                try:
                    final_hash = (await self._observe(run_id)).sha256
                except Exception:
                    pass
            result = replace(result, steps=executed, final_observation_sha256=final_hash,
                post_audit={"failed": True, "phase": "post_audit", "error_code": code} if audit_phase else None)
        finally:
            try:
                if started:
                    if result.status in {"failed", "blocked", "interrupted", "needs_review"} or result.reason_code == "ADB_OUTCOME_UNKNOWN":
                        wake_after = max(wake_after, 600)
                    wake = time.time() + wake_after
                    if life_cycle:
                        wake = max(wake, time.time() + self.autonomy.limits.min_rest_seconds, self.autonomy.wake_override)
                    payload = asdict(result)
                    payload.update({"goal": goal or "", "decision": decision.kind if decision else "explore",
                                    "next_wake_at": wake, "run_kind": run_kind})
                    if self.semantic_provider:
                        payload['capability_usage']=dict(self.capability_usage)
                    if life_cycle:
                        payload['life_decision_rejections']=self.autonomy.decision_rejections
                        payload['decision'] = self.autonomy.decision.kind if self.autonomy.decision else 'unavailable'
                        payload['life_contract_version'] = '1.2'
                    self.store.finish_task(run_id, payload, wake,
                        f"Goal: {goal or 'rest'}; outcome={result.status}; {result.summary}",
                        finalizer=(lambda: self.autonomy.finalize(payload)) if life_cycle else None)
            finally:
                heartbeat_task.cancel()
                try:
                    await asyncio.gather(heartbeat_task, return_exceptions=True)
                finally:
                    try:
                        if journal_token is not None:COLLECTION_SINK.reset(journal_token)
                    finally:self.leases.release(self.name, owner)
                    if life_cycle:
                        self._life_active = False
                        self.verifier = original_verifier
                        self.step_limit = original_step_limit
                        self.autonomy.end()
        if interrupt:
            raise interrupt
        return payload

    async def run_once(self, force=False) -> dict:
        return await self._managed(force=force, run_kind=self.run_kind)

    async def execute_goal(self, goal: str, owner: str | None = None, *, prepare=None,
                           run_kind="manual_targeted", post_audit=None) -> ExecutionResult:
        payload = await self._managed(goal=goal, force=True, owner=owner, prepare=prepare,
                                      run_kind=run_kind, post_audit=post_audit)
        if payload["status"] == "busy":
            return ExecutionResult("blocked", payload["reason"], 0, "DEVICE_BUSY")
        verification = VerificationResult(**payload["verification"]) if payload.get("verification") else None
        return ExecutionResult(payload["status"], payload["summary"], payload["steps"], payload["reason_code"],
            payload["task_run_id"], verification, payload["final_observation_sha256"], payload.get("post_audit"))

    async def run_forever(self):
        while True:
            until = self.store.next_wake(self.name, run_kind=self.run_kind) - time.time()
            if until > 0:
                await asyncio.sleep(min(until, 60))
                continue
            outcome = await self.run_once()
            print(json.dumps(outcome, ensure_ascii=False), flush=True)
            if outcome["status"] == "busy":
                await asyncio.sleep(15)
