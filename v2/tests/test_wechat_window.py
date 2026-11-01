import tempfile,time,unittest,json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock,patch
from acceptance.wechat_voice_listen import WechatVoiceWindow,WindowSocialUI,eligible_voice
from digitallife_v2.safety import Blocked
from digitallife_v2.session import SessionStopped
from test_m1 import config

class WechatWindowTests(unittest.TestCase):
    def test_requested_test_group_does_not_accept_old_or_similar_group(self):
        now=time.time();event={'source_app':'com.tencent.mm','posted_at':now,'preview':'test 张三：[语音]'}
        self.assertTrue(eligible_voice(event,now-1,'test'))
        for preview in ('IAM电竞 张三：[语音]','test2 张三：[语音]','其他群 test [语音]'):
            self.assertFalse(eligible_voice({**event,'preview':preview},now-1,'test'))

    def test_late_confirmed_notification_is_accepted_only_with_original_cutoff(self):
        now=time.time();event={'source_app':'com.tencent.mm','posted_at':now-60,'preview':'测试联系人：[语音]'}
        self.assertFalse(eligible_voice(event,now))
        self.assertTrue(eligible_voice(event,now-600))
        self.assertFalse(eligible_voice({**event,'posted_at':now-601},now-600))
        self.assertFalse(eligible_voice({**event,'source_app':'com.tencent.mobileqq'},now-600))
        self.assertFalse(eligible_voice({**event,'preview':'普通文字'},now-600))

    def test_recovery_requires_recent_past_time_and_does_not_start_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg=config(Path(directory));now=time.time()
            window=WechatVoiceWindow(cfg,Path(directory)/'unused',6,now-120)
            self.assertEqual(window.opened_after,now-120);self.assertIsNone(window.store)
            for timestamp in (now+60,now-3601,float('nan')):
                with self.assertRaises(ValueError):WechatVoiceWindow(cfg,Path(directory)/'unused',6,timestamp)

    def test_confirmed_visible_group_has_no_synthetic_notification_route(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg=config(Path(directory))
            window=WechatVoiceWindow(cfg,Path(directory)/'unused',6,route_hint='test',confirmed_route=True)
            self.assertFalse(window.notification_route_used)
            self.assertEqual(window.source,'human_confirmed_existing_visible_voice')
            with self.assertRaises(ValueError):WechatVoiceWindow(cfg,Path(directory)/'unused',6,confirmed_route=True)


class WindowActualRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_readback_failure_keeps_observed_send_without_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            window=WechatVoiceWindow(config(Path(directory)),Path(directory),6,confirmed_binding_id='a'*64,route_hint='test')
            window.opened=1;window.bridge=object()
            window.store=SimpleNamespace(db=Mock())
            window.store.db.execute.return_value.fetchone.return_value=(json.dumps({'route_hint':'test'}),'测试正文')
            async def guarded(awaitable):return await awaitable
            window.guarded=guarded
            result={'outcome':'sent_observed','reply_format':'voice','outbound_id':'b'*32,'accepted':True}
            with patch('acceptance.wechat_voice_listen.WechatVoiceAgent.run',new=AsyncMock(return_value=result)) as send, \
                    patch('acceptance.wechat_voice_listen.WechatVoicePolicy.read',return_value={'account':'fixture'}), \
                    patch('acceptance.wechat_voice_listen.DesktopWechatTransport.call',new=AsyncMock(side_effect=Blocked('menu_missing'))):
                with self.assertRaisesRegex(SessionStopped,'READBACK_UNAVAILABLE'):await window.step()
            saved=json.loads((Path(directory)/'WECHAT_VOICE_RESULT.json').read_text(encoding='utf8'))
            self.assertEqual(saved['result'],result);self.assertFalse(saved['notification_route_used'])
            self.assertTrue((Path(directory)/'WECHAT_READBACK_FAILURE.json').exists())
            send.assert_awaited_once()

    async def test_notification_preview_cannot_override_actual_conversation(self):
        ui=WindowSocialUI.__new__(WindowSocialUI);ui.route_hint='test';ui.open_existing=False
        for title,accepted in (('test(3)',True),('IAM电竞(12)',False),('test2',False)):
            actual=(SimpleNamespace(route_hint=title),{},None)
            with patch('acceptance.wechat_voice_listen.AndroidSocialUI.resolve',new=AsyncMock(return_value=actual)):
                if accepted:self.assertIs(await ui.resolve(event={}),actual)
                else:
                    with self.assertRaisesRegex(Blocked,'OTHER_CONVERSATION'):await ui.resolve(event={})

    async def test_existing_route_opens_visible_thread_before_normal_resolution(self):
        ui=WindowSocialUI.__new__(WindowSocialUI);ui.route_hint='test';ui.open_existing=True
        ui.open_hint=AsyncMock();actual=(SimpleNamespace(route_hint='test(3)'),{},None)
        with patch('acceptance.wechat_voice_listen.AndroidSocialUI.resolve',new=AsyncMock(return_value=actual)):
            self.assertIs(await ui.resolve(event=None,binding_id=None),actual)
        ui.open_hint.assert_awaited_once_with('test')
