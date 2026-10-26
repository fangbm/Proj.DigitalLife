import asyncio
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import AsyncMock, patch
from PIL import Image

from digitallife_v2.brain import LifeBrain
from digitallife_v2.config import Config, IDENTITY
from digitallife_v2.perception import Control, Observation, parse_xml, unlocked_awake
from digitallife_v2.phone import PhoneController
from digitallife_v2.safety import Blocked, DeviceLock, UnknownDispatch, guard, parse_decision, rebind
from digitallife_v2.session import SessionLoop
from digitallife_v2.store import LifeStore, status
from digitallife_v2.__main__ import main, stop


def config(root):
    identity = {key: "test" for key in IDENTITY}
    identity.update({"ro.product.manufacturer": "OPPO", "ro.product.model": "PGFM10", "ro.kernel.qemu": ""})
    return Config("OFFLINE_TEST_DEVICE", identity, data=Path(root) / "db", api_key="fake-offline-key")


def observation(oid="one", foreground="com.reader/.Main", **kwargs):
    c = Control(oid + ":0", "com.reader", "com.reader:id/article", "阅读", "", "android.widget.Button",
                (100, 200, 600, 800), True, False, True, False, None, None)
    return Observation(oid, "2026-10-26T00:00:00Z", "OFFLINE_TEST_DEVICE", foreground,
                       720, 1600, 0, [c], png_sha256="png-" + oid, xml_sha256="xml-" + oid, **kwargs)


def decision(action):
    return {"intent": "rest" if action["type"] == "rest" else "explore", "action": action, "note": "想读文章"}


class FixturePhone:
    instances = []
    unknown = False
    def __init__(self, config, directory):
        self.calls, self.count = [], 0
        self.__class__.instances.append(self)
    async def observe(self):
        self.count += 1
        return observation(str(self.count), foreground="com.reader/.Article" if self.calls else "com.reader/.Main")
    async def catalog(self):
        return {"com.reader": ["com.reader/.Main"]}
    async def prepare(self, action, target, obs):
        return ("fixture", action["type"])
    async def dispatch(self, command):
        self.calls.append(command)
        if self.unknown:
            raise UnknownDispatch("OFFLINE_UNKNOWN")


class FixtureBrain:
    def __init__(self, config, store):
        self.count = 0
    async def decide(self, obs, apps, recent):
        self.count += 1
        if self.count == 1:
            return decision({"type": "tap", "control_id": obs.controls[0].control_id})
        return decision({"type": "rest", "seconds": 120})


