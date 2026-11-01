import tempfile,time,unittest
from pathlib import Path
from acceptance.wechat_voice_listen import WechatVoiceWindow,eligible_voice
from test_m1 import config

class WechatWindowTests(unittest.TestCase):
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
