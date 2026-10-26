"""Observation -> one decision -> guard -> durable dispatch -> independent observation."""
import asyncio
from contextlib import suppress
import json
import os
from pathlib import Path
import time
import uuid
from .brain import LifeBrain
from .phone import PhoneController
from .safety import Blocked, DeviceLock, UnknownDispatch, guard, rebind
from .store import LifeStore


class SessionStopped(Exception):
    pass


class SessionLoop:
    def __init__(self, config, directory, minutes=10, phone_factory=PhoneController, brain_factory=LifeBrain):
        self.config = config
        self.directory = Path(directory).resolve()
        self.minutes = minutes
        if not 0 < minutes <= 10:
            raise ValueError("M1_SESSION_REQUIRES_0_TO_10_MINUTES")
        self.phone_factory, self.brain_factory = phone_factory, brain_factory
        self.store = None
        self.recent = []  # M1 short context only, no M2 memory migration/state machine.

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
        self.check_stop()
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
            await self.guarded(self.phone.dispatch(command))
            self.store.event("action_dispatched", {"action_id": action_id, "action": action})
            self.store.update(dispatched=self.store.state["dispatched"] + 1)
            after = await self.guarded(self.phone.observe())
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
        self.store.event(result, {"action_id": action_id, "before": observation.observation_id,
                                 "after": after.observation_id, "achieved": None})
        self.lock.pending.unlink()
        return after, result

    async def step(self):
        before = await self.guarded(self.phone.observe())
        self.observed(before)
        apps = await self.guarded(self.phone.catalog())
        proposal = await self.guarded(self.brain.decide(before, apps, self.recent))
        self.store.event("proposal", {"observation_id": before.observation_id, **proposal})
        action = proposal["action"]
        self.store.update(last_action=action, last_error=None)
        guard(action, before, apps)
        if action["type"] == "rest":
            seconds = min(action["seconds"], max(0, self.deadline - time.monotonic()))
            self.store.event("rest", {"seconds": seconds})
            self.store.update(next_wake=time.time() + seconds)
            await self.guarded(asyncio.sleep(seconds))
            self.store.update(next_wake=None)
            return {"action": action, "outcome": "rested"}
        fresh = await self.guarded(self.phone.observe())
        self.observed(fresh)
        if action["type"] == "open_app":
            # An app switch may recover from a sensitive page; it still uses fresh device/window checks.
            guard(action, fresh, apps)
            target = None
        else:
            target = rebind(action, before, fresh, apps)
        command = await self.guarded(self.phone.prepare(action, target, fresh))
        after, result = await self.send(command, action, fresh)
        if action["type"] == "search":
            if after.foreground != fresh.foreground:
                raise Blocked("SEARCH_FOREGROUND_CHANGED_AFTER_TYPING")
            command = await self.guarded(self.phone.prepare_search_submit(after, action, target))
            _, result = await self.send(command, {"type": "search_submit", "query": action["query"]}, after)
        return {"action": action, "outcome": result, "note": proposal["note"]}

    async def run(self):
        with DeviceLock(self.config.serial) as self.lock:
            # No resumption/replay: every run has a fresh exclusive evidence directory.
            self.directory.mkdir(parents=True, exist_ok=False)
            self.deadline = time.monotonic() + self.minutes * 60
            self.store = LifeStore(self.config.data, str(self.directory), self.config.serial)
            self.store.update(status="running", deadline=time.time() + self.minutes * 60,
                              emergency_path=str(self.lock.emergency), pending_path=str(self.lock.pending),
                              vision_configured=self.config.vision)
            self.phone = self.phone_factory(self.config, self.directory / "screens")
            self.brain = self.brain_factory(self.config, self.store)
            status, reason = "stopped", "INTERRUPTED"
            try:
                failures = 0
                while True:
                    self.check_stop()
                    try:
                        outcome = await self.step()
                        failures = 0
                    except Blocked as e:
                        code = str(e)
                        self.store.event("blocked", {"reason": code})
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
                if self.lock.pending.exists():
                    status = "unknown"
                self.store.event("session_ended", {"status": status, "reason": reason})
                self.store.finish(status, reason)
                self.store.close()
            return {"status": status, "reason": reason, "session": str(self.directory)}
