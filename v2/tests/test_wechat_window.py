import tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from acceptance.wechat_voice_listen import WechatVoiceWindow,WindowSocialUI,eligible_voice
from digitallife_v2.safety import Blocked
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


class WindowActualRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_notification_preview_cannot_override_actual_conversation(self):
        ui=WindowSocialUI.__new__(WindowSocialUI);ui.route_hint='test'
        for title,accepted in (('test(3)',True),('IAM电竞(12)',False),('test2',False)):
            actual=(SimpleNamespace(route_hint=title),{},None)
            with patch('acceptance.wechat_voice_listen.AndroidSocialUI.resolve',new=AsyncMock(return_value=actual)):
                if accepted:self.assertIs(await ui.resolve(event={}),actual)
                else:
                    with self.assertRaisesRegex(Blocked,'OTHER_CONVERSATION'):await ui.resolve(event={})
