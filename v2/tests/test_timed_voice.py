import tempfile
import unittest
from digitallife_v2.safety import Blocked
from digitallife_v2.timed_voice import VoiceIntent, send_timed_self_voice


class Clock:
    now = 0
    def __call__(self):return self.now


class UI:
    def __init__(self):self.calls=[];self.changed=False;self.fail_send=False;self.observed=5;self.cancelled_with_audio_active=False
    def require_self_chat(self):
        if self.changed:raise Blocked('SELF_CHAT_CHANGED')
    def voice_ids(self):return {1}
    def start_recording(self):self.calls.append('record')
    def send_recording(self):
        self.calls.append('send')
        if self.fail_send:raise TimeoutError('unknown GUI dispatch')
    def observe_new_voice(self, before):return {'seconds':self.observed} if self.observed else None
    def cancel_if_self_recording(self):
        self.cancelled_with_audio_active=not self.audio.released
        self.calls.append('cancel');return True


class Audio:
    def __init__(self, clock, ui):self.clock=clock;self.ui=ui;self.duration=4.2;self.released=False;self.switch=False
    def __enter__(self):return self
    def __exit__(self,*args):self.released=True
    def play(self):
        self.clock.now+=self.duration
        if self.switch:self.ui.changed=True


class TimedVoiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.clock=Clock();self.ui=UI();self.audio=Audio(self.clock,self.ui);self.ui.audio=self.audio
        self.intent=VoiceIntent(self.temp.name,'stable-self-source')
    def run_voice(self):return send_timed_self_voice(self.ui,self.audio,4,self.intent,clock=self.clock)
    def test_send_follows_audio_completion_without_another_brain_turn(self):
        result=self.run_voice()
        self.assertTrue(result['accepted']);self.assertEqual(result['recording_elapsed'],4.2)
        self.assertEqual(self.ui.calls,['record','send']);self.assertTrue(self.audio.released)
    def test_late_audio_completion_cancels_without_sending(self):
        self.audio.duration=8
        with self.assertRaises(Blocked):self.run_voice()
        self.assertEqual(self.ui.calls,['record','cancel']);self.assertTrue(self.audio.released)
        self.assertTrue(self.ui.cancelled_with_audio_active)
    def test_changed_conversation_does_not_send_to_the_new_target(self):
        self.audio.switch=True
        with self.assertRaises(Blocked):self.run_voice()
        self.assertNotIn('send',self.ui.calls);self.assertTrue(self.audio.released)
    def test_unknown_send_never_cancels_or_retries_the_dispatch(self):
        self.ui.fail_send=True
        with self.assertRaises(TimeoutError):self.run_voice()
        self.assertEqual(self.ui.calls,['record','send'])
        self.assertEqual(self.intent.body['state'],'delivery_unknown')
        with self.assertRaises(Blocked):VoiceIntent(self.temp.name,'stable-self-source')
    def test_observed_sixty_second_message_is_not_duration_acceptance(self):
        self.ui.observed=60
        result=self.run_voice();self.assertFalse(result['accepted'])
        self.assertEqual(self.intent.body['state'],'sent_observed')
    def test_missing_receipt_stays_unknown_and_keeps_the_intent(self):
        self.ui.observed=None
        self.assertEqual(self.run_voice()['outcome'],'delivery_unknown')
        with self.assertRaises(Blocked):VoiceIntent(self.temp.name,'stable-self-source')
    def test_invalid_duration_never_opens_audio_or_records(self):
        with self.assertRaises(Blocked):
            send_timed_self_voice(self.ui,self.audio,float('nan'),self.intent,clock=self.clock)
        self.assertEqual(self.ui.calls,[]);self.assertFalse(self.audio.released)
    def test_cleanup_failure_preserves_observed_delivery_without_replay(self):
        class BrokenRelease(Audio):
            def __exit__(self,*args):raise OSError('restore failed')
        self.audio=BrokenRelease(self.clock,self.ui);self.ui.audio=self.audio
        with self.assertRaises(OSError):self.run_voice()
        self.assertEqual(self.intent.body['state'],'sent_observed')
        self.assertEqual(self.ui.calls,['record','send'])
