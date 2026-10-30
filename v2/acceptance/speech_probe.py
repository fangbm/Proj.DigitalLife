"""Visible own-app acoustic calibration. No contact, group, or outbound message."""
import argparse
import asyncio
import base64
import hashlib
import json
from pathlib import Path
import uuid
from digitallife_v2.config import Config
from digitallife_v2.bridge import PACKAGE
from digitallife_v2.session import SessionLoop,SessionStopped
from digitallife_v2.speech import SpeechEngine
from digitallife_v2.store import write_json
from digitallife_v2.safety import Blocked
from digitallife_v2.speech import inspect_wav


class SpeechProbe:
    def __init__(self,config,store,microphone='voice_communication'):self.config=config;self.store=store;self.stage=0;self.engine=SpeechEngine(config,store);self.microphone=microphone
    def button(self,obs,text):
        rows=[c for c in obs.controls if c.package==PACKAGE and c.text==text and c.clickable and c.enabled and not c.covered]
        if len(rows)!=1:raise SessionStopped('SPEECH_PROBE_CONTROL_UNAVAILABLE')
        return {'type':'tap','control_id':rows[0].control_id}
    async def decide(self,obs,apps,recent):
        bridge=getattr(self.store,'notification_client',None)
        if self.stage==1 and obs.foreground.endswith('.CalibrationActivity'):self.stage=2
        if self.stage==0:action={'type':'open_app','package_name':PACKAGE};self.stage=1
        elif self.stage==1:action=self.button(obs,'本机声音路径测试');self.stage=2
        elif self.stage==2:
            self.asset=await self.engine.synthesize('你好，我是数字生命。这是一条语音测试消息。')
            raw=Path(self.asset['path']).read_bytes();job=uuid.uuid4().hex
            result=await bridge.request('POST','/speech/import',{'job_id':job,'sha256':hashlib.sha256(raw).hexdigest(),
                'voice_id':self.asset['voice_id'],'wav_base64':base64.b64encode(raw).decode()})
            if result.get('state')!='ready' or result.get('sha256')!=self.asset['sha256']:raise SessionStopped('PHONE_SPEECH_IMPORT_FAILED')
            self.imported=result;action=self.button(obs,'普通麦克风声学测试' if self.microphone=='mic' else '开始本机声学测试');self.stage=3
        elif self.stage==3:
            result=await bridge.request('POST','/speech/calibration',{'fetch':True})
            if result.get('state')=='recording':action={'type':'rest','seconds':2}
            elif result.get('state')=='ready':
                raw=base64.b64decode(result['wav_base64'],validate=True)
                path=self.engine.cache/(uuid.uuid4().hex+'.wav');path.write_bytes(raw)
                try:
                    transcript=await self.engine.transcribe({'path':str(path),'sha256':hashlib.sha256(raw).hexdigest(),
                        'source_ref':'own_app_phone_speaker_microphone_calibration'},'zh-CN')
                except Blocked as error:
                    transcript={'text':'','available':False,'reason':str(error),'path':str(path)}
                text=transcript['text']
                matched=all(any(value in text for value in group) for group in [('你好',),('数字生命','數字生命'),('语音测试','語音測試')])
                try:captured=inspect_wav(path)[0]
                except Blocked as error:captured={'validation_error':str(error),'sha256':hashlib.sha256(raw).hexdigest()}
                capture_asset=self.engine.asset('capture',{'path':str(path),**captured,'source_ref':'own_app_acoustic_calibration'},600)
                playback=await bridge.request('POST','/speech/status',{})
                volume=await bridge.phone._text('shell','cmd','media_session','volume','--stream','3','--get')
                write_json(self.store.directory/'SPEECH_CALIBRATION.json',{'source':'LIVE_PHONE_OWN_APP_SPEAKER_TO_MICROPHONE',
                    'tts':self.asset,'phone_import':self.imported,'transcript':transcript,'short_phrase_matched':matched,
                    'captured_wav':captured,'microphone_source':self.microphone,
                    'capture_asset':capture_asset,'phone_playback':playback,'music_volume_after_recording':volume,
                    'phone_recording_details':{k:v for k,v in result.items() if k!='wav_base64'},
                    'social_messages_sent':0,'QQ_or_WeChat_recording_verified':False})
                self.stage=4;action={'type':'home'}
            else:
                write_json(self.store.directory/'SPEECH_CALIBRATION.json',{'source':'LIVE_PHONE_OWN_APP_SPEAKER_TO_MICROPHONE',
                    'short_phrase_matched':False,'phone_recording_details':result,'microphone_source':self.microphone,
                    'social_messages_sent':0,'QQ_or_WeChat_recording_verified':False})
                self.stage=4;action={'type':'home'}
        else:raise SessionStopped('SPEECH_CALIBRATION_DONE')
        return {'intent':'rest' if action['type']=='rest' else 'explore','action':action,'note':'Explicit own-app microphone calibration; no social messages'}


async def run(config,session,microphone='voice_communication'):
    return await SessionLoop(config,session,minutes=3,brain_factory=lambda c,s:SpeechProbe(c,s,microphone)).run()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--profile',default='find-x6');parser.add_argument('--session',required=True)
    parser.add_argument('--microphone-source',choices=('mic','voice_communication'),default='voice_communication')
    args=parser.parse_args();config=Config.load(args.profile)
    result=asyncio.run(run(config,args.session,args.microphone_source))
    evidence=Path(args.session)/'SPEECH_CALIBRATION.json'
    passed=bool(evidence.exists() and json.loads(evidence.read_text(encoding='utf-8')).get('short_phrase_matched'))
    print(json.dumps({**result,'calibration_passed':passed}));return 0 if passed and result['reason']=='SPEECH_CALIBRATION_DONE' else 2


if __name__=='__main__':raise SystemExit(main())
