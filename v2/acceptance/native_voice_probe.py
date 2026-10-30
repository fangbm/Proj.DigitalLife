"""One native voice message to WeChat's own file-transfer helper only."""
import argparse
import asyncio
import base64
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import uuid
from digitallife_v2.config import Config
from digitallife_v2.session import SessionLoop,SessionStopped
from digitallife_v2.safety import Blocked
from digitallife_v2.speech import SpeechEngine
from digitallife_v2.social_ui import AndroidSocialUI,WX
from digitallife_v2.store import write_json
from digitallife_v2.visual import add_control
from digitallife_v2.bridge import PACKAGE


def own_helper(obs):
    return obs.foreground.split('/')[0]==WX and any(c.text=='文件传输助手' and c.bounds[3]<obs.height*.12 for c in obs.controls)


class NativeVoiceProbe:
    def __init__(self,config,store,loop):
        self.config,self.store,self.loop=config,store,loop;self.stage=-2;self.engine=SpeechEngine(config,store)
    async def decide(self,obs,apps,recent):
        if self.stage==-2:
            if not own_helper(obs):raise SessionStopped('OWN_FILETRANSFER_HELPER_REQUIRED')
            self.stage=-1
            return {'intent':'explore','action':{'type':'open_app','package_name':PACKAGE},'note':'Start visible bounded playback service from own foreground app'}
        if self.stage==-1:
            if obs.foreground.split('/')[0]!=PACKAGE:raise SessionStopped('OWN_SPEECH_APP_REQUIRED')
            rows=[c for c in obs.controls if c.text=='开启本机语音播放服务' and c.clickable and c.enabled]
            if not rows and obs.foreground.endswith('.CalibrationActivity'):
                return {'intent':'explore','action':{'type':'back'},'note':'Return from completed own calibration to playback setup'}
            if len(rows)!=1:raise SessionStopped('PLAYBACK_SERVICE_CONTROL_UNAVAILABLE')
            self.stage=0
            return {'intent':'explore','action':{'type':'tap','control_id':rows[0].control_id},'note':'Enable finite foreground playback and temporary test volume'}
        if self.stage==0:
            status=await self.loop.bridge.request('GET','/status')
            if not status.get('speech_playback_service',{}).get('active'):raise SessionStopped('FOREGROUND_PLAYBACK_UNAVAILABLE')
            self.stage=1
            return {'intent':'explore','action':{'type':'open_app','package_name':WX},'note':'Return to the already-open own file-transfer helper'}
        if not own_helper(obs):raise SessionStopped('OWN_FILETRANSFER_HELPER_REQUIRED')
        if self.stage==1:
            intent=self.store.directory/'SELF_VOICE_INTENT.json'
            if intent.exists():raise SessionStopped('SELF_VOICE_ALREADY_ATTEMPTED_NO_REPLAY')
            self.asset=await self.engine.synthesize('你好，我是数字生命。这是一条语音测试消息。')
            raw=Path(self.asset['path']).read_bytes();self.job=uuid.uuid4().hex
            result=await self.loop.bridge.request('POST','/speech/import',{'job_id':self.job,'sha256':self.asset['sha256'],
                'voice_id':self.asset['voice_id'],'wav_base64':base64.b64encode(raw).decode()})
            if result.get('state')!='ready' or result.get('sha256')!=self.asset['sha256']:raise SessionStopped('PHONE_SPEECH_IMPORT_FAILED')
            await self.loop.bridge.request('POST','/speech/play',{'job_id':self.job})
            for _ in range(44):
                status=await self.loop.bridge.request('POST','/speech/status',{})
                if status.get('state') in {'failed','played'}:break
                await asyncio.sleep(.25)
            write_json(self.store.directory/'BACKGROUND_PLAYBACK_PREFLIGHT.json',status)
            if status.get('state')!='played':raise SessionStopped('BACKGROUND_PHONE_PLAYBACK_NOT_VERIFIED')
            self.job=uuid.uuid4().hex
            result=await self.loop.bridge.request('POST','/speech/import',{'job_id':self.job,'sha256':self.asset['sha256'],
                'voice_id':self.asset['voice_id'],'wav_base64':base64.b64encode(raw).decode()})
            if result.get('state')!='ready':raise SessionStopped('PHONE_SPEECH_REIMPORT_FAILED')
            ui=AndroidSocialUI(self.loop,WX);fresh=await ui.observe()
            if not own_helper(fresh):raise SessionStopped('OWN_FILETRANSFER_HELPER_CHANGED')
            rows=[c for c in fresh.controls if re.sub(r'\s','',c.text)=='按住说话' and not c.covered and c.bounds[1]>fresh.height*.8]
            if len(rows)!=1:raise SessionStopped('NATIVE_HOLD_TO_TALK_UNAVAILABLE')
            target=add_control(fresh,rows[0].text,list(rows[0].bounds),1,'vision','self_chat_voice_record_button')
            command=await self.loop.phone.prepare({'type':'tap','control_id':target.control_id},target,fresh)
            if command[:3]!=('shell','input','tap'):raise SessionStopped('NATIVE_HOLD_POINT_UNAVAILABLE')
            x,y=command[-2:];milliseconds=round((self.asset['seconds']+1.7)*1000)
            if not 1500<milliseconds<=22000:raise SessionStopped('NATIVE_RECORD_DURATION_INVALID')
            write_json(intent,{'speech_ref':self.asset['speech_ref'],'target':'WeChat own file-transfer helper','state':'attempting',
                'source_text_sha256':self.asset['text_sha256'],'one_attempt_only':True,'external_contacts':0})
            async def record():
                released=False
                task=asyncio.create_task(self.loop.phone.dispatch(('shell','input','swipe',x,y,x,y,str(milliseconds))))
                try:
                    await asyncio.sleep(.8)
                    started=await self.loop.bridge.request('POST','/speech/play',{'job_id':self.job})
                    if not started.get('started'):raise Blocked('PHONE_TTS_PLAYBACK_NOT_STARTED')
                    await asyncio.sleep(.6)
                    audio=await self.loop.phone._text('shell','dumpsys','audio')
                    (self.store.directory/'android-audio-during-wechat-recording.txt').write_bytes(audio.encode('utf-8'))
                    await task
                    released=True
                    self.playback=await self.loop.bridge.request('POST','/speech/status',{})
                finally:
                    if not released:
                        # BACK cancels the own-chat recording before releasing
                        # a possible outstanding pointer. Never start it again.
                        if not task.done():task.cancel()
                        await asyncio.gather(task,return_exceptions=True)
                        if (await self.loop.phone.scene()).split('/')[0]==WX:
                            await self.loop.phone._adb('shell','input','keyevent','4')
                            await self.loop.phone._adb('shell','input','motionevent','UP',x,y)
                    await self.loop.bridge.request('POST','/speech/stop',{})
            after,_=await self.loop.send_operation(record,{'type':'own_filehelper_native_voice_record_once',
                'speech_ref':self.asset['speech_ref'],'outbound_intent':str(intent)},fresh)
            self.stage=2
            write_json(self.store.directory/'SELF_VOICE_POST_OBSERVATION.json',{'observation':asdict(after),
                'tts':self.asset,'playback':self.playback,'target_still_own_filehelper':own_helper(after),
                'native_audio_content_verified':False,'external_contact_messages':0})
            return {'intent':'rest','action':{'type':'rest','seconds':1},'note':'One self-chat native voice test; inspect resulting voice bubble before claiming audio delivery'}
        write_json(self.store.directory/'app-view.json',asdict(obs))
        raise SessionStopped('SELF_VOICE_RECORDING_OBSERVED_FOR_REVIEW')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--profile',default='find-x6');parser.add_argument('--session',required=True)
    args=parser.parse_args();config=Config.load(args.profile)
    loop=SessionLoop(config,args.session,minutes=4,brain_factory=lambda c,s:NativeVoiceProbe(c,s,loop))
    result=asyncio.run(loop.run());print(json.dumps(result))
    return 0 if result['reason']=='SELF_VOICE_RECORDING_OBSERVED_FOR_REVIEW' else 2


if __name__=='__main__':raise SystemExit(main())
