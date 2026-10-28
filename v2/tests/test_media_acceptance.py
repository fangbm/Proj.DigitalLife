import unittest
from unittest.mock import AsyncMock
from acceptance.media_probe import Probe
from digitallife_v2.safety import Blocked
from digitallife_v2.session import SessionStopped


class MediaAcceptanceBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_pre_dispatch_refusal_stops_probe_without_automatic_replay(self):
        probe=Probe.__new__(Probe);probe._decide=AsyncMock(side_effect=Blocked('FINAL_SCENE_CHANGED_OR_EXPIRED'))
        with self.assertRaisesRegex(SessionStopped,'M5_ACCEPTANCE_BLOCKED:FINAL_SCENE_CHANGED_OR_EXPIRED'):
            await probe.decide(None,{},[])
        probe._decide.assert_awaited_once()
