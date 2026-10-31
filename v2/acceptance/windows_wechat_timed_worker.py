"""Optional Windows adapters for the fixed-duration File Transfer Assistant test."""
from contextlib import suppress
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import warnings

from digitallife_v2.safety import Blocked
from digitallife_v2.speech import inspect_wav
from digitallife_v2.timed_voice import VoiceIntent, send_timed_self_voice
from acceptance.virtual_cable_probe import resample, virtual_device

PROGRAM = r'C:\Program Files\Tencent\Weixin\Weixin.exe'
CAPTURE = 'CABLE Output (VB-Audio Virtual Cable)'
RENDER = 'CABLE Input (VB-Audio Virtual Cable)'


def audio_objects():
    import comtypes
    from pycaw.pycaw import AudioUtilities
    from pycaw.api.mmdeviceapi import IMMDeviceEnumerator
    from pycaw.api.policyconfig import IPolicyConfig
    from pycaw.constants import CLSID_MMDeviceEnumerator, CLSID_CPolicyConfigClient
    enumerator = comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_INPROC_SERVER)
    policy = comtypes.CoCreateInstance(CLSID_CPolicyConfigClient, IPolicyConfig, comtypes.CLSCTX_INPROC_SERVER)
    devices = AudioUtilities.GetAllDevices()
    def endpoint(name):
        found = [d for d in devices if d.FriendlyName == name and d.state.value == 1]
        if len(found) != 1:raise Blocked('UNIQUE_VIRTUAL_AUDIO_ENDPOINT_REQUIRED')
        return found[0]
    return enumerator, policy, devices, endpoint(CAPTURE), endpoint(RENDER)


def restore_lease(path):
    """Independent audio-only watchdog. It never reads or operates application UI."""
    warnings.filterwarnings('ignore', category=UserWarning, module='pycaw')
    path = Path(path); body = json.loads(path.read_text(encoding='utf8'))
    deadline = time.monotonic()+max(0, min(45, body['expires']-time.time()))
    stop = path.with_suffix('.stop')
    while not stop.exists() and time.monotonic() < deadline:time.sleep(.05)
    enumerator, policy, _, capture, render = audio_objects()
    if capture.id != body['target']:raise Blocked('VIRTUAL_AUDIO_ENDPOINT_CHANGED')
    for role, original in body['original'].items():
        # Respect a device change made by the user during the test.
        if enumerator.GetDefaultAudioEndpoint(1, int(role)).GetId() == capture.id:
            policy.SetDefaultEndpoint(original, int(role))
    if render.EndpointVolume.GetMute() == 0:
        render.EndpointVolume.SetMute(body['original_mute'], None)
    body['defaults_restored'] = all(enumerator.GetDefaultAudioEndpoint(1, int(r)).GetId() == v
                                    for r, v in body['original'].items())
    body['mute_restored'] = render.EndpointVolume.GetMute() == body['original_mute']
    body['watchdog_finished'] = True
    path.write_text(json.dumps(body, indent=2), encoding='utf8')


