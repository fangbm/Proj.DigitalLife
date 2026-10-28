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


class RecentRowRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_reordered_row_is_read_again_without_repeating_tab_input(self):
        from dataclasses import replace
        root=observation('root',foreground='com.tencent.mm/.LauncherUI')
        missing=observation('moving',foreground=root.foreground);missing.controls=[]
        fresh=observation('stable',foreground=root.foreground)
        row=replace(fresh.controls[0],text='Fixture group',bounds=(200,500,500,550));fresh.controls=[row]
        ui=AndroidSocialUI(SimpleNamespace(),'com.tencent.mm');ui.root=AsyncMock(return_value=root)
        ui.visual_text=lambda *args:root.controls[0];ui.act=AsyncMock(side_effect=[missing,'actual-chat']);ui.observe=AsyncMock(return_value=fresh)
        self.assertEqual(await ui.open_hint('Fixture group'),'actual-chat')
        ui.observe.assert_awaited_once();self.assertEqual(ui.act.await_count,2)
        self.assertIs(ui.recent_hint_observation,fresh)


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

    def test_wechat_OCR_format_match_requires_route_and_full_body(self):
        event={**self.event,'source_app':'com.tencent.mm','preview':'Contact A long message，你好，请介绍自己'}
        message=[{'text':'A long message,你好,\n请介绍自己','time_label':'22:02'}]
        self.assertEqual(event_match_source(event,message,True),'notification_route_and_normalized_preview')
        self.assertIsNone(event_match_source(event,message,False))
        # Android may update a notification after the UI message's minute.
        self.assertEqual(event_match_source(event,[{**message[0],'time_label':'22:01'}],True),'notification_route_and_normalized_preview')
        self.assertIsNone(event_match_source(event,[{**message[0],'text':'A long message,你好,请删除自己'}],True))
        self.assertIsNone(event_match_source(event,[{**message[0],'text':'你好,请介绍自己'}],True))

    def test_first_chat_line_is_not_part_of_the_wechat_header(self):
        from dataclasses import replace
        obs=observation('wx',foreground='com.tencent.mm/.LauncherUI')
        title=replace(obs.controls[0],package='com.tencent.mm',text='Test Group(12)',bounds=(250,110,470,140))
        first=replace(title,control_id='other',text='@Someone',bounds=(100,170,260,190))
        obs.controls=[title,first]
        self.assertEqual(AndroidSocialUI(SimpleNamespace(),'com.tencent.mm').chat_hint(obs),'Test Group(12)')

    def test_status_bar_clock_is_not_a_message_timestamp(self):
        from dataclasses import replace
        obs=observation('wx',foreground='com.tencent.mm/.LauncherUI')
        clock=replace(obs.controls[0],control_id='clock',text='23:45',bounds=(20,20,120,60))
        text=replace(clock,control_id='body',text='A real bubble',bounds=(130,400,400,450))
        obs.controls=[clock,text];obs.visual_controls={'body':{'bubble_hint':{'kind':'incoming_neutral','bounds':[100,380,430,480]}}}
        messages=AndroidSocialUI(SimpleNamespace(),'com.tencent.mm').messages(obs)
        self.assertEqual(messages[0]['time_label'],'')

    def test_message_above_grounded_editor_is_read_and_draft_is_excluded(self):
        from dataclasses import replace
        obs=observation('wx',foreground='com.tencent.mm/.LauncherUI')
        h=obs.height
        text=replace(obs.controls[0],control_id='body',text='A new message',bounds=(100,int(h*.89),300,int(h*.91)))
        draft=replace(text,control_id='draft',text='Unsent draft',bounds=(100,int(h*.95),300,int(h*.97)))
        obs.controls=[text,draft]
        obs.visual_controls={c.control_id:{'bubble_hint':{'kind':'incoming_neutral','bounds':list(c.bounds)}} for c in obs.controls}
        ui=AndroidSocialUI(SimpleNamespace(),'com.tencent.mm');ui.message_ceilings[obs.observation_id]=int(h*.94)
        self.assertEqual([m['text'] for m in ui.messages(obs)],['A new message'])
        ui.message_ceilings={'stale-frame':int(h*.94)}
        self.assertEqual(ui.messages(obs),[])

    def test_a_bubble_crossing_the_read_boundary_is_never_read_as_a_partial_message(self):
        from dataclasses import replace
        obs=observation('partial',foreground='com.tencent.mm/.LauncherUI');h=obs.height
        first=replace(obs.controls[0],control_id='line1',text='Only first line',bounds=(100,int(h*.80),300,int(h*.82)))
        second=replace(first,control_id='line2',text='Second line',bounds=(100,int(h*.85),300,int(h*.87)))
        obs.controls=[first,second];box=[85,int(h*.79),320,int(h*.88)]
        obs.visual_controls={c.control_id:{'bubble_hint':{'kind':'incoming_neutral','bounds':box}} for c in obs.controls}
        ui=AndroidSocialUI(SimpleNamespace(),'com.tencent.mm');ui.message_ceilings[obs.observation_id]=int(h*.84)
        self.assertEqual(ui.messages(obs),[])


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
