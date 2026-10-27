"""System-consented playback capture, local ASR, real model summary; no microphone."""
import argparse
import asyncio
import json
from pathlib import Path
from digitallife_v2.brain import LifeBrain
from digitallife_v2.bridge import PACKAGE
from digitallife_v2.config import Config
from digitallife_v2.session import SessionLoop, SessionStopped
from digitallife_v2.store import write_json
from .reading_probe import provision


class AudioProbe:
    def __init__(self, config, store, resume_prompt=False):
        self.store=store;self.real=LifeBrain(config,store);self.stage=2 if resume_prompt else 0;self.consent_clicked=False

    async def structured(self,*args):
        return await self.real.structured(*args)

    def button(self, observation, text):
        targets=[c for c in observation.controls if c.text==text and c.clickable and c.enabled]
        if len(targets)!=1:raise SessionStopped('AUDIO_BUTTON_UNAVAILABLE')
        return {'type':'tap','control_id':targets[0].control_id}

    async def decide(self, observation, apps, recent):
        if self.stage==0:
            self.stage=1;action={'type':'open_app','package_name':PACKAGE}
        elif self.stage==1:
            self.stage=2;action=self.button(observation,'授权采集应用播放音频')
        elif self.stage==2:
            bridge=getattr(self.store,'notification_client',None)
            if not bridge:raise SessionStopped('AUDIO_BRIDGE_UNAVAILABLE')
            state=await bridge.request('GET','/status')
            if state.get('audio_authorized'):
                self.stage=3;action=self.button(observation,'播放语音验收材料')
            elif observation.foreground.split('/')[0]=='com.android.systemui' and not self.consent_clicked:
                if not any('DigitalLife' in (c.text+c.description) for c in observation.controls):raise SessionStopped('PROJECTION_APP_LABEL_UNVERIFIED')
                targets=[c for c in observation.controls if c.text in {'立即开始','开始','允许','Start now','Start recording'} and c.clickable and c.enabled]
                if len(targets)!=1:raise SessionStopped('PROJECTION_PROMPT_REVIEW')
                self.consent_clicked=True;action={'type':'tap','control_id':targets[0].control_id}
            else:
                raise SessionStopped('PROJECTION_AUTHORIZATION_NOT_OBSERVED')
        elif self.stage==3:
            if not observation.foreground.endswith('SpeechActivity'):raise SessionStopped('SPEECH_FOREGROUND_UNVERIFIED')
            self.stage=4;action={'type':'watch_video','seconds':30}
        elif self.stage==4:
            row=self.store.db.execute("SELECT id,body,summary FROM content_segments WHERE session=? AND status='summarized' ORDER BY started DESC LIMIT 1",(self.store.session,)).fetchone()
            if not row:raise SessionStopped('AUDIO_SUMMARY_MISSING')
            body=json.loads(row[1]);audio=body.get('audio',{})
            if not audio.get('available') or not audio.get('capture_available'):raise SessionStopped('AUDIO_CAPTURE_OR_ASR_UNAVAILABLE')
            text=' '.join(x['text'] for x in audio['segments']).lower()
            if 'country' not in text:raise SessionStopped('AUDIO_TRANSCRIPT_REVIEW')
            write_json(self.store.directory/'AUDIO_READY.json',{'source':'LIVE_DEVICE_PLAYBACK_CAPTURE_LOCAL_ASR_REAL_SUMMARY',
                'segment_id':row[0],'audio':audio,'summary':json.loads(row[2]),'microphone_used':False})
            self.stage=5;action={'type':'home'}
        else:
            raise SessionStopped('AUDIO_ACCEPTANCE_DONE')
        return {'intent':'explore','action':action,'note':'Explicit playback-audio acceptance using Android system consent'}


async def run(config,directory,speech,resume_prompt=False):
    if not config.audio_enabled or not config.asr_python or not config.asr_model:raise ValueError('LOCAL_AUDIO_CONFIGURATION_REQUIRED')
    await provision(config,speech,'speech.wav')
    return await SessionLoop(config,directory,minutes=7,brain_factory=lambda c,s:AudioProbe(c,s,resume_prompt)).run()


def main():
    p=argparse.ArgumentParser();p.add_argument('--session',required=True);p.add_argument('--profile',default='find-x6');p.add_argument('--speech',default='data/m3-audio/speech.wav');p.add_argument('--resume-projection-prompt',action='store_true');args=p.parse_args()
    result=asyncio.run(run(Config.load(args.profile),args.session,Path(args.speech).resolve(),args.resume_projection_prompt))
    print(json.dumps(result));return 0 if result['reason']=='AUDIO_ACCEPTANCE_DONE' else 2

if __name__=='__main__':raise SystemExit(main())
