"""One bounded, leased recovery per task; no old input or control reference replay."""
import asyncio
import time
from .semantic_stability import trusted
from .devices import DeviceTransportError


class SemanticRecoveryController:
    async def recover(self, life, run_id, owner, observation):
        if trusted(life.semantic_observation) or life.phone.kind!='emulator':
            return observation
        if life._semantic_recovery_used or life._transport_unknown:
            return observation
        life.leases.renew(life.name,owner,life.lease_seconds)  # Must already own a live lease.
        life._semantic_recovery_used=True
        trace={'task_run_id':run_id,'initial_observation_id':observation.observation_id,
               'initial_failure':life.semantic_observation.attempts,'maximum_launches':1,
               'model_actions':0,'recovery_actions':0,'started_monotonic_ns':time.monotonic_ns()}
        life.store.event(life.name,'semantic_recovery_started',trace)
        async def body():
            nonlocal observation
            # New capture honors each existing backend cooldown, never resets it.
            observation=await life._observe(run_id)
            if trusted(life.semantic_observation):
                trace['state']='recovered_readonly';return
            foreground=await life.phone.foreground()
            action=life.capabilities.recovery_action(life.phone.kind,foreground,life.semantic_observation)
            life.policy.check(action,observation)
            if life._life_active and life.autonomy.operator_error():
                raise life.autonomy.operator_error()
            life.leases.renew(life.name,owner,life.lease_seconds)
            life._use_capability('launch_allowed_app')
            trace.update(state='dispatch_outcome_unknown',action=action.wire())
            life.store.event(life.name,'semantic_recovery_dispatch',trace)
            # Mark uncertainty before sending; interruption never permits an automatic resend.
            life._transport_unknown=True
            await life.phone.reopen_settings()
            life._transport_unknown=False
            trace.update(recovery_actions=1,state='launch_confirmed')
            await asyncio.sleep(.3)
            observation=await life._observe(run_id)
            trace['state']='recovered_after_launch' if trusted(life.semantic_observation) else 'stopped_unavailable'
        try:
            await asyncio.wait_for(body(),45)
        except DeviceTransportError:
            trace.update(state='stopped_outcome_unknown',error_code='ADB_OUTCOME_UNKNOWN')
            from .llm import APIError
            raise APIError('ADB_OUTCOME_UNKNOWN','Recovery launch outcome unknown; no resend') from None
        except Exception as error:
            trace.update(state='stopped',error_code=getattr(error,'code',type(error).__name__))
        finally:
            trace.update(final_observation_id=observation.observation_id,
                         final_quality=life.semantic_observation.evidence_quality,
                         final_page=life.capabilities.view(life.semantic_observation)['known_page_labels'],
                         finished_monotonic_ns=time.monotonic_ns(),business_goal_succeeded=False)
            life.store.event(life.name,'semantic_recovery_finished',trace)
        return observation
