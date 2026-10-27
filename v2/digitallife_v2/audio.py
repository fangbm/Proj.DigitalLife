"""Bounded Android playback capture and optional offline local transcription."""
import asyncio
import base64
import hashlib
from io import BytesIO
import json
from pathlib import Path
import struct
import sys
import uuid
import wave


def validate_wav(raw):
    if len(raw) > 650000:
        raise ValueError('AUDIO_SIZE_LIMIT')
    with wave.open(BytesIO(raw), 'rb') as source:
        if source.getnchannels() != 1 or source.getsampwidth() != 2 or source.getframerate() != 16000:
            raise ValueError('AUDIO_FORMAT_UNSUPPORTED')
        duration = source.getnframes() / 16000
        pcm = source.readframes(source.getnframes())
    if not .5 <= duration <= 20.1:
        raise ValueError('AUDIO_DURATION_LIMIT')
    values = [x[0] for x in struct.iter_unpack('<h', pcm)]
    rms = (sum(v*v for v in values) / max(1, len(values))) ** .5
    if rms < 8:
        raise ValueError('AUDIO_SILENT_OR_DISALLOWED')
    return duration, rms


async def start(loop, observation, seconds):
    if not getattr(loop.config, 'audio_enabled', False):
        return {'available': False, 'reason': 'audio_disabled'}
    if not loop.bridge:
        return {'available': False, 'reason': 'notification_bridge_unavailable'}
    package = observation.foreground.split('/')[0]
    try:
        result = await loop.guarded(loop.bridge.request('POST', '/audio/start', {'package': package, 'seconds': min(20, seconds)}))
        if not result.get('available'):
            return result
        uuid.UUID(result['capture_id'])
        if result.get('source_package') != package:
            raise ValueError('AUDIO_SOURCE_MISMATCH')
        loop.store.event('audio_capture_started', {'capture_id': result['capture_id'], 'source_package': package,
                         'source_observation': observation.observation_id, 'source_uid': result.get('source_uid')})
        return result
    except (OSError, ValueError, KeyError, EOFError, asyncio.TimeoutError):
        return {'available': False, 'reason': 'audio_start_unavailable_or_uncertain'}


async def transcribe(config, path):
    if not config.asr_python or not config.asr_model or not Path(config.asr_model).is_dir():
        return {'available': False, 'reason': 'local_asr_not_configured'}
    proc = await asyncio.create_subprocess_exec(str(config.asr_python), str(Path(__file__).resolve()),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        data = json.dumps({'wav': str(path), 'model': str(config.asr_model)}).encode()
        output, _ = await asyncio.wait_for(proc.communicate(data), 60)
        if proc.returncode or len(output) > 500000:
            raise ValueError('LOCAL_ASR_FAILED')
        result = json.loads(output)
        if not isinstance(result, dict) or not isinstance(result.get('segments'), list):
            raise ValueError('LOCAL_ASR_INVALID')
        return result
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


async def finish(loop, capture):
    if not capture.get('available'):
        return capture
    try:
        value = await loop.guarded(loop.bridge.request('POST', '/audio/finish', {'capture_id': capture['capture_id']}))
        if value.get('state') != 'ready':
            return {'available': False, 'reason': value.get('reason', 'audio_not_ready')}
        if value.get('capture_id') != capture['capture_id'] or value.get('source_package') != capture['source_package']:
            raise ValueError('AUDIO_SOURCE_MISMATCH')
        raw = base64.b64decode(value['wav_base64'], validate=True)
        duration, rms = validate_wav(raw)
        directory = loop.directory / 'audio'
        directory.mkdir(exist_ok=True)
        path = directory / (uuid.UUID(capture['capture_id']).hex + '.wav')
        with path.open('xb') as target:
            target.write(raw)
        checksum = hashlib.sha256(raw).hexdigest()
        loop.store.event('audio_captured', {'capture_id': capture['capture_id'], 'wav_sha256': checksum,
                         'seconds': duration, 'rms': round(rms, 2), 'source_package': capture['source_package']})
        transcription = await loop.guarded(transcribe(loop.config, path))
        body = {'capture_id': capture['capture_id'], 'wav_path': str(path), 'wav_sha256': checksum,
                'duration_seconds': duration, 'source_package': capture['source_package'],
                'capture_available': True, **transcription}
        path.with_suffix('.json').write_text(json.dumps(body, ensure_ascii=False), encoding='utf-8')
        loop.store.event('audio_transcribed', {'capture_id': capture['capture_id'], 'available': body.get('available', False),
                         'engine': body.get('engine'), 'language': body.get('language'), 'transcript_sha256': hashlib.sha256(json.dumps(body.get('segments', [])).encode()).hexdigest()})
        return body
    except (OSError, ValueError, KeyError, EOFError, asyncio.TimeoutError):
        return {'available': False, 'reason': 'audio_capture_or_transcription_unavailable'}


def worker():
    import os
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        value = json.loads(sys.stdin.buffer.read(32769))
        from faster_whisper import WhisperModel
        import numpy as np
        with wave.open(value['wav'], 'rb') as source:
            if source.getnchannels()!=1 or source.getsampwidth()!=2 or source.getframerate()!=16000:
                raise ValueError('PCM_16000_MONO_REQUIRED')
            samples=np.frombuffer(source.readframes(source.getnframes()),dtype=np.int16).astype(np.float32)/32768.0
        model = WhisperModel(value['model'], device='cpu', compute_type='int8', cpu_threads=2, local_files_only=True)
        segments, info = model.transcribe(samples, beam_size=1, condition_on_previous_text=False, vad_filter=False)
        result = []
        for segment in segments:
            if segment.no_speech_prob > .8 or segment.avg_logprob < -1.5:
                continue
            result.append({'start': round(segment.start, 2), 'end': round(segment.end, 2), 'text': segment.text[:1000],
                           'avg_logprob': round(segment.avg_logprob, 3), 'no_speech_prob': round(segment.no_speech_prob, 3)})
            if len(result) >= 30:
                break
        json.dump({'available': bool(result), 'engine': 'faster-whisper-local-cpu', 'language': info.language,
                   'segments': result, 'confidence': 'automatic_transcription_unverified'}, sys.stdout, ensure_ascii=False)
        return 0
    except Exception:
        return 2


if __name__ == '__main__':
    raise SystemExit(worker())