class M1Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = config(self.root)
        self.lockroot = self.root / "locks"
        original = DeviceLock.__init__
        def init(lock, serial):
            original(lock, serial)
            lock.root = self.lockroot
        self.lock_patch = patch.object(DeviceLock, "__init__", init)
        self.lock_patch.start()
        self.addCleanup(self.lock_patch.stop)
        self.addCleanup(self.temp.cleanup)
        FixturePhone.instances.clear()
        FixturePhone.unknown = False

    def make_store(self):
        path = self.root / "session"
        path.mkdir()
        store = LifeStore(self.config.data, str(path), self.config.serial)
        self.addCleanup(store.close)
        return store

    def test_strict_json_and_single_action(self):
        for a in ({"type": "open_app", "package_name": "com.reader"}, {"type": "back"},
                  {"type": "tap", "control_id": "a:0"}, {"type": "swipe", "control_id": "a:0", "direction": "up"},
                  {"type": "search", "control_id": "a:0", "query": "space science"}, {"type": "rest", "seconds": 30}):
            self.assertEqual(parse_decision(json.dumps(decision(a)))["action"], a)
        invalid = ["[]", '{"intent":"rest","intent":"explore"}',
                   json.dumps(decision({"type": "tap", "control_id": "a:0", "x": 123})),
                   json.dumps(decision({"type": "rest", "seconds": True})),
                   json.dumps(decision({"type": "search", "control_id": "a:0", "query": "a;reboot"})),
                   json.dumps(decision({"type": "search", "control_id": "a:0", "query": "中文"})),
                   json.dumps(decision({"type": "open_app", "package_name": "com.reader;evil"})),
                   json.dumps(decision({"type": "shell", "command": "id"}))]
        for text in invalid:
            with self.subTest(text=text), self.assertRaises(Blocked):
                parse_decision(text)

    def test_xml_unknown_fields_and_sensitive_redaction(self):
        raw = '<?xml version="1.0"?><hierarchy rotation="1"><node package="com.reader" bounds="[0,0][720,1600]" enabled="true" text="阅读"/></hierarchy>dumped'
        _, controls, private, rotation = parse_xml(raw, "a")
        self.assertIsNone(controls[0].visible)
        self.assertIsNone(controls[0].editable)
        self.assertFalse(private)
        self.assertEqual(rotation, 1)
        for attr in ('password="true"', 'resource-id="password_editor"', 'class="android.widget.EditText"'):
            with self.subTest(attr=attr):
                self.assertTrue(parse_xml(raw.replace('enabled="true"', attr), "b")[2])

    def test_lockscreen_grammar_and_conflicts(self):
        power = "mWakefulness=Awake mInteractive=true"
        for policy, display, expected in [
                ("mShowingLockscreen=false", "", True),
                ("KeyguardServiceDelegate\n  showing=false\nOther\n  showing=true", "", True),
                ("showing=false", "mKeyguardDrawComplete=false", False),
                ("mShowingLockscreen=false", "mShowingLockscreen=true", False),
                ("mShowingLockscreen=false mShowingLockscreen=unknown", "", False),
                ("mShowingLockscreen=true", "", False)]:
            with self.subTest(policy=policy):
                self.assertEqual(unlocked_awake(power, policy, display), expected)
        self.assertFalse(unlocked_awake(power + " mInteractive=false", "mShowingLockscreen=false", ""))

    def test_bound_controls_and_side_effect_guard(self):
        obs = observation()
        a = {"type": "tap", "control_id": "one:0"}
        self.assertIsNotNone(guard(a, obs, {}))
        for change in ({"text": "点赞"}, {"text": "关注"}, {"text": "发送"}, {"text": "删除"},
                       {"text": "确认"}, {"text": "save"}, {"visible": False}, {"enabled": None},
                       {"ancestry": "comment_submit"}, {"package": "com.foreign"}, {"text": "不明按钮", "resource": "unknown"}):
            obs.controls[0] = replace(observation().controls[0], **change)
            with self.subTest(change=change), self.assertRaises(Blocked):
                guard(a, obs, {})
        with self.assertRaises(Blocked):
            guard(a, observation("new"), {})
        with self.assertRaises(Blocked):
            guard({"type": "open_app", "package_name": "com.no"}, observation(), {"com.reader": []})
        with self.assertRaises(Blocked):
            guard({"type": "back"}, observation(redacted=True), {})

    def test_fresh_target_rebinding(self):
        a = {"type": "tap", "control_id": "one:0"}
        self.assertEqual(rebind(a, observation(), observation("two"), {}).control_id, "two:0")
        for fresh in (observation("two", foreground="com.other/.Main"),
                      replace(observation("two"), width=1080),
                      replace(observation("two"), controls=[])):
            with self.assertRaises(Blocked):
                rebind(a, observation(), fresh, {})

    def test_tab_label_uses_verified_clickable_parent_without_inventing_flags(self):
        raw = ('<?xml version="1.0"?><hierarchy><node package="com.reader" bounds="[0,0][720,1600]">'
               '<node package="com.reader" bounds="[100,200][300,400]" clickable="true" enabled="true">'
               '<node package="com.reader" resource-id="com.reader:id/tv_tab_title" text="热榜" '
               'class="android.widget.TextView" bounds="[120,230][280,330]" clickable="false" enabled="true"/>'
               '</node></node></hierarchy>')
        controls = parse_xml(raw, "one")[1]
        obs = replace(observation(), controls=controls)
        self.assertIs(controls[2].clickable, False)
        self.assertIsNone(controls[2].visible)
        self.assertIsNotNone(guard({"type": "tap", "control_id": "one:2"}, obs, {}))
        fresh = replace(observation("two"), controls=parse_xml(raw, "two")[1])
        self.assertEqual(rebind({"type": "tap", "control_id": "one:2"}, obs, fresh, {}).control_id, "two:2")
        bad = raw.replace('clickable="true"', 'clickable="false"')
        with self.assertRaises(Blocked):
            rebind({"type": "tap", "control_id": "one:2"}, obs,
                   replace(fresh, controls=parse_xml(bad, "two")[1]), {})
        risky = raw.replace('<node package="com.reader" bounds="[100,200]',
                            '<node text="关注" package="com.reader" bounds="[100,200]')
        with self.assertRaises(Blocked):
            guard({"type": "tap", "control_id": "one:2"}, replace(obs, controls=parse_xml(risky, "one")[1]), {})

    async def test_fullscreen_scroll_uses_clipped_gesture_not_edge_controls(self):
        obs = observation()
        obs.controls[0] = replace(obs.controls[0], bounds=(0, 0, 720, 1600), scrollable=True)
        action = {"type": "swipe", "control_id": "one:0", "direction": "up"}
        target = guard(action, obs, {})
        phone = PhoneController(self.config, self.root)
        phone.identity = AsyncMock()
        phone.scene = AsyncMock(return_value=obs.foreground)
        cmd = await phone.prepare(action, target, obs)
        self.assertGreater(int(cmd[4]), int(cmd[6]))
        self.assertGreater(int(cmd[6]), 1600 * .12)
        self.assertLess(int(cmd[4]), 1600 * .82)
        obs.controls[0] = replace(target, bounds=(0, 200, 720, 350))
        with self.assertRaisesRegex(Blocked, "SCROLL_REGION"):
            guard(action, obs, {})

    def overlay_xml(self):
        return ('<?xml version="1.0"?><hierarchy><node package="com.reader" bounds="[0,0][720,1600]">'
                '<node drawing-order="1" package="com.reader" bounds="[0,0][720,1600]">'
                '<node package="com.reader" resource-id="article" text="后台文章" class="android.widget.Button" '
                'bounds="[100,200][600,400]" enabled="true" clickable="true"/></node>'
                '<node drawing-order="7" package="com.reader" bounds="[0,0][720,1600]">'
                '<node package="com.reader" resource-id="search" class="android.widget.EditText" '
                'bounds="[100,100][600,200]" enabled="true" clickable="true" focused="true" text=""/>'
                '</node></node></hierarchy>')

    def test_focused_overlay_excludes_retained_background_controls(self):
        controls = parse_xml(self.overlay_xml(), "one")[1]
        obs = replace(observation(), controls=controls)
        self.assertTrue(controls[2].covered)
        self.assertFalse(controls[4].covered)
        with self.assertRaises(Blocked):
            guard({"type": "tap", "control_id": "one:2"}, obs, {})
        self.assertIsNotNone(guard({"type": "search", "control_id": "one:4"}, obs, {}))
        unknown = parse_xml(self.overlay_xml().replace('drawing-order="7"', ''), "one")[1]
        self.assertFalse(any(c.covered for c in unknown))

    def test_prompt_has_only_guarded_affordances_and_active_layer_text(self):
        store = self.make_store()
        controls = parse_xml(self.overlay_xml(), "one")[1]
        obs = replace(observation(), controls=controls)
        value = json.loads(LifeBrain(self.config, store).payload(obs, {"com.reader": []}, [])["messages"][1]["content"])
        offered = value["observation"]["controls"]
        self.assertEqual(len(offered), 1)
        self.assertEqual(offered[0]["control_id"], "one:4")
        self.assertIn("search", offered[0]["allowed_actions"])
        self.assertNotIn("后台文章", str(value["page_text_from_xml"]))
        self.assertTrue(all("control_id" not in x for x in value["page_text_from_xml"]))

    def test_scroll_requires_current_container(self):
        obs = observation()
        a = {"type": "swipe", "control_id": "one:0", "direction": "up"}
        with self.assertRaises(Blocked):
            guard(a, obs, {})
        obs.controls[0] = replace(obs.controls[0], scrollable=True)
        self.assertIsNotNone(guard(a, obs, {}))

    def test_search_requires_identified_focused_search_editor(self):
        obs = observation()
        obs.controls[0] = replace(obs.controls[0], resource="com.reader:id/search", text="", kind="android.widget.EditText")
        a = {"type": "search", "control_id": "one:0", "query": "space"}
        self.assertIsNotNone(guard({"type": "tap", "control_id": "one:0"}, obs, {}))
        with self.assertRaises(Blocked):
            guard(a, obs, {})
        obs.controls[0] = replace(obs.controls[0], focused=True)
        self.assertIsNotNone(guard(a, obs, {}))
        obs.controls[0] = replace(obs.controls[0], text="existing private text")
        self.assertIsNotNone(guard(a, obs, {}))
        obs.controls[0] = replace(obs.controls[0], resource="message_input", description="", text="")
        with self.assertRaises(Blocked):
            guard(a, obs, {})

    def test_autocomplete_search_hint_is_not_treated_as_verified_empty_text(self):
        raw = ('<?xml version="1.0"?><hierarchy><node package="com.reader" resource-id="com.reader:id/search_src_text" '
               'class="android.widget.AutoCompleteTextView" text="搜索…" clickable="true" focused="true" enabled="true" '
               'bounds="[100,200][600,300]"/></hierarchy>')
        _, controls, redacted, _ = parse_xml(raw, "one")
        obs = replace(observation(), controls=controls)
        self.assertFalse(redacted)
        self.assertTrue(controls[0].is_editor)
        self.assertEqual(controls[0].text, "搜索…")
        self.assertIsNotNone(guard({"type": "search", "control_id": "one:0"}, obs, {}))
        self.assertTrue(parse_xml(raw.replace("search_src_text", "message_input").replace("搜索…", ""), "one")[2])

    def test_device_lock_and_persistent_barriers(self):
        with DeviceLock(self.config.serial) as lock:
            with self.assertRaises((OSError, Blocked)):
                with DeviceLock(self.config.serial):
                    self.fail("second lock acquired")
            lock.emergency.touch()
        with self.assertRaises(Blocked):
            with DeviceLock(self.config.serial):
                pass
        lock.emergency.unlink()
        lock.pending.write_text("{}")
        with self.assertRaises(Blocked):
            with DeviceLock(self.config.serial):
                pass

    async def test_identity_mismatch_never_inputs(self):
        phone = PhoneController(self.config, self.root)
        phone._text = AsyncMock(side_effect=["device"] + ["changed"] * len(IDENTITY))
        with self.assertRaisesRegex(Blocked, "IDENTITY"):
            await phone.identity()

    async def test_observation_uses_real_png_xml_and_private_paths(self):
        phone = PhoneController(self.config, self.root / "screens")
        phone.identity = AsyncMock()
        phone.scene = AsyncMock(return_value="com.reader/.Main")
        xml = '<?xml version="1.0"?><hierarchy><node package="com.reader" bounds="[10,100][700,1400]" enabled="true"/></hierarchy>'
        phone._text = AsyncMock(return_value=xml)
        raw = BytesIO()
        Image.new("RGB", (720, 1600)).save(raw, format="PNG")
        phone._adb = AsyncMock(return_value=raw.getvalue())
        obs = await phone.observe()
        self.assertEqual((obs.width, obs.height), (720, 1600))
        self.assertTrue(Path(obs.png_path).exists())
        self.assertTrue(Path(obs.xml_path).exists())
        phone._text.assert_awaited_with("exec-out", "uiautomator", "dump", "/proc/self/fd/1")

    async def test_sensitive_observation_never_captures_png_or_saves_xml(self):
        phone = PhoneController(self.config, self.root / "screens")
        phone.identity = AsyncMock()
        phone.scene = AsyncMock(return_value="com.reader/.Main")
        phone._text = AsyncMock(return_value='<?xml version="1.0"?><hierarchy><node password="true" bounds="[0,0][700,1000]"/></hierarchy>')
        phone._adb = AsyncMock()
        obs = await phone.observe()
        self.assertTrue(obs.redacted)
        self.assertFalse(obs.controls)
        phone._adb.assert_not_awaited()
        self.assertFalse((self.root / "screens").exists())

    async def test_fixed_command_builders(self):
        phone = PhoneController(self.config, self.root)
        phone.identity = AsyncMock()
        phone.scene = AsyncMock(return_value="com.reader/.Main")
        obs = observation()
        c = obs.controls[0]
        self.assertEqual(await phone.prepare({"type": "tap"}, c, obs), ("shell", "input", "tap", "350", "500"))
        self.assertEqual((await phone.prepare({"type": "swipe", "direction": "up"}, c, obs))[-1], "350")
        self.assertEqual(await phone.prepare({"type": "back"}, None, obs), ("shell", "input", "keyevent", "4"))
        self.assertEqual(await phone.prepare({"type": "search", "query": "space science"}, c, obs),
                         ("shell", "input", "keycombination", "113", "29"))
        phone.recheck_search = AsyncMock()
        self.assertEqual(await phone.prepare_search_text(obs, {"query": "space science"}, c),
                         ("shell", "input", "text", "'space%sscience'"))
        phone.recheck_search.assert_awaited_once_with(obs, c, c.text)
        phone.catalog = AsyncMock()
        phone.apps = {"com.reader": ["com.reader/.Main"]}
        phone._text = AsyncMock(return_value="com.reader/.Main")
        cmd = await phone.prepare({"type": "open_app", "package_name": "com.reader"}, None, obs)
        self.assertEqual(cmd, ("shell", "am", "start", "-W", "-a", "android.intent.action.MAIN",
                               "-c", "android.intent.category.LAUNCHER", "-n", "com.reader/.Main"))

    def test_background_surfaces_not_automatic_occlusion(self):
        phone = PhoneController(self.config, self.root)
        phone.last_windows = "Window #1 Window{abc com.overlay/.Main}\n mHasSurface=true mViewVisibility=0x0 ty=2038"
        phone.check_windows("com.reader/.Main")
        phone.last_windows += "\n isOnScreen()=true isVisible()=true"
        with self.assertRaises(Blocked):
            phone.check_windows("com.reader/.Main")

    async def test_search_submit_checks_text_focus_and_foreground(self):
        phone = PhoneController(self.config, self.root)
        phone.identity = AsyncMock()
        phone.scene = AsyncMock(return_value="com.reader/.Main")
        obs = observation()
        target = replace(obs.controls[0], kind="android.widget.EditText", resource="search", focused=True, text="")
        obs.controls[0] = replace(target, text="space")
        phone._text = AsyncMock(return_value='<?xml version="1.0"?><hierarchy rotation="0"><node package="com.reader" resource-id="search" text="space" content-desc="" class="android.widget.EditText" bounds="[100,200][600,800]" clickable="true" scrollable="false" enabled="true" focused="true"/></hierarchy>')
        cmd = await phone.prepare_search_submit(obs, {"query": "space"}, target)
        self.assertEqual(cmd[-1], "66")
        obs.controls[0] = replace(obs.controls[0], focused=False)
        with self.assertRaises(Blocked):
            await phone.prepare_search_submit(obs, {"query": "space"}, target)

    async def test_search_focus_changes_during_final_recheck(self):
        phone = PhoneController(self.config, self.root)
        phone.identity = AsyncMock()
        phone.scene = AsyncMock(return_value="com.reader/.Main")
        obs = observation()
        target = replace(obs.controls[0], kind="android.widget.EditText", resource="search", focused=True, text="")
        obs.controls[0] = replace(target, text="space")
        phone._text = AsyncMock(return_value='<?xml version="1.0"?><hierarchy rotation="0"><node package="com.reader" resource-id="search" text="space" class="android.widget.EditText" bounds="[100,200][600,800]" clickable="true" scrollable="false" enabled="true" focused="false"/></hierarchy>')
        with self.assertRaisesRegex(Blocked, "FOCUS"):
            await phone.prepare_search_submit(obs, {"query": "space"}, target)

    async def test_search_wrong_or_appended_text_never_produces_enter(self):
        phone = PhoneController(self.config, self.root)
        phone.identity = AsyncMock()
        phone._text = AsyncMock()
        obs = observation()
        target = replace(obs.controls[0], kind="android.widget.AutoCompleteTextView", resource="search", focused=True, text="old")
        obs.controls[0] = replace(target, text="oldsettings")
        with self.assertRaisesRegex(Blocked, "TEXT_CHANGED"):
            await phone.prepare_search_submit(obs, {"query": "settings"}, target)
        phone._text.assert_not_awaited()

    async def test_search_selection_focus_change_blocks_typing(self):
        phone = PhoneController(self.config, self.root)
        phone.identity = AsyncMock()
        phone._text = AsyncMock()
        obs = observation()
        target = replace(obs.controls[0], kind="android.widget.AutoCompleteTextView", resource="search", focused=True, text="Search...")
        obs.controls[0] = replace(target, focused=False)
        with self.assertRaisesRegex(Blocked, "FOCUS"):
            await phone.prepare_search_text(obs, {"query": "settings"}, target)
        phone._text.assert_not_awaited()

    def test_hidden_parent_and_nested_side_effect_label(self):
        raw = '<?xml version="1.0"?><hierarchy><node package="com.reader" resource-id="article" bounds="[100,200][600,800]" clickable="true" enabled="true"><node text="点赞"/></node></hierarchy>'
        _, controls, _, _ = parse_xml(raw, "one")
        obs = replace(observation(), controls=controls)
        with self.assertRaises(Blocked):
            guard({"type": "tap", "control_id": "one:0"}, obs, {})
        raw = raw.replace('clickable="true"', 'clickable="true" visible-to-user="false"').replace('<node text="点赞"/>',
            '<node package="com.reader" resource-id="video" bounds="[100,200][500,700]" clickable="true" enabled="true"/>')
        controls = parse_xml(raw, "one")[1]
        self.assertIs(controls[1].visible, False)

    async def test_sensitive_page_appearing_after_capture_discards_artifacts(self):
        phone = PhoneController(self.config, self.root / "screens")
        phone.identity = AsyncMock()
        phone.scene = AsyncMock(return_value="com.reader/.Main")
        phone._text = AsyncMock(side_effect=[
            '<?xml version="1.0"?><hierarchy><node text="阅读" bounds="[10,100][700,1400]"/></hierarchy>',
            '<?xml version="1.0"?><hierarchy><node password="true" bounds="[10,100][700,1400]"/></hierarchy>'])
        raw = BytesIO()
        Image.new("RGB", (720, 1600)).save(raw, format="PNG")
        phone._adb = AsyncMock(return_value=raw.getvalue())
        obs = await phone.observe()
        self.assertTrue(obs.redacted)
        self.assertIsNone(obs.png_path)
        self.assertFalse((self.root / "screens").exists())

    async def test_post_observation_failure_retains_barrier(self):
        class FailedPostPhone(FixturePhone):
            async def observe(self):
                if self.calls:
                    raise Blocked("OFFLINE_POST_CAPTURE_FAILURE")
                return await super().observe()
        result = await SessionLoop(self.config, self.root / "run", minutes=.04,
            phone_factory=FailedPostPhone, brain_factory=FixtureBrain).run()
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(len(FixturePhone.instances[-1].calls), 1)
        self.assertTrue((self.lockroot / "PENDING_ACTION.json").exists())

    async def test_fixed_dispatch_rejects_shell_and_unsafe_text(self):
        phone = PhoneController(self.config, self.root)
        phone._adb = AsyncMock()
        for cmd in (("shell", "reboot"), ("shell", "input", "text", "'a;reboot'"),
                    ("shell", "am", "start", "https://example.com"), ("shell", "input", "keyevent", "26"),
                    ("shell", "input", "keycombination", "113", "28")):
            with self.subTest(command=cmd), self.assertRaises(Blocked):
                await phone.dispatch(cmd)
        phone._adb.assert_not_awaited()
        await phone.dispatch(("shell", "input", "tap", "100", "200"))
        phone._adb.assert_awaited_once_with("shell", "input", "tap", "100", "200", dispatch=True)
        phone._adb.reset_mock()
        await phone.dispatch(("shell", "input", "keycombination", "113", "29"))
        phone._adb.assert_awaited_once_with("shell", "input", "keycombination", "113", "29", dispatch=True)

    def test_v2_imports_do_not_load_v1(self):
        result = subprocess.run([sys.executable, "-c", "import sys; import digitallife_v2.session; assert not any(n.startswith('phonelife') for n in sys.modules)"],
            capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))

    def test_emergency_stop_stays_set_after_session_ends(self):
        store = self.make_store()
        emergency = self.lockroot / "EMERGENCY_STOP"
        store.update(emergency_path=str(emergency))
        store.finish("stopped", "offline")
        result = stop(store.directory, emergency=True, wait=0)
        self.assertEqual(result["status"], "stopped")
        self.assertTrue(emergency.exists())
        with self.assertRaises(Blocked):
            with DeviceLock(self.config.serial):
                pass

    async def test_model_repair_exactly_once_and_usage_reserved(self):
        store = self.make_store()
        good = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(decision({"type": "back"}))}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20}}
        transport = AsyncMock(side_effect=[{"choices": [{"finish_reason": "stop", "message": {"content": "broken"}}]}, good])
        brain = LifeBrain(self.config, store, transport)
        self.assertEqual((await brain.decide(observation(), {"com.reader": []}, []))["action"]["type"], "back")
        self.assertEqual(store.state["requests"], 2)
        self.assertEqual(store.state["input_tokens"], 100)
        transport = AsyncMock(return_value={"choices": []})
        with self.assertRaisesRegex(Blocked, "REPAIR_EXHAUSTED"):
            await LifeBrain(self.config, store, transport).decide(observation(), {"com.reader": []}, [])
        self.assertEqual(transport.await_count, 2)

    def test_prompt_bounded_and_vision_honest(self):
        store = self.make_store()
        obs = observation()
        obs.controls *= 200
        brain = LifeBrain(self.config, store)
        apps = {f"com.example.app{i}": [] for i in range(1000)}
        payload = brain.payload(obs, apps, [{"note": "很多内容" * 300}])
        user = payload["messages"][1]["content"]
        self.assertIsInstance(user, str)
        self.assertFalse(json.loads(user)["vision_available"])
        self.assertEqual(json.loads(user)["page_text_from_xml"][0]["text"], "阅读")
        self.assertLessEqual(len((payload["messages"][0]["content"] + user).encode()) + 256, self.config.max_input_tokens)
        path = self.root / "screen.png"
        path.write_bytes(b"test-png")
        obs.png_path = str(path)
        self.config.vision = True
        self.assertEqual(brain.payload(obs, apps, [])["messages"][1]["content"][1]["type"], "image_url")

    def test_request_and_money_budgets_persist(self):
        store = self.make_store()
        self.config.requests_hour = 1
        store.reserve_request(self.config)
        with self.assertRaisesRegex(Blocked, "REQUEST_BUDGET"):
            store.reserve_request(self.config)
        # A failed request remains a reservation, including across another SQLite connection.
        db = sqlite3.connect(self.config.data / "life.sqlite3")
        self.assertEqual(db.execute("SELECT count(*) FROM usage").fetchone()[0], 1)
        db.close()
        self.config.requests_hour = 20
        self.config.input_price = 1
        self.config.output_price = 1
        self.config.max_cost = .001
        with self.assertRaisesRegex(Blocked, "MONEY_BUDGET"):
            store.reserve_request(self.config)

    async def run_fixture(self, **kwargs):
        return await SessionLoop(self.config, self.root / "run", minutes=.04,
                                 phone_factory=FixturePhone, brain_factory=FixtureBrain, **kwargs).run()

    async def test_full_loop_pending_dispatch_observe_and_no_goal_claim(self):
        result = await self.run_fixture()
        self.assertEqual(result["status"], "completed_duration")
        events = [json.loads(x) for x in (self.root / "run/events.jsonl").read_text(encoding="utf-8").splitlines()]
        kinds = [e["kind"] for e in events]
        self.assertLess(kinds.index("pending"), kinds.index("action_dispatched"))
        self.assertIn("changed", kinds)
        self.assertIsNone(next(e["achieved"] for e in events if e["kind"] == "changed"))
        self.assertEqual(len(FixturePhone.instances[0].calls), 1)
        self.assertFalse((self.lockroot / "PENDING_ACTION.json").exists())

    async def test_unknown_dispatch_stops_once_and_blocks_new_session(self):
        FixturePhone.unknown = True
        result = await self.run_fixture()
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(len(FixturePhone.instances[0].calls), 1)
        self.assertTrue((self.lockroot / "PENDING_ACTION.json").exists())
        with self.assertRaises(Blocked):
            await SessionLoop(self.config, self.root / "another", phone_factory=FixturePhone).run()

    async def test_stop_interrupts_a_hung_model_independently(self):
        started = asyncio.Event()
        class SlowBrain:
            def __init__(self, *args):
                pass
            async def decide(self, *args):
                started.set()
                await asyncio.sleep(100)
        loop = SessionLoop(self.config, self.root / "run", phone_factory=FixturePhone, brain_factory=SlowBrain)
        task = asyncio.create_task(loop.run())
        await started.wait()
        now = time.monotonic()
        result = await asyncio.to_thread(stop, self.root / "run", False, 3)
        self.assertEqual(result["status"], "stopped")
        self.assertLess(time.monotonic() - now, 2)
        self.assertEqual((await task)["reason"], "STOP_REQUESTED")
        self.assertFalse(FixturePhone.instances[0].calls)

    async def test_action_budget_stops_before_second_dispatch(self):
        self.config.max_actions = 1
        class TapBrain(FixtureBrain):
            async def decide(self, obs, *args):
                return decision({"type": "tap", "control_id": obs.controls[0].control_id})
        result = await SessionLoop(self.config, self.root / "run", minutes=.08,
                                  phone_factory=FixturePhone, brain_factory=TapBrain).run()
        self.assertEqual(result["reason"], "ACTION_BUDGET_EXHAUSTED")
        self.assertEqual(len(FixturePhone.instances[0].calls), 1)

    def test_status_and_stop_are_local_only(self):
        store = self.make_store()
        store.finish("stopped", "offline")
        with patch.object(Config, "load", side_effect=AssertionError("profile access")), \
                patch("digitallife_v2.phone.asyncio.create_subprocess_exec", side_effect=AssertionError("process")):
            self.assertEqual(main(["status", "--session", str(store.directory)]), 0)
            self.assertEqual(main(["stop", "--session", str(store.directory), "--wait", "0"]), 0)
        with self.assertRaises(FileNotFoundError):
            status(self.root / "absent")
        self.assertFalse((self.root / "absent").exists())

    async def test_http_worker_uses_openai_wire_with_local_mock_only(self):
        received = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                value = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(decision({"type": "rest", "seconds": 1}))}}],
                         "usage": {"prompt_tokens": 10, "completion_tokens": 15}}
                raw = json.dumps(value).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.config.api_base = f"http://127.0.0.1:{server.server_port}/v1"
        store = self.make_store()
        result = await LifeBrain(self.config, store).decide(observation(), {"com.reader": []}, [])
        self.assertEqual(result["action"]["type"], "rest")
        self.assertEqual(received[0][0], "/v1/chat/completions")
        self.assertFalse(received[0][1]["stream"])
        self.assertNotIn("fake-offline-key", (store.directory / "events.jsonl").read_text())


if __name__ == "__main__":
    unittest.main()
