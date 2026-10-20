import asyncio
import tempfile
import time
import unittest
from pathlib import Path

from phonelife.devices import MockPhone
from phonelife.llm import MockGuiPlanner, MockPlanner, parse_object
from phonelife.loop import PhoneLife
from phonelife.models import Action, Decision
from phonelife.policy import ActionPolicy, PolicyDenied
from phonelife.storage import Store


class DataValidationTests(unittest.TestCase):
    def test_plan_is_bounded(self):
        with self.assertRaises(ValueError):
            Decision.from_dict({"kind": "explore", "wake_after_seconds": 0})

    def test_action_rejects_unknown_type(self):
        with self.assertRaises(ValueError):
            Action.from_dict({"type": "shell", "text": "whoami"})

    def test_json_parsing(self):
        self.assertEqual(parse_object('```json\n{"kind":"rest"}\n```')["kind"], "rest")


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.apps = frozenset({"com.android.chrome"})

    def test_real_denies_tap(self):
        with self.assertRaises(PolicyDenied):
            ActionPolicy("real", self.apps).check(Action(type="tap", x=20, y=20))

    def test_emulator_permits_bounded_tap(self):
        ActionPolicy("emulator", self.apps).check(Action(type="tap", x=20, y=20))

    def test_denies_unlisted_app(self):
        with self.assertRaises(PolicyDenied):
            ActionPolicy("mock", self.apps).check(
                Action(type="launch", package="com.bank.example")
            )

    def test_sensitive_text(self):
        with self.assertRaises(PolicyDenied):
            ActionPolicy("emulator", self.apps).check(
                Action(type="text", text="银行卡1234")
            )

    def test_shell_metacharacter_rejected(self):
        with self.assertRaises(PolicyDenied):
            ActionPolicy("emulator", self.apps).check(
                Action(type="text", text="hello; rm -rf /bad")
            )


class PersistenceAndLoopTests(unittest.TestCase):
    def test_mock_autonomy_and_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.db"
            store = Store(path)
            phone = MockPhone()
            agent = PhoneLife(store, "mock", phone, MockPlanner(), MockGuiPlanner(),
                              ActionPolicy("mock", frozenset({"com.android.chrome"})),
                              step_limit=5, action_delay=0)
            first = asyncio.run(agent.run_once())
            self.assertEqual(first["status"], "succeeded")
            self.assertEqual(first["steps"], 1)
            self.assertEqual(phone.current_app, "com.android.chrome")
            self.assertEqual(asyncio.run(agent.run_once())["status"], "sleeping")
            self.assertGreater(store.next_wake("mock"), time.time())
            store.close()

            reopened = Store(path)
            self.assertEqual(len(reopened.recent_memories("mock")), 1)
            agent.store = reopened
            second = asyncio.run(agent.run_once(force=True))
            self.assertEqual(second["decision"], "rest")
            self.assertEqual(len(reopened.recent_memories("mock")), 2)
            reopened.close()

    def test_lease_blocks_second_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "state.db")
            self.assertTrue(store.acquire("emulator", "a"))
            self.assertFalse(store.acquire("emulator", "b"))
            store.release("emulator", "a")
            self.assertTrue(store.acquire("emulator", "b"))
            store.close()


if __name__ == "__main__":
    unittest.main()