class VirtualAudio:
    def __init__(self, source, expected_hash, directory):
        import numpy as np
        import sounddevice as sd
        self.np, self.sd = np, sd
        info, samples = inspect_wav(source, 20)
        if info['sha256'] != expected_hash:raise Blocked('DESKTOP_VOICE_SOURCE_CHANGED')
        self.seconds = info['seconds']
        mono = np.asarray(resample(samples, info['sample_rate']), dtype=np.float32)/32768
        self.output = np.repeat(mono[:, None], 2, axis=1)
        self.device = virtual_device(sd.query_devices(), sd.query_hostapis(), RENDER, 'output')
        self.directory = Path(directory); self.lease = self.directory/'AUDIO_LEASE.json'
        self.monitor = None; self.stream = None; self.go = False; self.position = 0
        self.done = threading.Event(); self.underflow = False; self.capture_owners = []

    def __enter__(self):
        import os
        from pycaw.pycaw import IAudioSessionControl2
        self.enumerator, policy, devices, capture, render = audio_objects()
        # No other microphone user is interrupted by a default-device change.
        for d in devices:
            if d.state.value != 1 or not d.id.startswith('{0.0.1.'):continue
            with suppress(OSError):
                sessions = d.AudioSessionManager.GetSessionEnumerator()
                for i in range(sessions.GetCount()):
                    session = sessions.GetSession(i).QueryInterface(IAudioSessionControl2)
                    if session.GetState() == 1 and session.GetProcessId() != os.getpid():
                        raise Blocked('OTHER_AUDIO_CAPTURE_ACTIVE')
        original = {r:self.enumerator.GetDefaultAudioEndpoint(1, r).GetId() for r in range(3)}
        body = {'original':original, 'target':capture.id, 'original_mute':render.EndpointVolume.GetMute(),
                'expires':time.time()+45, 'defaults_restored':False, 'mute_restored':False}
        self.lease_deadline = time.monotonic()+45
        self.lease.write_text(json.dumps(body, indent=2), encoding='utf8')
        self.monitor_log = (self.directory/'lease.log').open('wb')
        self.monitor = subprocess.Popen([sys.executable, '-m', 'acceptance.windows_wechat_timed_worker', '--restore-lease', str(self.lease)],
            stdout=self.monitor_log, stderr=self.monitor_log, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            for role in original:policy.SetDefaultEndpoint(capture.id, role)
            render.EndpointVolume.SetMute(0, None)
            self.capture, self.render = capture, render
            self.stream = self.sd.OutputStream(device=self.device, samplerate=48000, channels=2,
                dtype='float32', blocksize=480, latency=.05, callback=self.callback)
            self.stream.start()
            return self
        except BaseException:
            self.close()
            raise

    def callback(self, outdata, frames, timing, status):
        outdata.fill(0)
        if self.go and status.output_underflow:self.underflow = True
        if not self.go:return
        count = min(frames, len(self.output)-self.position)
        outdata[:count] = self.output[self.position:self.position+count]
        self.position += count
        if self.position == len(self.output):self.done.set()

    def play(self):
        import psutil
        from pycaw.pycaw import IAudioSessionControl2
        if time.monotonic()+self.seconds+3 > self.lease_deadline:
            raise Blocked('VIRTUAL_MICROPHONE_LEASE_TOO_SHORT')
        if any(self.enumerator.GetDefaultAudioEndpoint(1, r).GetId() != self.capture.id for r in range(3)):
            raise Blocked('VIRTUAL_MICROPHONE_LEASE_NOT_ACTIVE')
        until = time.monotonic()+1
        while not self.capture_owners and time.monotonic() < until:
            sessions = self.capture.AudioSessionManager.GetSessionEnumerator()
            for i in range(sessions.GetCount()):
                session = sessions.GetSession(i).QueryInterface(IAudioSessionControl2)
                if session.GetState() == 1:
                    pid = session.GetProcessId()
                    if Path(psutil.Process(pid).exe()).resolve() != Path(PROGRAM).resolve():
                        raise Blocked('UNEXPECTED_VIRTUAL_CAPTURE_OWNER')
                    self.capture_owners.append(pid)
            if not self.capture_owners:time.sleep(.05)
        if not self.capture_owners:raise Blocked('WECHAT_VIRTUAL_MICROPHONE_NOT_RECORDING')
        self.go = True
        if not self.done.wait(timeout=self.seconds+2):raise Blocked('VIRTUAL_PLAYBACK_TIMEOUT')
        time.sleep(self.stream.latency+.15)
        if self.underflow:raise Blocked('VIRTUAL_PLAYBACK_UNDERFLOW')

    def close(self):
        if self.stream:
            with suppress(Exception):self.stream.stop(); self.stream.close()
        self.lease.with_suffix('.stop').write_text('stop', encoding='ascii')
        if self.monitor:
            with suppress(subprocess.TimeoutExpired):self.monitor.wait(timeout=5)
            self.monitor_log.close()
        body = json.loads(self.lease.read_text(encoding='utf8'))
        if not body.get('defaults_restored') or not body.get('mute_restored'):
            restore_lease(self.lease)
            body = json.loads(self.lease.read_text(encoding='utf8'))
        if not body.get('defaults_restored') or not body.get('mute_restored'):
            raise Blocked('DESKTOP_AUDIO_RESTORE_UNCONFIRMED')

    def __exit__(self, *args):self.close()


class FileAssistantUI:
    def __init__(self):
        import psutil
        from pywinauto import Desktop
        self.desktop = Desktop(backend='uia')
        windows = [w for w in self.desktop.windows(title='微信')
                   if Path(psutil.Process(w.element_info.process_id).exe()).resolve() == Path(PROGRAM).resolve()]
        if len(windows) != 1:raise Blocked('ONE_WECHAT_WINDOW_REQUIRED')
        self.window = windows[0]
        self.require_self_chat()
        self.window.set_focus()

    def require_self_chat(self):
        controls = self.window.descendants()
        if any(c.element_info.automation_id == 'login_layout_' and c.is_visible() for c in controls):
            raise Blocked('WECHAT_MANUAL_LOGIN_REQUIRED')
        names = [c.element_info.name for c in controls
                 if c.element_info.automation_id.endswith('.current_chat_name_label')]
        if names != ['文件传输助手']:raise Blocked('FILE_TRANSFER_ASSISTANT_ONLY')
        drafts = [c for c in controls
                  if c.element_info.automation_id == 'chat_input_field']
        if len(drafts) != 1 or drafts[0].get_value().strip():raise Blocked('EMPTY_SELF_CHAT_DRAFT_REQUIRED')

    def button(self, name, *, optional=False):
        found = [b for b in self.window.descendants(control_type='Button')
                 if (b.element_info.name.startswith('发语音') if name == 'record' else b.element_info.name == name)]
        if optional and not found:return None
        if len(found) != 1:raise Blocked('UNIQUE_WECHAT_VOICE_CONTROL_REQUIRED')
        return found[0]

    def start_recording(self):
        if self.button('发送语音', optional=True):raise Blocked('EXISTING_RECORDING_MUST_BE_HANDLED_MANUALLY')
        self.button('record').click_input()
        until = time.monotonic()+2
        while time.monotonic() < until:
            if self.button('发送语音', optional=True):return
            time.sleep(.05)
        raise Blocked('WECHAT_RECORDING_NOT_READY')

    def send_recording(self):self.button('发送语音').click_input()

    def cancel_if_self_recording(self):
        self.require_self_chat()
        if self.button('发送语音', optional=True):
            self.button('取消').click_input(); return True
        return False

    def voices(self):
        result = {}
        for row in self.window.descendants(control_type='ListItem'):
            match = re.match(r'^语音\s*(\d+)\s*["″]?\s*秒', row.element_info.name)
            if match:result[tuple(row.element_info.runtime_id)] = {'seconds':int(match[1])}
        return result

    def voice_ids(self):return set(self.voices())

    def observe_new_voice(self, before):
        until = time.monotonic()+4
        while time.monotonic() < until:
            self.require_self_chat()
            rows = [body for key, body in self.voices().items() if key not in before]
            if len(rows) == 1:return rows[0]
            if len(rows) > 1:return None
            time.sleep(.1)
        return None


def main():
    warnings.filterwarnings('ignore', category=UserWarning, module='pycaw')
    if len(sys.argv) == 3 and sys.argv[1] == '--restore-lease':
        restore_lease(sys.argv[2]); return 0
    value = json.loads(sys.stdin.buffer.read(32769))
    directory = Path(value['directory']).resolve()
    intent = VoiceIntent(value['data'], value['intent'])
    try:
        audio = VirtualAudio(value['source'], value['sha256'], directory)
        ui = FileAssistantUI()
        result = send_timed_self_voice(ui, audio, audio.seconds, intent)
    except BaseException as error:
        (directory/'WORKER_FAILURE.json').write_text(json.dumps({
            'reason':str(error)[:1000], 'intent_state':intent.body['state'],
            'automatic_retry':False}, ensure_ascii=False, indent=2), encoding='utf8')
        raise
    result.update(target='文件传输助手', speaker_used=False, physical_microphone_used=False,
                  virtual_capture_owner_pids=audio.capture_owners, intent_path=str(intent.path))
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':raise SystemExit(main())
