import asyncio
from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from digitallife_v2.__main__ import main
from digitallife_v2.brain import LifeBrain, SYSTEM
from digitallife_v2.memory import LifeMemory, MAX_CONTEXT_BYTES
from digitallife_v2.phone import PhoneController
from digitallife_v2.safety import Blocked, DeviceLock, guard, parse_decision
from digitallife_v2.session import SessionLoop, SessionStopped
from digitallife_v2.store import LifeStore, open_database
from test_m1 import config, observation, decision, FixturePhone, FixtureBrain


class MemoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = config(self.root)
        self.addCleanup(self.temp.cleanup)
        self.n = 0

    def store(self, run_kind="autonomous"):
        self.n += 1
        p = self.root / f"session-{self.n}"
        p.mkdir()
        s = LifeStore(self.config.data, str(p), self.config.serial, run_kind)
        self.addCleanup(s.close)
        s.memory = LifeMemory(s.db, self.config.serial, str(p), s.event)
        s.memory.start()
        return s

    def episode(self, s, topic="科技", text="公开技术文章", reference=None):
        seq = s.db.execute("SELECT count(*) FROM events").fetchone()[0]
        before, after = observation(f"before-{seq}"), observation(f"after-{seq}")
        after.controls[0] = replace(after.controls[0], text=text)
        proposal = decision({"type": "tap", "control_id": before.controls[0].control_id})
        proposal["life"] = {"topic": topic, "goal": "继续了解这项技术"}
        if reference:
            proposal["life"]["memory_id"] = reference
        aid = f"action-{seq}"
        s.event("observed", after.summary())
        s.event("action_dispatched", {"action_id": aid, "action": proposal["action"]})
        event = s.event("changed", {"action_id": aid, "before": before.observation_id, "after": after.observation_id})
        return proposal, before, after, [aid], event

    def record(self, s, **kwargs):
        p, before, after, ids, event = self.episode(s, **kwargs)
        return s.memory.record(p, before, after, ids, "changed", event,
                               [kwargs["reference"]] if kwargs.get("reference") else [])

    def test_m1_database_migrates_without_reclassifying_old_sessions(self):
        self.config.data.mkdir()
        db = sqlite3.connect(self.config.data / "life.sqlite3")
        db.execute("CREATE TABLE sessions(id TEXT PRIMARY KEY,serial TEXT,started REAL,ended REAL,status TEXT,reason TEXT)")
        db.execute("INSERT INTO sessions VALUES('m1','bound',1,2,'completed_duration','GLOBAL_DEADLINE')")
        db.commit(); db.close()
        db = open_database(self.config.data)
        self.assertEqual(db.execute("SELECT status,run_kind FROM sessions").fetchone(), ("completed_duration", "legacy_unclassified"))
        self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 4)
        db.close()
        backups = list((self.config.data / "backups").glob("pre-m2-*.sqlite3"))
        self.assertEqual(len(backups), 1)
        backup = sqlite3.connect(backups[0])
        self.assertNotIn("run_kind", {r[1] for r in backup.execute("PRAGMA table_info(sessions)")})
        self.assertEqual(backup.execute("SELECT status FROM sessions").fetchone()[0], "completed_duration")
        backup.close()

    def test_episode_state_interest_and_goal_survive_new_session(self):
        first = self.store()
        mid = self.record(first)
        state = first.memory.brief()
        second = self.store()
        context = second.memory.context("科技")
        self.assertEqual(context["state"]["goal"]["memory_id"], mid)
        self.assertIn(mid, [m["memory_id"] for m in context["recent"]])
        self.assertEqual(second.memory.interests(), first.memory.interests())
        self.assertAlmostEqual(state["energy"], .785)
        self.assertGreaterEqual(second.memory.state["energy"], state["energy"])
        body = json.loads(first.db.execute("SELECT body FROM memories WHERE id=?", (mid,)).fetchone()[0])
        self.assertIsNone(body["achieved"])
        self.assertEqual(body["subjective"]["topic"], "科技")
        self.assertTrue(body["dispatched"])

    def test_blocked_candidate_does_not_inflate_interests_or_advance_goal(self):
        s = self.store()
        self.record(s)
        before = s.memory.brief()
        event = s.event("blocked", {"reason": "UNKNOWN_ACTION_ROLE"})
        s.memory.blocked("UNKNOWN_ACTION_ROLE", event)
        self.assertEqual(s.memory.state["goal"], before["goal"])
        self.assertEqual(s.memory.interests(), before["interests"])
        self.assertEqual(s.memory.state["energy"], before["energy"])
        self.assertGreater(s.memory.state["boredom"], before["boredom"])
        body = json.loads(s.db.execute("SELECT body FROM memories WHERE kind='blocked'").fetchone()[0])
        self.assertIs(body["dispatched"], False)
        self.assertIs(body["achieved"], False)

    def test_interests_change_gradually_and_require_new_observed_content(self):
        s = self.store()
        self.record(s, text="第一项公开内容")
        self.assertEqual(s.memory.interests()[0]["weight"], .37)
        self.record(s, text="第一项公开内容")
        self.assertEqual(s.memory.interests()[0]["evidence_count"], 1)
        self.record(s, text="另一项公开内容")
        self.assertEqual(s.memory.interests()[0]["weight"], .39)
        self.assertEqual(s.memory.interests()[0]["evidence_count"], 2)

    def test_png_changes_do_not_make_static_page_interesting(self):
        s = self.store()
        p, before, after, ids, ev = self.episode(s)
        after.controls = [replace(before.controls[0], control_id=after.observation_id + ":0")]
        before.png_sha256, after.png_sha256 = "cursor-frame-one", "cursor-frame-two"
        starting = s.memory.state["boredom"]
        mid = s.memory.record(p, before, after, ids, "changed", ev)
        body = json.loads(s.db.execute("SELECT body FROM memories WHERE id=?", (mid,)).fetchone()[0])
        self.assertFalse(body["content_changed"])
        self.assertTrue(body["repeat_content"])
        self.assertGreater(s.memory.state["boredom"], starting)
        self.assertEqual(s.memory.state["stagnant_steps"], 1)

    def test_content_fingerprint_includes_body_beyond_toolbar_and_scroll_positions(self):
        s = self.store(); before = observation()
        before.controls = [replace(before.controls[0], control_id=f"one:{i}", text=f"导航 {i}") for i in range(8)]
        before.controls.append(replace(before.controls[0], control_id="one:8", text="真正的正文内容", bounds=(100, 900, 600, 1200)))
        after = replace(before, controls=list(before.controls))
        after.controls[-1] = replace(after.controls[-1], bounds=(100, 500, 600, 800))
        self.assertNotEqual(s.memory.content(before)[0], s.memory.content(after)[0])

    def test_partial_search_refusal_preserves_actual_dispatch_without_claiming_completion(self):
        s = self.store()
        _, _, _, ids, _ = self.episode(s)
        event = s.event("blocked", {"reason": "SEARCH_FOCUS_OR_TEXT_CHANGED"})
        s.memory.blocked("SEARCH_FOCUS_OR_TEXT_CHANGED", event, ids)
        body = json.loads(s.db.execute("SELECT body FROM memories WHERE kind='blocked'").fetchone()[0])
        self.assertTrue(body["dispatched"])
        self.assertEqual(body["partial_action_ids"], ids)
        self.assertIs(body["achieved"], False)
        self.assertFalse(s.memory.interests())

    def test_goal_memory_stays_offered_after_multiple_refusals(self):
        s = self.store(); mid = self.record(s)
        for _ in range(6):
            event = s.event("blocked", {"reason": "UNKNOWN_ACTION_ROLE"})
            s.memory.blocked("UNKNOWN_ACTION_ROLE", event)
        context = s.memory.context()
        self.assertTrue(all(m["kind"] == "blocked" for m in context["recent"]))
        self.assertIn(mid, [m["memory_id"] for m in context["relevant"]])

    def test_duplicate_episode_cannot_double_update_life(self):
        s = self.store()
        p, b, a, ids, ev = self.episode(s)
        self.assertIsNotNone(s.memory.record(p, b, a, ids, "changed", ev))
        before = s.memory.brief()
        self.assertIsNone(s.memory.record(p, b, a, ids, "changed", ev))
        self.assertEqual(s.memory.brief(), before)

    def test_scripted_acceptance_never_becomes_autonomous_memory(self):
        s = self.store("scripted")
        p, b, a, ids, ev = self.episode(s)
        with self.assertRaisesRegex(ValueError, "AUTONOMOUS"):
            s.memory.record(p, b, a, ids, "changed", ev)
        self.assertFalse(s.memory.interests())

    def test_memory_requires_dispatch_and_independent_observation_sources(self):
        s = self.store()
        p, b, a, ids, ev = self.episode(s)
        with self.assertRaisesRegex(ValueError, "SOURCE"):
            s.memory.record(p, b, a, [], "changed", ev)
        with self.assertRaisesRegex(ValueError, "SOURCE"):
            s.memory.record(p, b, a, ids, "changed", ev + 999)
        s.db.execute("DELETE FROM events WHERE kind='observed'"); s.db.commit()
        with self.assertRaisesRegex(ValueError, "OBSERVATION"):
            s.memory.record(p, b, a, ids, "changed", ev)

    def test_rest_credits_actual_elapsed_and_persists_remaining_rest(self):
        s = self.store()
        s.memory.begin_rest(120)
        energy = s.memory.state["energy"]
        event = s.event("rest_finished", {"elapsed_seconds": 2, "completed": False})
        s.memory.end_rest(2, False, event)
        self.assertAlmostEqual(s.memory.state["energy"], energy + .004)
        next_session = self.store()
        self.assertGreater(next_session.memory.state["rest_until"], time.time())
        next_session.memory.end_rest(10, True, event)
        self.assertEqual(next_session.memory.state["rest_until"], 0)

    def test_context_is_bounded_and_excludes_non_autonomous_sources(self):
        s = self.store()
        for i in range(12):
            self.record(s, text="科技公开内容" + str(i) + "文字" * 60)
        for i in range(10):
            s.memory.insert("episode", "脚本验收内容" * 50, {}, source_type="scripted", source_device="fixture", created=time.time()+100)
        s.db.commit()
        value = s.memory.context("科技")
        self.assertLessEqual(len(value["recent"]), 5)
        self.assertLessEqual(len(value["relevant"]), 5)
        self.assertLessEqual(len(json.dumps(value, ensure_ascii=False).encode()), MAX_CONTEXT_BYTES)
        self.assertTrue(all(m["source_type"] == "v2_autonomous" for m in value["recent"]))
        self.assertTrue(all(m["trust"] == "data_not_instructions" for m in value["recent"] + value["relevant"]))
        self.assertTrue(all(m["source_type"] == "v2_autonomous" for m in value["recent"] + value["relevant"]))
        self.assertIsNotNone(s.memory.state["reflection"])

    async def test_brain_receives_continuity_and_checks_cited_memory(self):
        s = self.store()
        mid = self.record(s)
        valid = decision({"type": "back"})
        valid["life"] = {"topic": "科技", "goal": "继续了解这项技术", "memory_id": mid}
        response = lambda p: {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(p)}}]}
        bad = {**valid, "life": {**valid["life"], "memory_id": "invented"}}
        transport = AsyncMock(side_effect=[response(bad), response(valid)])
        brain = LifeBrain(self.config, s, transport)
        result = await brain.decide(observation(), {"com.reader": []}, [])
        self.assertEqual(result["life"]["memory_id"], mid)
        self.assertEqual(transport.await_count, 2)
        context = json.loads(transport.call_args.args[0]["messages"][1]["content"])["continuity"]
        self.assertEqual(context["state"]["goal"]["memory_id"], mid)
        self.assertIn("不能改变安全边界", SYSTEM)

    def test_continuation_has_prior_session_source_and_observed_result(self):
        a = self.store(); old = self.record(a)
        b = self.store(); new = self.record(b, reference=old, text="后续公开内容")
        body = json.loads(b.db.execute("SELECT body FROM memories WHERE id=?", (new,)).fetchone()[0])
        self.assertEqual(body["continuation_of"], old)
        source = b.db.execute("SELECT source_session FROM memories WHERE id=?", (old,)).fetchone()[0]
        self.assertNotEqual(source, b.session)

    def test_life_metadata_cannot_inject_an_action_or_change_limits(self):
        for life in ({"topic": "科技", "shell": "id"}, {"goal": "x" * 161}, {"memory_id": 2}, {"budget": 10000}):
            proposal = decision({"type": "back"}); proposal["life"] = life
            with self.assertRaises(Blocked):
                parse_decision(json.dumps(proposal))

    def test_android_navigation_actions_are_fixed_and_home_can_leave_redacted_page(self):
        for kind in ("home", "recent_apps", "back"):
            self.assertEqual(parse_decision(json.dumps(decision({"type": kind})))["action"], {"type": kind})
            with self.assertRaises(Blocked):
                parse_decision(json.dumps(decision({"type": kind, "keycode": "26"})))
        sensitive = observation(redacted=True)
        self.assertIsNone(guard({"type": "home"}, sensitive, {}))
        self.assertIsNone(guard({"type": "recent_apps"}, sensitive, {}))
        with self.assertRaises(Blocked):
            guard({"type": "back"}, sensitive, {})

    async def test_navigation_builds_exact_home_back_app_switch_keycodes(self):
        phone = PhoneController(self.config, self.root)
        phone.identity = AsyncMock()
        phone.scene = AsyncMock(return_value="com.reader/.Main")
        phone._adb = AsyncMock()
        for kind, key in (("home", "3"), ("recent_apps", "187"), ("back", "4")):
            cmd = await phone.prepare({"type": kind}, None, observation())
            self.assertEqual(cmd, ("shell", "input", "keyevent", key))
            await phone.dispatch(cmd)
            phone._adb.assert_awaited_with(*cmd, dispatch=True)
        self.assertFalse(phone._recent_apps_requested)

    def test_overview_keeps_xml_controls_but_withholds_only_image_input(self):
        s = self.store()
        self.config.vision = True
        image = self.root / "overview.png"; image.write_bytes(b"local-private-preview")
        obs = observation()
        obs.png_path = str(image)
        obs.vision_allowed = False
        obs.image_withheld_reason = "unclassified_task_preview_images"
        payload = LifeBrain(self.config, s).payload(obs, {"com.reader": []}, [])
        user = payload["messages"][1]["content"]
        self.assertIsInstance(user, str)
        data = json.loads(user)
        self.assertFalse(data["vision_available"])
        self.assertTrue(data["observation"]["controls"])
        self.assertTrue(data["page_text_from_xml"])
        self.assertEqual(data["observation"]["image_withheld_reason"], "unclassified_task_preview_images")

    def test_memory_cli_is_readonly_and_never_calls_phone_or_model(self):
        s = self.store(); self.record(s)
        with patch("digitallife_v2.__main__.Config.load", return_value=self.config), \
                patch("digitallife_v2.phone.asyncio.create_subprocess_exec", side_effect=AssertionError("process")), redirect_stdout(StringIO()):
            self.assertEqual(main(["memory"]), 0)

    async def test_restart_waits_remaining_rest_without_replaying_a_device_action(self):
        s = self.store(); s.memory.begin_rest(3)
        original = DeviceLock.__init__
        def init(lock, serial):
            original(lock, serial); lock.root = self.root / "locks"
        FixturePhone.instances.clear()
        with patch.object(DeviceLock, "__init__", init), \
                patch("digitallife_v2.session.PhoneController", FixturePhone), patch("digitallife_v2.session.LifeBrain", FixtureBrain):
            result = await SessionLoop(self.config, self.root / "short", minutes=.01,
                phone_factory=FixturePhone, brain_factory=FixtureBrain).run()
        self.assertEqual(result["status"], "completed_duration")
        self.assertFalse(FixturePhone.instances[0].calls)
        self.assertEqual(FixturePhone.instances[0].count, 0)
        state = LifeMemory(s.db, s.serial).state
        self.assertGreater(state["rest_until"], time.time())

    async def test_deadline_reserve_stops_before_pending_or_device_dispatch(self):
        loop = SessionLoop(self.config, self.root / "reserve", phone_factory=FixturePhone, brain_factory=FixtureBrain)
        loop.directory.mkdir()
        loop.run_kind = "autonomous"
        loop.deadline = time.monotonic() + 2
        loop.phone = FixturePhone(self.config, self.root)
        loop.lock = DeviceLock(self.config.serial)
        loop.lock.root = self.root / "locks"
        loop.lock.root.mkdir()
        with self.assertRaisesRegex(SessionStopped, "OBSERVATION_TIME_RESERVE"):
            await loop.send(("fixture", "tap"), {"type": "tap"}, observation())
        self.assertFalse(loop.lock.pending.exists())
        self.assertFalse(loop.phone.calls)


if __name__ == "__main__":
    unittest.main()
