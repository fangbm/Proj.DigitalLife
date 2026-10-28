import json,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from acceptance.social_inspect import Inspect
from digitallife_v2.events import EventHub
from digitallife_v2.session import SessionStopped
from digitallife_v2.store import LifeStore
from test_m1 import config,observation


class ReadOnlyInspectionTests(unittest.IsolatedAsyncioTestCase):
    async def run_read(self,complete):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cfg=config(root);directory=root/'session';directory.mkdir()
            store=LifeStore(cfg.data,str(directory),cfg.serial,'fixture')
            try:
                app='com.tencent.mm';marker='READ-MARKER-2026';text=marker+' Hello, please introduce yourself'
                EventHub(store,[app]).ingest({'package':app,'event_key':'fixture','posted_at':time.time(),'text':text})
                loop=SimpleNamespace(config=cfg);probe=Inspect(cfg,store,app,[],True,loop,marker)
                ui=SimpleNamespace(composer=lambda obs:None,ground_editor_bounds=AsyncMock(),act=AsyncMock(),
                    messages=lambda obs:[{'direction':'incoming','text':text if complete else marker+' Hello,'}])
                with patch('digitallife_v2.social_ui.AndroidSocialUI',return_value=ui):
                    reason='SOCIAL_CURRENT_MESSAGE_READ_VERIFIED' if complete else 'SOCIAL_READ_FULL_BODY_NOT_MATCHED'
                    with self.assertRaisesRegex(SessionStopped,reason):
                        await probe.decide(observation('live',foreground=app+'/.Chat'),{},[])
                ui.ground_editor_bounds.assert_awaited_once()
                if complete:
                    evidence=json.loads((directory/'M4_READ_READY.json').read_text(encoding='utf-8'))
                    self.assertFalse(evidence['notification_routing_verified']);self.assertEqual(evidence['outgoing_sends'],0)
                    self.assertEqual(ui.act.call_args.args[0],{'type':'home'})
                else:
                    self.assertFalse((directory/'M4_READ_READY.json').exists());ui.act.assert_not_awaited()
                self.assertEqual(store.db.execute('SELECT count(*) FROM social_outbox').fetchone()[0],0)
            finally:store.close()

    async def test_full_current_body_read_is_labeled_read_only_not_route_acceptance(self):await self.run_read(True)
    async def test_partial_body_never_creates_read_acceptance(self):await self.run_read(False)
