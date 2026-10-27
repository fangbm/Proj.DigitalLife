import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
from acceptance.social_receive import ReceiveLoop, MARKERS
from digitallife_v2.events import EventHub
from digitallife_v2.session import SessionStopped
from digitallife_v2.store import LifeStore
from test_m1 import config


class SocialReceiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_real_source_markers_required_and_unrelated_preview_not_offered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = config(root)
            directory = root / 'session'
            directory.mkdir()
            loop = ReceiveLoop(cfg, directory)
            loop.store = LifeStore(cfg.data, str(directory), cfg.serial, 'fixture')
            try:
                loop.hub = EventHub(loop.store, MARKERS)
                loop.bridge = object()
                loop.opened = time.time() - 1
                loop.poll_notifications = AsyncMock()
                loop.wait_for_event = AsyncMock()
                async def guarded(awaitable): return await awaitable
                loop.guarded = guarded
                offered = []
                async def decide(*args):
                    event = loop.store.inbound_context
                    offered.append(event['preview'])
                    return {'event_response': {'event_id': event['event_id'], 'decision': 'read_only', 'note': 'test'}}
                loop.brain = SimpleNamespace(decide=decide)
                def incoming(app, key, text):
                    return loop.hub.ingest({'package': app, 'event_key': key,
                        'posted_at': time.time(), 'title': '', 'text': text})
                apps = list(MARKERS)
                unrelated = incoming(apps[0], 'other', 'private unrelated text')
                incoming(apps[0], 'qq', MARKERS[apps[0]])
                await loop.step()
                self.assertEqual(len(offered), 1)
                self.assertFalse((directory / 'SOCIAL_READY.json').exists())
                incoming(apps[1], 'wechat', MARKERS[apps[1]])
                with self.assertRaisesRegex(SessionStopped, 'SOCIAL_RECEIVE_ACCEPTANCE_DONE'):
                    await loop.step()
                self.assertEqual(len(offered), 2)
                self.assertTrue((directory / 'SOCIAL_READY.json').exists())
                self.assertEqual(loop.store.state['dispatched'], 0)
                self.assertEqual(loop.store.db.execute('SELECT status FROM inbound_events WHERE id=?', (unrelated,)).fetchone()[0], 'pending')
            finally:
                loop.store.close()
