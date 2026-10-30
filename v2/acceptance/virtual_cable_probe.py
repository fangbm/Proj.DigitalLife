"""Bounded local WAV roundtrip on VB-CABLE; no UI, default devices or sends."""
import argparse
from array import array
import json
from pathlib import Path
import subprocess
import time
import warnings

from digitallife_v2.speech import inspect_wav


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


def signal_match(expected, captured, np):
    if len(captured) < len(expected):
        return {'correlation': 0.0, 'full_source_coverage': False}
    size = 1 << (len(expected)+len(captured)-2).bit_length()
    convolution = np.fft.irfft(np.fft.rfft(captured, size)
                              * np.fft.rfft(expected[::-1], size), size)
    # Only full-source windows are eligible. A short matching prefix cannot pass.
    valid = convolution[len(expected)-1:len(captured)]
    offset = int(np.argmax(valid))
    actual = captured[offset:offset+len(expected)]
    energy = float(np.dot(expected, expected)*np.dot(actual, actual))
    correlation = float(np.dot(expected, actual)/(energy**.5)) if energy > 0 else 0.0
    return {'correlation': correlation, 'full_source_coverage': True,
            'offset_seconds': offset/48000}


def run(source, ffmpeg, directory):
    # Optional host dependencies belong in the local TTS environment.
    import numpy as np
    import sounddevice as sd
    from pycaw.pycaw import AudioUtilities, IAudioSessionControl2
    import os
    warnings.filterwarnings('ignore', category=UserWarning, module='pycaw')
    source = Path(source).resolve(strict=True)
    ffmpeg = Path(ffmpeg).resolve(strict=True)
    info, samples = inspect_wav(source, 20)
    mono = np.asarray(resample(samples, info['sample_rate']), dtype=np.float32)/32768
    output = np.repeat(mono[:, None], 2, axis=1)
    devices = sd.query_devices(); apis = sd.query_hostapis()
    render_name = 'CABLE Input (VB-Audio Virtual Cable)'
    capture_name = 'CABLE Output (VB-Audio Virtual Cable)'
    render = virtual_device(devices, apis, render_name, 'output')
    virtual_device(devices, apis, capture_name, 'input')
    endpoints = AudioUtilities.GetAllDevices()
    def endpoint(name):
        found = [d for d in endpoints if d.FriendlyName == name and d.state.value == 1]
        if len(found) != 1:raise ValueError('Unique active virtual endpoint required')
        return found[0]
    render_ep = endpoint(render_name); capture_ep = endpoint(capture_name)
    for ep in (render_ep, capture_ep):
        sessions = ep.AudioSessionManager.GetSessionEnumerator()
        for i in range(sessions.GetCount()):
            session = sessions.GetSession(i).QueryInterface(IAudioSessionControl2)
            if session.GetState() == 1 and session.GetProcessId() != os.getpid():
                raise ValueError('Virtual endpoint already in use; do not mix other audio')
    directory = Path(directory).resolve(); directory.mkdir(parents=True, exist_ok=False)
    capture = directory/'captured.wav'; result_path = directory/'ROUNDTRIP.json'
    old_mute = render_ep.EndpointVolume.GetMute(); process = None
    evidence = {'source': str(source), 'source_sha256': info['sha256'],
                'source_seconds': info['seconds'], 'capture': str(capture),
                'render_device': render_name, 'capture_device': capture_name,
                'physical_devices_used': False, 'default_devices_changed': False,
                'messages_sent': 0, 'accepted': False}
    try:
        with (directory/'capture.log').open('wb') as log:
            render_ep.EndpointVolume.SetMute(0, None)
            process = subprocess.Popen([str(ffmpeg), '-nostdin', '-hide_banner',
                '-loglevel', 'warning', '-f', 'dshow', '-audio_buffer_size', '50',
                '-i', 'audio='+capture_name, '-t', str(info['seconds']+2),
                '-ar', '48000', '-ac', '1', '-c:a', 'pcm_s16le', str(capture)],
                stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
            deadline = time.monotonic()+10
            while not capture.exists():
                if process.poll() is not None:raise RuntimeError('Virtual capture exited before ready')
                if time.monotonic() >= deadline:raise TimeoutError('Virtual capture not ready; check OS permission prompt')
                time.sleep(.05)
            time.sleep(.25)
            with sd.OutputStream(device=render, samplerate=48000, channels=2,
                                 dtype='float32', latency=.05) as stream:
                underflow = False
                for offset in range(0, len(output), 480):
                    underflow = bool(stream.write(output[offset:offset+480])) or underflow
                time.sleep(.2)
            process.wait(timeout=5)
            if process.returncode:raise RuntimeError('Virtual capture failed; inspect capture.log')
        captured_info, captured_samples = inspect_wav(capture, 23)
        actual = np.asarray(captured_samples, dtype=np.float32)/32768
        match = signal_match(mono, actual, np)
        evidence.update(match, capture_seconds=captured_info['seconds'],
                        capture_sha256=captured_info['sha256'], output_underflow=underflow)
        evidence['accepted'] = match['full_source_coverage'] and match['correlation'] >= .98 and not underflow
        return evidence
    except BaseException as error:
        evidence['error'] = type(error).__name__+': '+str(error)
        raise
    finally:
        try:
            if process and process.poll() is None:
                process.terminate(); process.wait(timeout=5)
        finally:
            sd.stop(); render_ep.EndpointVolume.SetMute(old_mute, None)
            evidence['virtual_mute_restored'] = render_ep.EndpointVolume.GetMute() == old_mute
            result_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--ffmpeg', required=True)
    parser.add_argument('--session', required=True)
    args = parser.parse_args()
    result = run(args.source, args.ffmpeg, args.session)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['accepted'] and result['virtual_mute_restored'] else 2


if __name__ == '__main__':raise SystemExit(main())
