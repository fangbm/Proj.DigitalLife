"""Offline Whisper worker; audio is local and transcription stays untrusted."""
import json
import math
import os
import re
import sys
import wave


def main():
    os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
    sys.stdout.reconfigure(encoding='utf-8')
    value=json.loads(sys.stdin.buffer.read(32769))
    import numpy as np
    from faster_whisper import WhisperModel
    with wave.open(value['wav'],'rb') as source:
        if source.getnchannels()!=1 or source.getsampwidth()!=2 or source.getframerate()!=16000:raise ValueError('PCM_16000_MONO_REQUIRED')
        samples=np.frombuffer(source.readframes(source.getnframes()),dtype=np.int16).astype(np.float32)/32768
    model=WhisperModel(value['model'],device='cpu',compute_type='int8',cpu_threads=2,local_files_only=True)
    segments,info=model.transcribe(samples,language=value.get('language'),beam_size=3,condition_on_previous_text=False,
        vad_filter=True,vad_parameters={'min_silence_duration_ms':300})
    result=[];complete=True
    for s in segments:
        result.append({'start':round(s.start,3),'end':round(s.end,3),'text':s.text.strip(),
            'confidence_heuristic':round(math.exp(min(0,s.avg_logprob)),3),
            'avg_logprob':round(s.avg_logprob,3),'no_speech_probability':round(s.no_speech_prob,3),
            'uncertain':s.avg_logprob<-.8 or s.no_speech_prob>.5})
        if len(result)>=100:complete=False;break
    text=''.join(s['text'] for s in result)
    mixed_missing=bool(value.get('mixed') and not (re.search('[A-Za-z]',text) and re.search('[\u3400-\u9fff]',text)))
    json.dump({'text':text,'language':info.language,
        'language_probability':round(info.language_probability,3),'segments':result,'complete':complete,
        'uncertain':mixed_missing or any(s['uncertain'] for s in result),
        'uncertainty_reason':'mixed_language_hint_not_reflected' if mixed_missing else None,
        'confidence_kind':'model_heuristics_not_calibrated_probability'},sys.stdout,ensure_ascii=False)


if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit(2)
