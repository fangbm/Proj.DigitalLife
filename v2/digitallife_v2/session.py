"""Observation -> one decision -> guard -> durable dispatch -> independent observation."""
import asyncio
from contextlib import suppress
import json
import os
from pathlib import Path
import time
import uuid
from .brain import LifeBrain
from .memory import LifeMemory
from .phone import PhoneController
from .safety import Blocked, DeviceLock, UnknownDispatch, guard, rebind
from .store import LifeStore
from .events import EventHub
from .bridge import NotificationBridge
from .content import ContentPerception
from .reading import ReadingStore
from .social import SocialPolicy, SocialStore, APPS as SOCIAL_APPS


class SessionStopped(Exception):
    pass


class SessionLoop:
    def __init__(self, config, directory, minutes=10, phone_factory=PhoneController, brain_factory=LifeBrain):
        self.config = config
        self.directory = Path(directory).resolve()
        self.minutes = minutes
        if not 0 < minutes <= 10:
            raise ValueError("SESSION_REQUIRES_0_TO_10_MINUTES")
        self.phone_factory, self.brain_factory = phone_factory, brain_factory
        self.store = None
        self.recent = []
        self.memory = None
        self.run_kind = "autonomous" if phone_factory is PhoneController and brain_factory is LifeBrain else "scripted" if phone_factory is PhoneController else "fixture"
        self.action_ids = []
        self.last_result_event = None
        self.bridge = None
        self.hub = None
        self.inbound_wake = asyncio.Event()

    async def poll_notifications(self):
        if self.bridge:
            try:
                if await self.bridge.poll(self.hub):self.inbound_wake.set()
                self.store.update(inbox=self.hub.counts())
            except (OSError,ValueError,asyncio.TimeoutError,EOFError):
                self.store.update(notification_error='BRIDGE_POLL_UNAVAILABLE')

    async def wait_for_event(self, seconds):
        try:await asyncio.wait_for(self.inbound_wake.wait(),seconds)
        except asyncio.TimeoutError:pass

    def check_stop(self):
        if (self.directory / "STOP").exists() or self.lock.emergency.exists():
            raise SessionStopped("STOP_REQUESTED")
        if time.monotonic() >= self.deadline:
            raise SessionStopped("GLOBAL_DEADLINE")

    async def guarded(self, awaitable):
        """Poll local stop independently of a model call, rest or ADB timeout."""
        task = asyncio.create_task(awaitable)
        heartbeat = time.monotonic()
        try:
            while not task.done():
                self.check_stop()
                await asyncio.wait({task}, timeout=.15)
                if self.store and time.monotonic() - heartbeat >= 2:
                    await self.poll_notifications()
                    self.store.update()
                    heartbeat = time.monotonic()
            self.check_stop()
            return task.result()
        finally:
            if not task.done():
                task.cancel()
            with suppress(BaseException):
                await task

    def observed(self, observation):
        self.store.event("observed", observation.summary())
        self.store.update(foreground=observation.foreground)

    async def send(self, command, action, observation):
        return await self.send_operation(lambda: self.phone.dispatch(command), action, observation)

    async def send_operation(self, operation, action, observation):
        """Journal one fixed internal operation; models never supply callables."""
        self.check_stop()
        if self.run_kind != "fixture" and self.deadline - time.monotonic() < 20:
            raise SessionStopped("OBSERVATION_TIME_RESERVE")
        if self.store.state["dispatched"] >= self.config.max_actions:
            raise SessionStopped("ACTION_BUDGET_EXHAUSTED")
        action_id = uuid.uuid4().hex
        payload = {"action_id": action_id, "session": str(self.directory), "action": action,
                   "observation_id": observation.observation_id}
        # Exclusive global marker survives process death, regardless of data-dir override.
        with self.lock.pending.open("x", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        self.store.event("pending", payload)
        try:
            self.check_stop()
        except SessionStopped:
            self.store.event("cancelled_before_dispatch", {"action_id": action_id})
            self.lock.pending.unlink()
            raise
        try:
            await self.guarded(operation())
            self.store.event("action_dispatched", {"action_id": action_id, "action": action})
            self.store.update(dispatched=self.store.state["dispatched"] + 1)
            # A freshly opened system dialog can change focus during capture.
            # Retry only the read, never the dispatched input; all other failures
            # retain the durable unknown-action barrier.
            for attempt in range(3):
                try:
                    after = await self.guarded(self.phone.observe())
                    break
                except Blocked as error:
                    if attempt == 2 or str(error) not in {'SCENE_CHANGED_DURING_OBSERVATION', 'FOREGROUND_UNVERIFIED'}:
                        raise
                    self.store.event('post_observation_retry', {'action_id': action_id, 'reason': str(error)})
                    await self.guarded(asyncio.sleep(.3))
            self.observed(after)
        except BaseException:
            self.store.event("unknown", {"action_id": action_id, "reason": "DISPATCH_OR_POST_OBSERVATION_UNCERTAIN"})
            raise UnknownDispatch("DISPATCH_OR_POST_OBSERVATION_UNCERTAIN") from None
        changed = (observation.foreground != after.foreground or
                   (observation.png_sha256 is not None and after.png_sha256 is not None
                    and observation.png_sha256 != after.png_sha256) or
                   (observation.xml_sha256 is not None and after.xml_sha256 is not None
                    and observation.xml_sha256 != after.xml_sha256))
        result = "changed" if changed else "unchanged"
        self.last_result_event = self.store.event(result, {"action_id": action_id, "before": observation.observation_id,
                                 "after": after.observation_id, "achieved": None})
        self.lock.pending.unlink()
        self.action_ids.append(action_id)
        return after, result

    async def step(self):
        self.action_ids = []
        before = await self.guarded(self.phone.observe())
        self.observed(before)
        apps = await self.guarded(self.phone.catalog())
        reading_source = 'v2_autonomous' if self.run_kind == 'autonomous' else 'live_scripted' if self.run_kind == 'scripted' else 'fixture'
        self.store.reading_context = ReadingStore(self.store, reading_source).context(before.foreground.split('/')[0])
        self.store.inbound_context = self.hub.next() if self.hub else None
        policy=SocialPolicy(self.config).read()
        social_enabled=policy.get('enabled') is True
        self.store.social_context={'enabled':social_enabled,
            'contacts_mode':policy.get('contacts',{}).get('mode'),
            'targets':SocialStore(self.store).targets() if social_enabled else []}
        proposal = await self.guarded(self.brain.decide(before, apps, self.recent))
        logged=proposal if not self.store.inbound_context else {**proposal,'note':'Ephemeral notification decision; private note omitted','life':{}}
        if self.store.inbound_context and proposal.get('event_response'):
            logged={**logged,'event_response':{**proposal['event_response'],'note':'Private notification reason omitted'}}
        self.store.event("proposal", {"observation_id": before.observation_id, **logged})
        action = proposal["action"]
        if self.hub:self.hub.resolve(self.store.inbound_context,proposal.get('event_response'))
        self.store.update(last_action=action, last_error=None)
        social_actions={'inspect_chat','reply_to_contact','initiate_dm','group_post'}
        reply_event=self.store.inbound_context if social_enabled and proposal.get('event_response',{}).get('decision')=='reply_now' else None
        if action['type'] in social_actions or reply_event:
            if not social_enabled:raise Blocked('SOCIAL_DISABLED')
            from .social_agent import SocialAgent
            from .social_ui import AndroidSocialUI
            ui=AndroidSocialUI(self,reply_event['source_app'] if reply_event else None)
            try:
                if action['type']=='inspect_chat':
                    binding,context,observed=await ui.resolve()
                    bid=SocialStore(self.store).bind(binding)
                    return {'outcome':'social_identity_observed','binding_id':bid}
                kind='auto_reply' if reply_event or action['type']=='reply_to_contact' else 'proactive_dm' if action['type']=='initiate_dm' else 'group_post'
                result=await SocialAgent(self,ui).run(kind,event=reply_event,binding_id=action.get('binding_id'))
                if reply_event:
                    self.store.event('inbound_social_result',{'event_id':reply_event['event_id'],**result,
                        'activity_resume':'abandoned_current_app_after_social_handling'})
                return result
            except Blocked as error:
                if any(code in str(error) for code in ('DEVICE_OFFLINE','DEVICE_IDENTITY','REQUEST_BUDGET','TOKEN_LIMIT')):raise
                self.store.event('social_deferred',{'reason':str(error)})
                return {'outcome':'social_deferred','reason':str(error)}
        if social_enabled and before.foreground.split('/')[0] in SOCIAL_APPS:
            controls=[c for c in before.controls if c.control_id==action.get('control_id')]
            if controls and ((action['type']=='type_text' and controls[0].is_editor and controls[0].resource.endswith('/input'))
                    or action['type']=='tap' and (controls[0].resource.endswith('/send_btn')
                        or before.foreground.split('/')[0]=='com.tencent.mm' and controls[0].text=='发送')):
                raise Blocked('SOCIAL_COMPOSER_REQUIRES_SOCIAL_INTENT')
        guard(action, before, apps, full_access=self.config.full_access)
        if action['type'] in {'watch_video','read_article'}:
            return await ContentPerception(self).run(proposal,before)
        if action["type"] == "rest":
            seconds = min(action["seconds"], max(0, self.deadline - time.monotonic()))
            self.store.event("rest", {"seconds": action["seconds"], "sleep_in_session": seconds})
            self.store.update(next_wake=time.time() + seconds)
            if self.memory:
                self.memory.begin_rest(action["seconds"])
            started = time.monotonic()
            self.inbound_wake.clear()
            try:
                await self.guarded(self.wait_for_event(seconds))
            finally:
                if self.memory:
                    elapsed = min(seconds, time.monotonic() - started)
                    completed = elapsed >= action["seconds"] - .05
                    event = self.store.event("rest_finished", {"elapsed_seconds": elapsed, "completed": completed})
                    self.memory.end_rest(elapsed, completed, event)
                    self.store.update(life=self.memory.brief())
            self.store.update(next_wake=None)
            return {"action": action, "outcome": "rested"}
        fresh = await self.guarded(self.phone.observe())
        self.observed(fresh)
        if action['type']=='tap' and before.visual_controls.get(action.get('control_id'),{}).get('kind')=='vision':
            from .visual import rebind_region
            old=next(c for c in before.controls if c.control_id==action['control_id'])
            rebind_region(before,fresh,old)
        if action["type"] in {"open_app", "home", "recent_apps"}:
            # An app switch may recover from a sensitive page; it still uses fresh device/window checks.
            guard(action, fresh, apps, full_access=self.config.full_access)
            target = None
        else:
            target = rebind(action, before, fresh, apps, full_access=self.config.full_access)
        command = await self.guarded(self.phone.prepare(action, target, fresh))
        if action["type"] in {"search", "type_text"}:
            searching = action["type"] == "search"
            selected, _ = await self.send(command, {"type": "search_select" if searching else "text_select", "control_id": action["control_id"]}, fresh)
            if selected.foreground != fresh.foreground:
                raise Blocked("SEARCH_FOREGROUND_CHANGED_AFTER_SELECTION")
            builder = self.phone.prepare_search_text if searching else self.phone.prepare_input_text
            command = await self.guarded(builder(selected, action, target))
            typed, typed_result = await self.send(command, action, selected)
            if typed.foreground != fresh.foreground:
                raise Blocked("SEARCH_FOREGROUND_CHANGED_AFTER_TYPING")
            if searching:
                command = await self.guarded(self.phone.prepare_search_submit(typed, action, target))
                after, result = await self.send(command, {"type": "search_submit", "query": action["query"]}, typed)
            else:
                after = typed
                result = typed_result
        else:
            after, result = await self.send(command, action, fresh)
        if self.memory and not self.store.inbound_context:
            self.memory.record(proposal, before, after, self.action_ids, result, self.last_result_event,
                               getattr(self.brain, "offered_memory_ids", ()))
            self.store.update(life=self.memory.brief())
        return {"action": action, "outcome": result, "note": proposal["note"]}

    async def run(self):
        with DeviceLock(self.config.serial) as self.lock:
            # No resumption/replay: every run has a fresh exclusive evidence directory.
            self.directory.mkdir(parents=True, exist_ok=False)
            self.deadline = time.monotonic() + self.minutes * 60
            self.store = LifeStore(self.config.data, str(self.directory), self.config.serial, self.run_kind)
            self.store.update(status="running", deadline=time.time() + self.minutes * 60,
                              emergency_path=str(self.lock.emergency), pending_path=str(self.lock.pending),
                              vision_configured=self.config.vision, full_access=self.config.full_access)
            self.phone = self.phone_factory(self.config, self.directory / "screens")
            self.brain = self.brain_factory(self.config, self.store)
            status, reason = "stopped", "INTERRUPTED"
            try:
                self.hub = EventHub(self.store,self.config.notification_packages)
                if (self.config.data/'notification-bridge.private.json').exists() and self.run_kind!='fixture':
                    bridge=NotificationBridge(self.config,self.phone)
                    try:
                        await self.guarded(bridge.connect(max(1,self.deadline-time.monotonic()-5)))
                        self.bridge=bridge
                        self.store.notification_client=bridge
                        self.store.update(notification_bridge='connected')
                    except (OSError,ValueError,asyncio.TimeoutError,EOFError):
                        await bridge.close()
                        self.store.update(notification_bridge='unavailable')
                    except BaseException:
                        await bridge.close()
                        raise
                if self.run_kind == "autonomous":
                    self.memory = self.store.memory = LifeMemory(self.store.db, self.config.serial,
                                                                  str(self.directory), self.store.event)
                    self.memory.start()
                    self.store.update(life=self.memory.brief())
                    remaining = min(120, max(0, self.memory.state["rest_until"] - time.time()))
                    if remaining:
                        requested = remaining
                        remaining = min(requested, max(0, self.deadline - time.monotonic()))
                        self.store.event("rest_continued", {"seconds": remaining})
                        self.store.update(next_wake=time.time() + remaining)
                        started = time.monotonic()
                        try:
                            await self.guarded(self.wait_for_event(remaining))
                        finally:
                            elapsed = min(remaining, time.monotonic() - started)
                            completed = elapsed >= requested - .05
                            event = self.store.event("rest_finished", {"elapsed_seconds": elapsed, "completed": completed})
                            self.memory.end_rest(elapsed, completed, event)
                            self.store.update(life=self.memory.brief(), next_wake=None)
                failures = 0
                while True:
                    self.check_stop()
                    try:
                        outcome = await self.step()
                        failures = 0
                    except Blocked as e:
                        code = str(e)
                        event = self.store.event("blocked", {"reason": code})
                        if self.memory:
                            self.memory.blocked(code, event, self.action_ids)
                            self.store.update(life=self.memory.brief())
                        self.store.update(blocked=self.store.state["blocked"] + 1, last_error=code)
                        if "BUDGET" in code or "TOKEN_LIMIT" in code or "IDENTITY" in code or "OFFLINE" in code:
                            raise SessionStopped(code)
                        outcome = {"outcome": "blocked", "reason": code}
                        failures += 1
                        if failures >= 5:
                            raise SessionStopped("REPEATED_FAILURES:" + code)
                    self.recent = (self.recent + [outcome])[-5:]
                    await self.guarded(asyncio.sleep(1))
            except SessionStopped as e:
                reason = str(e)
                status = "completed_duration" if reason == "GLOBAL_DEADLINE" else "stopped"
            except UnknownDispatch as e:
                status, reason = "unknown", str(e)
            except (asyncio.CancelledError, KeyboardInterrupt):
                status = "unknown" if self.lock.pending.exists() else "stopped"
                reason = "INTERRUPTED"
            except Exception as e:
                status, reason = "failed", type(e).__name__
                self.store.update(last_error=reason)
            finally:
                if hasattr(self.phone,'close'):
                    with suppress(OSError,ValueError,asyncio.TimeoutError):
                        await self.phone.close()
                if self.bridge:
                    inactive=await self.bridge.close()
                    self.store.update(notification_collection_inactive=inactive,
                                      audio_projection_released=getattr(self.bridge,'audio_projection_released',None))
                if self.lock.pending.exists():
                    status = "unknown"
                self.store.event("session_ended", {"status": status, "reason": reason})
                self.store.finish(status, reason)
                self.store.close()
            return {"status": status, "reason": reason, "session": str(self.directory)}
