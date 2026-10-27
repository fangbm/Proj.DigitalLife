from datetime import datetime
import unittest
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock
from digitallife_v2.social import CHINA
from digitallife_v2.social_ui import event_match_source, AndroidSocialUI
from digitallife_v2.events import EventHub
from digitallife_v2.safety import Blocked
from digitallife_v2.store import LifeStore
from test_m1 import config, observation
import time


class EventRouteTests(unittest.TestCase):
    def setUp(self):
        self.event={'preview':'QQ 已隐藏敏感通知内容','posted_at':datetime(2026,10,27,22,2,tzinfo=CHINA).timestamp()}
        self.incoming=[{'text':'Actual message','time_label':'22:02'}]

    def test_hidden_preview_requires_actual_route_and_matching_UI_minute(self):
        self.assertEqual(event_match_source(self.event,self.incoming,True),'package_owned_notification_route_and_UI_post_minute')
        self.assertIsNone(event_match_source(self.event,self.incoming,False))
        self.assertIsNone(event_match_source(self.event,[{'text':'Unrelated old chat','time_label':'21:00'}],True))

    def test_visible_preview_must_match_actual_message_instead_of_display_name(self):
        self.assertEqual(event_match_source({**self.event,'preview':'QQ Actual message'},self.incoming,False),'notification_preview_exact')
        self.assertIsNone(event_match_source({**self.event,'preview':'QQ A different message'},self.incoming,True))


class RoutePreflightTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_route_is_rejected_before_any_pending_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);cfg=config(root);session=root/'session';session.mkdir()
            store=LifeStore(cfg.data,str(session),cfg.serial,'fixture')
            try:
                hub=EventHub(store,['com.tencent.mobileqq'])
                eid=hub.ingest({'package':'com.tencent.mobileqq','event_key':'fixture-route','posted_at':time.time(),'title':'QQ','text':'A message'})
                async def guarded(awaitable):return await awaitable
                loop=SimpleNamespace(store=store,config=cfg,guarded=guarded,
                    bridge=SimpleNamespace(request=AsyncMock(return_value={'available':False})),send_operation=AsyncMock())
                ui=AndroidSocialUI(loop,'com.tencent.mobileqq');ui.observe=AsyncMock(return_value=observation('home',foreground='com.android.launcher/.Launcher'))
                with self.assertRaisesRegex(Blocked,'SOCIAL_NOTIFICATION_ROUTE_UNAVAILABLE'):
                    await ui.resolve(event={'event_id':eid,'source_app':'com.tencent.mobileqq'})
                loop.send_operation.assert_not_awaited()
                self.assertFalse(store.db.execute("SELECT 1 FROM events WHERE kind='pending'").fetchone())
            finally:store.close()
