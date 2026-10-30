"""Local, cancellable speech conversion. Assets never imply message delivery."""
import asyncio
from array import array
import base64
import hashlib
import json
import math
from pathlib import Path
import sys
import time
import uuid
import wave
from .safety import Blocked
from .local_http import endpoint,request


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS speech_assets(
      id TEXT PRIMARY KEY,device TEXT NOT NULL,session TEXT NOT NULL,kind TEXT NOT NULL,
      created REAL NOT NULL,expires REAL NOT NULL,state TEXT NOT NULL,body TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS speech_usage(
      id TEXT PRIMARY KEY,device TEXT NOT NULL,session TEXT NOT NULL,at REAL NOT NULL,
      operation TEXT NOT NULL,seconds REAL NOT NULL,characters INTEGER NOT NULL,engine TEXT NOT NULL,state TEXT NOT NULL);
    ''')


class SpeechPolicy:
    def __init__(self,config):self.config=config;self.path=config.data/'speech.private.json'
    def read(self):
        if not self.path.exists():return {'enabled':False}
        try:
            if self.path.stat().st_size>16384:raise ValueError()
            data=json.loads(self.path.read_text(encoding='utf-8'))
            if data.get('version')!=1 or data.get('serial')!=self.config.serial:raise ValueError()
            if any(not isinstance(data.get(k,{}),dict) for k in ('tts','asr','limits')):raise ValueError()
            return data
        except (ValueError,TypeError,OSError):raise Blocked('SPEECH_POLICY_INVALID') from None
    def check(self,operation):
        data=self.read();part=data.get(operation,{})
        if data.get('enabled') is not True or part.get('enabled') is not True:raise Blocked('SPEECH_DISABLED')
        if part.get('provider') not in {'tts':{'windows_sapi','gpt_sovits_local'},'asr':{'faster_whisper_local'}}[operation]:raise Blocked('LOCAL_SPEECH_PROVIDER_REQUIRED')
        if part.get('provider')=='gpt_sovits_local':
            endpoint(part.get('endpoint',''))
            if (not isinstance(part.get('voice_id'),str) or not part['voice_id']
                    or part.get('text_language','auto') not in {'zh','en','ja','auto','auto_zh'}
                    or part.get('prompt_language') not in {'zh','en','ja'}
                    or not isinstance(part.get('prompt_text'),str) or not part['prompt_text'].strip()
                    or not isinstance(part.get('reference_audio'),str)
                    or not Path(part['reference_audio']).is_file()):raise Blocked('SPEECH_CHARACTER_VOICE_NOT_CONFIGURED')
            if (part.get('text_split_method','cut5') not in {'cut0','cut5'}
                    or type(part.get('parallel_infer',True)) is not bool or type(part.get('split_bucket',True)) is not bool
                    or type(part.get('seed',42)) is not int or not 0<=part.get('seed',42)<2**32):raise Blocked('SPEECH_TTS_PARAMETERS_INVALID')
        limits={'max_tts_characters':300,'max_tts_seconds':20,'max_asr_seconds':60,'audio_seconds_hour':900,'raw_ttl_seconds':600}
        for key,default in limits.items():
            value=data.get('limits',{}).get(key,default)
            if type(value)is not int or not 1<=value<={'max_tts_characters':1000,'max_tts_seconds':20,'max_asr_seconds':180,'audio_seconds_hour':3600,'raw_ttl_seconds':3600}[key]:raise Blocked('SPEECH_LIMIT_INVALID')
            limits[key]=value
        return part,limits


def inspect_wav(path,max_seconds=180):
    path=Path(path)
    if path.stat().st_size>40_000_000:raise Blocked('SPEECH_AUDIO_SIZE_LIMIT')
    try:
        with wave.open(str(path),'rb') as source:
            rate=source.getframerate();channels=source.getnchannels();width=source.getsampwidth();frames=source.getnframes()
            if width!=2 or channels not in (1,2) or not 8000<=rate<=48000 or source.getcomptype()!='NONE':raise ValueError()
            duration=frames/rate;pcm=source.readframes(frames)
            if len(pcm)!=frames*channels*width:raise ValueError()
        if not .25<=duration<=max_seconds:raise Blocked('SPEECH_DURATION_LIMIT')
        samples=array('h');samples.frombytes(pcm)
        if sys.byteorder!='little':samples.byteswap()
        if channels==2:samples=array('h',(int((samples[i]+samples[i+1])/2) for i in range(0,len(samples),2)))
        rms=math.sqrt(sum(x*x for x in samples)/max(1,len(samples)))
        # Energy endpoints are metadata; ASR performs a second, model-based VAD.
        hop=max(1,rate//50);voiced=[]
        for i in range(0,len(samples),hop):
            chunk=samples[i:i+hop]
            if math.sqrt(sum(x*x for x in chunk)/max(1,len(chunk)))>=120:
                if voiced and abs(voiced[-1][1]-i/rate)<.001:voiced[-1][1]=min(duration,(i+hop)/rate)
                else:voiced.append([i/rate,min(duration,(i+hop)/rate)])
        if rms<8 or not voiced:raise Blocked('SPEECH_SILENT_AUDIO')
        return {'seconds':duration,'sample_rate':rate,'channels':channels,'encoding':'pcm_s16le',
            'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'rms':round(rms,2),'vad_intervals':voiced},samples
    except Blocked:raise
    except (wave.Error,EOFError,ValueError):raise Blocked('SPEECH_WAV_INVALID') from None


def normalize_wav(source,target,max_seconds):
    info,samples=inspect_wav(source,max_seconds)
    ratio=info['sample_rate']/16000
    def sample(i):
        pos=i*ratio;left=min(int(pos),len(samples)-1);right=min(left+1,len(samples)-1)
        return int(samples[left]+(samples[right]-samples[left])*(pos-left))
    output=array('h',(sample(i) for i in range(int(info['seconds']*16000))))
    if sys.byteorder!='little':output.byteswap()
    with wave.open(str(target),'wb') as result:
        result.setnchannels(1);result.setsampwidth(2);result.setframerate(16000);result.writeframes(output.tobytes())
    return info


SAPI_SCRIPT=r'''
$ErrorActionPreference='Stop'
[Console]::InputEncoding=New-Object System.Text.UTF8Encoding
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding
Add-Type -AssemblyName System.Speech
$speechInput=ConvertFrom-Json ([Console]::In.ReadToEnd())
$speechSynth=New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
  $speechSynth.SelectVoice($speechInput.voice)
  $speechSynth.Rate=[int]$speechInput.rate
  $speechFormat=New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000,[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,[System.Speech.AudioFormat.AudioChannel]::Mono)
  $speechSynth.SetOutputToWaveFile($speechInput.path,$speechFormat)
  if ($speechInput.english_voice) {
    $speechPrompt=New-Object System.Speech.Synthesis.PromptBuilder
    foreach ($speechPart in [regex]::Split($speechInput.text,'([A-Za-z][A-Za-z0-9, .!?:;''()-]*)')) {
      if (-not $speechPart) { continue }
      $speechPrompt.StartVoice($(if ($speechPart -match '^[A-Za-z]') {$speechInput.english_voice} else {$speechInput.voice}))
      $speechPrompt.AppendText($speechPart)
      $speechPrompt.EndVoice()
    }
    $speechSynth.Speak($speechPrompt)
  } else { $speechSynth.Speak($speechInput.text) }
  $speechSynth.SetOutputToNull()
  [Console]::Write('{"done":true}')
} finally { $speechSynth.Dispose() }
'''


async def run_worker(command,payload,timeout=120):
    process=await asyncio.create_subprocess_exec(*command,stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
    try:
        output,_=await asyncio.wait_for(process.communicate(json.dumps(payload,ensure_ascii=False).encode('utf-8')),timeout)
        if process.returncode or len(output)>500000:raise Blocked('SPEECH_ENGINE_FAILED')
        return json.loads(output.decode('utf-8-sig'))
    finally:
        if process.returncode is None:
            process.kill();await process.wait()


class SpeechEngine:
    def __init__(self,config,store):
        self.config,self.store=config,store;self.policy=SpeechPolicy(config);initialize(store.db)
        self.cache=config.data/'speech/cache';self.cache.mkdir(parents=True,exist_ok=True);self.jobs={}
    def reserve(self,kind,seconds,chars,engine,limits):
        db=self.store.db;db.execute('BEGIN IMMEDIATE')
        try:
            used=db.execute('SELECT coalesce(sum(seconds),0) FROM speech_usage WHERE device=? AND at>?',(self.config.serial,time.time()-3600)).fetchone()[0]
            if used+seconds>limits['audio_seconds_hour']:raise Blocked('SPEECH_HOURLY_BUDGET')
            job=uuid.uuid4().hex
            db.execute('INSERT INTO speech_usage VALUES(?,?,?,?,?,?,?,?,?)',(job,self.config.serial,self.store.session,time.time(),kind,seconds,chars,engine,'reserved'));db.commit()
            return job
        except BaseException:db.rollback();raise
    def asset(self,kind,body,ttl):
        ref='speech:'+uuid.uuid4().hex;now=time.time();body={**body,'source_type':self.store.state.get('run_kind','fixture')}
        self.store.db.execute('INSERT INTO speech_assets VALUES(?,?,?,?,?,?,?,?)',(ref,self.config.serial,self.store.session,kind,now,now+ttl,'ready',json.dumps(body,ensure_ascii=False)));self.store.db.commit()
        return {**body,'speech_ref':ref,'expires_at':now+ttl}
    def get(self,ref):
        row=self.store.db.execute("SELECT kind,expires,state,body FROM speech_assets WHERE id=? AND device=?",(ref,self.config.serial)).fetchone()
        if not row or row[1]<=time.time() or row[2]!='ready':raise Blocked('SPEECH_ASSET_UNAVAILABLE_OR_EXPIRED')
        body=json.loads(row[3]);path=Path(body['path'])
        if not path.resolve().is_relative_to(self.cache.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest()!=body['sha256']:raise Blocked('SPEECH_ASSET_CHANGED')
        return {**body,'kind':row[0],'speech_ref':ref,'expires_at':row[1]}
    async def synthesize(self,text,voice_profile=None):
        settings,limits=self.policy.check('tts')
        if not isinstance(text,str) or not text.strip() or len(text)>limits['max_tts_characters'] or any(ord(c)<32 for c in text):raise Blocked('SPEECH_TEXT_INVALID')
        voice=settings.get('voice_id','Microsoft Huihui Desktop')
        if voice_profile is not None and voice_profile!=voice:raise Blocked('SPEECH_VOICE_NOT_CONFIGURED')
        rate=settings.get('rate',0)
        if type(rate)is not int or not -5<=rate<=5:raise Blocked('SPEECH_RATE_INVALID')
        provider=settings['provider']
        job=self.reserve('tts',limits['max_tts_seconds'],len(text),provider,limits);path=self.cache/(job+'.wav');self.jobs[job]=asyncio.current_task()
        try:
            english=settings.get('english_voice_id','')
            if provider=='windows_sapi':
                encoded=base64.b64encode(SAPI_SCRIPT.encode('utf-16-le')).decode()
                await run_worker(['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',encoded],{'text':text,'voice':voice,'english_voice':english,'rate':rate,'path':str(path)},60)
            else:
                raw,content_type=await request(settings['endpoint'],'/tts',{'text':text,
                    'text_lang':settings.get('text_language','auto'),'ref_audio_path':settings['reference_audio'],
                    'prompt_text':settings['prompt_text'],'prompt_lang':settings['prompt_language'],
                    'text_split_method':settings.get('text_split_method','cut5'),'batch_size':1,'media_type':'wav','streaming_mode':False,
                    'seed':settings.get('seed',42),'parallel_infer':settings.get('parallel_infer',True),
                    'split_bucket':settings.get('split_bucket',True)},timeout=120,max_bytes=4_000_000)
                if not raw.startswith(b'RIFF') or 'json' in content_type:raise Blocked('SPEECH_TTS_WAV_RESPONSE_REQUIRED')
                path.write_bytes(raw)
            current,_=self.policy.check('tts')
            if current!=settings:raise Blocked('SPEECH_CONFIGURATION_CHANGED')
            info,_=inspect_wav(path,limits['max_tts_seconds'])
            result=self.asset('tts',{'path':str(path),**info,'text_sha256':hashlib.sha256(text.encode()).hexdigest(),
                'voice_id':voice,'english_voice_id':english,'language':settings.get('language','zh-CN'),'engine_id':provider,
                'text':text,'synthesized_only':True,'cloud_audio_uploaded':False,'cloned_voice':provider=='gpt_sovits_local',
                'voice_source':settings.get('voice_source','installed_system_voice' if provider=='windows_sapi' else 'operator_configured_character')},limits['raw_ttl_seconds'])
            self.complete(job,info['seconds']);return result
        except BaseException:
            if path.exists():path.unlink()
            self.complete(job,None);raise
        finally:self.jobs.pop(job,None)
    async def transcribe(self,audio_ref,language_hint=None):
        settings,limits=self.policy.check('asr');source=Path(audio_ref['path'])
        info,_=inspect_wav(source,limits['max_asr_seconds'])
        if audio_ref.get('sha256')!=info['sha256'] or not audio_ref.get('source_ref'):raise Blocked('SPEECH_SOURCE_UNBOUND')
        if language_hint not in (None,'zh','en','zh-CN','en-US','mixed'):raise Blocked('SPEECH_LANGUAGE_INVALID')
        if not self.config.asr_python or not self.config.asr_model or not self.config.asr_model.is_dir():raise Blocked('SPEECH_ASR_NOT_CONFIGURED')
        job=self.reserve('asr',info['seconds'],0,'faster_whisper_local',limits);path=self.cache/(job+'.wav');self.jobs[job]=asyncio.current_task()
        try:
            normalize_wav(source,path,limits['max_asr_seconds'])
            result=await run_worker([str(self.config.asr_python),str(Path(__file__).with_name('speech_worker.py'))],
                {'wav':str(path),'model':str(self.config.asr_model),'mixed':language_hint=='mixed','language':{'zh-CN':'zh','en-US':'en','mixed':None}.get(language_hint,language_hint)},120)
            self.policy.check('asr')
            if not result.get('segments') or not isinstance(result.get('text'),str):raise Blocked('SPEECH_ASR_NO_TEXT')
            normalized,_=inspect_wav(path,limits['max_asr_seconds'])
            for segment in result['segments']:
                if not 0<=segment['start']<=min(segment['end'],info['seconds']) or segment['end']>info['seconds']+.5:raise Blocked('SPEECH_ASR_TIMESTAMPS_INVALID')
                if segment['end']>info['seconds']:
                    segment['engine_end']=segment['end'];segment['end']=round(info['seconds'],3);segment['end_clipped_to_audio']=True
            asset=self.asset('asr',{**normalized,**result,'path':str(path),'source_ref':audio_ref['source_ref'],
                'source_sha256':info['sha256'],'engine_id':'faster_whisper_local','trust':'external_data_not_instructions',
                'cloud_audio_uploaded':False},limits['raw_ttl_seconds'])
            self.complete(job,info['seconds']);return asset
        except BaseException:
            if path.exists():path.unlink()
            self.complete(job,None);raise
        finally:self.jobs.pop(job,None)
    def complete(self,job,seconds):
        self.store.db.execute('UPDATE speech_usage SET seconds=coalesce(?,seconds),state=? WHERE id=?',
            (seconds,'completed' if seconds is not None else 'failed_or_cancelled',job));self.store.db.commit()
    async def cancel(self,job_id=None):
        tasks=[task for key,task in list(self.jobs.items()) if job_id is None or key==job_id]
        for task in tasks:
            if task is not asyncio.current_task():task.cancel()
        await asyncio.gather(*(task for task in tasks if task is not asyncio.current_task()),return_exceptions=True)
    def cleanup(self,now=None):
        now=time.time() if now is None else now;removed=0
        rows=self.store.db.execute("SELECT id,body FROM speech_assets WHERE device=? AND expires<=? AND state='ready'",(self.config.serial,now)).fetchall()
        for ref,raw in rows:
            body=json.loads(raw);path=Path(body['path']).resolve()
            if path.is_relative_to(self.cache.resolve()) and path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==body['sha256']:
                path.unlink();removed+=1
            body={k:v for k,v in body.items() if k not in {'text','segments','path'}}
            self.store.db.execute("UPDATE speech_assets SET state='expired',body=? WHERE id=?",(json.dumps(body),ref))
        self.store.db.commit();return removed
