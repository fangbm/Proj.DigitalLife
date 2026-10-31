"""One bounded native self-voice action; GUI and audio are injected adapters."""
import hashlib
import json
import math
from pathlib import Path
import time

from .safety import Blocked


class VoiceIntent:
    def __init__(self, data, intent):
        if not isinstance(intent, str) or not 1 <= len(intent) <= 160:
            raise Blocked('DESKTOP_VOICE_INTENT_REQUIRED')
        directory = Path(data)/'windows_wechat_intents'
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory/(hashlib.sha256(intent.encode()).hexdigest()+'.json')
        self.body = {'intent': intent, 'target': '文件传输助手', 'state': 'prepared'}
        try:
            with self.path.open('x', encoding='utf8') as f:
                json.dump(self.body, f, ensure_ascii=False)
        except FileExistsError:
            raise Blocked('DESKTOP_VOICE_ALREADY_ATTEMPTED_NO_REPLAY') from None

    def update(self, state, **evidence):
        self.body.update(state=state, updated_at=time.time(), **evidence)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.body, ensure_ascii=False, indent=2), encoding='utf8')
        temporary.replace(self.path)


def send_timed_self_voice(ui, audio, seconds, intent, *, clock=time.monotonic):
    if not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not .25 <= seconds <= 20:
        raise Blocked('DESKTOP_VOICE_DURATION_INVALID')
    attempted = False
    recording = False
    cancelled = False
    try:
        ui.require_self_chat()
        before = ui.voice_ids()
        with audio:
            try:
                # All loading, resampling and device setup happens before recording.
                ui.require_self_chat()
                started = clock()
                recording = True
                ui.start_recording()
                intent.update('recording', source_seconds=seconds)
                audio.play()
                elapsed = clock()-started
                if elapsed > seconds+3:
                    raise Blocked('DESKTOP_VOICE_RECORDING_DEADLINE')
                ui.require_self_chat()
                intent.update('dispatch_attempted', recorded_seconds=elapsed)
                attempted = True
                ui.send_recording()
                observed = ui.observe_new_voice(before)
                if not observed:
                    intent.update('delivery_unknown')
                    return {'outcome': 'delivery_unknown', 'accepted': False}
                duration = observed['seconds']
                passed = max(1, math.floor(seconds)-1) <= duration <= math.ceil(seconds)+3
                intent.update('sent_observed', observed=observed, duration_in_range=passed)
                return {'outcome': 'sent_observed', 'accepted': passed,
                        'native_voice_seconds': duration, 'source_seconds': seconds,
                        'recording_elapsed': elapsed, 'content_independently_verified': False}
            except BaseException:
                if recording and not attempted:
                    try: cancelled = ui.cancel_if_self_recording()
                    except Exception: pass
                    recording = False
                raise
    except BaseException as error:
        if recording and not attempted:
            try: cancelled = ui.cancel_if_self_recording()
            except Exception: pass
        state = ('sent_observed' if intent.body['state'] == 'sent_observed'
                 else 'delivery_unknown' if attempted else 'failed_before_send')
        intent.update(state,
                      reason=str(error)[:200], cancelled_before_send=cancelled)
        raise
