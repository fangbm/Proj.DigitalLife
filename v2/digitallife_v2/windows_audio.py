"""Bounded WASAPI virtual-microphone routing and independent audio restoration."""
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
from array import array

def resample(samples, source_rate, target_rate=48000):
    # Python float positions avoid NumPy's Windows int32 arange multiplication.
    ratio = float(source_rate) / target_rate
    def sample(i):
        position = i * ratio
        left = min(int(position), len(samples)-1)
        right = min(left+1, len(samples)-1)
        return samples[left] + (samples[right]-samples[left]) * (position-left)
    return array('f', (sample(i) for i in range(round(len(samples)/ratio))))


def virtual_device(devices, apis, name, direction):
    found = [i for i, d in enumerate(devices) if d['name'] == name
             and apis[d['hostapi']]['name'] == 'Windows WASAPI'
             and d['max_'+direction+'_channels'] >= 2]
    if len(found) != 1:
        raise ValueError('Unique named virtual endpoint required; no default fallback')
    return found[0]


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
        self.monitor = subprocess.Popen([sys.executable, '-m', 'digitallife_v2.windows_audio', '--restore-lease', str(self.lease)],
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
        if not self.wait_done():raise Blocked('VIRTUAL_PLAYBACK_TIMEOUT')
        time.sleep(self.stream.latency+.15)
        if self.underflow:raise Blocked('VIRTUAL_PLAYBACK_UNDERFLOW')

    def wait_done(self):
        until=time.monotonic()+self.seconds+2
        while not self.done.wait(.05):
            if getattr(self,"stop",None) is not None and self.stop.is_set():raise Blocked("DESKTOP_JOB_CANCELLED")
            if time.monotonic()>until:return False
        return True

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


if __name__ == "__main__":
    if len(sys.argv)!=3 or sys.argv[1]!="--restore-lease":raise SystemExit(2)
    restore_lease(sys.argv[2])
