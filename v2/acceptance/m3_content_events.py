"""Script-orchestrated real video capture + real model + real local notification."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from digitallife_v2.brain import LifeBrain
from digitallife_v2.bridge import PACKAGE
from digitallife_v2.config import Config
from digitallife_v2.phone import PhoneController
from digitallife_v2.safety import DeviceLock
from digitallife_v2.session import SessionLoop,SessionStopped
from digitallife_v2.store import write_json


async def media(config,path):
    data=path.read_bytes()
    if len(data)>10_000_000 or b'ftyp' not in data[:32]:raise ValueError('BOUNDED_MP4_REQUIRED')
    with DeviceLock(config.serial):
        phone=PhoneController(config,config.data/'unused-media-setup');await phone.identity()
        remote='/data/local/tmp/digitallife-m3-video.mp4'
        await phone._adb('push',str(path),remote)
        await phone._adb('shell','run-as',PACKAGE,'cp',remote,'files/acceptance.mp4')
        digest=(await phone._text('shell','run-as',PACKAGE,'sha256sum','files/acceptance.mp4')).split()[0]
        if digest!=hashlib.sha256(data).hexdigest():raise ValueError('MEDIA_HASH_MISMATCH')
        await phone._adb('shell','rm',remote)
        return digest


class Probe:
    def __init__(self,config,store,events_only=False):
        self.store=store;self.stage=3 if events_only else 0;self.real=LifeBrain(config,store);self.events_only=events_only

    async def structured(self,*args):
        return await self.real.structured(*args)

    async def decide(self,observation,apps,recent):
        action=None
        if self.stage==0:
            self.stage=1;action={'type':'open_app','package_name':PACKAGE}
        elif self.stage==1:
            targets=[c for c in observation.controls if c.text=='播放公开视频验收片段' and c.clickable]
            if len(targets)!=1:raise SessionStopped('M3_VIDEO_BUTTON_UNAVAILABLE')
            self.stage=2;action={'type':'tap','control_id':targets[0].control_id}
        elif self.stage==2:
            if not observation.foreground.endswith('/.VideoActivity') and not observation.foreground.endswith('/io.digitallife.companion.VideoActivity'):
                raise SessionStopped('M3_VIDEO_FOREGROUND_UNVERIFIED')
            self.stage=3;action={'type':'watch_video','seconds':30}
        elif self.stage==3:
            if not self.events_only and not self.store.db.execute("SELECT 1 FROM content_segments WHERE session=? AND status='summarized'",(self.store.session,)).fetchone():raise SessionStopped('M3_CONTENT_SUMMARY_MISSING')
            bridge=getattr(self.store,'notification_client',None)
            if not bridge:raise SessionStopped('M3_NOTIFICATION_BRIDGE_MISSING')
            # Close only this app's known synthetic test notifications from earlier
            # acceptance attempts; preserve their original rows and failure logs.
            old=self.store.db.execute("SELECT id FROM inbound_events WHERE device=? AND source_app=? AND status='pending' AND preview LIKE 'DigitalLife M3 通知验收 %'",(self.store.serial,PACKAGE)).fetchall()
            for row in old:
                self.store.db.execute("UPDATE inbound_events SET status='classified',decision='ignore',note='Old local acceptance notification closed by test operator' WHERE id=?",(row[0],))
                self.store.event('acceptance_old_test_event_ignored',{'event_id':row[0]})
            await bridge.request('POST','/test_notification',{})
            self.stage=4;action={'type':'rest','seconds':10}
        elif self.stage==4:
            if not self.store.inbound_context:
                action={'type':'rest','seconds':3}
            else:
                result=await self.real.decide(observation,apps,recent)
                response=result.get('event_response')
                if not response:raise SessionStopped('M3_EVENT_CLASSIFICATION_MISSING')
                self.store.event('acceptance_real_model_event_proposal',result)
                self.stage=5
                return {'intent':'rest','action':{'type':'rest','seconds':1},'note':'Read-only event classification acceptance','event_response':response}
        else:
            segments=self.store.db.execute('SELECT id,body,summary FROM content_segments WHERE session=? AND status=?',(self.store.session,'summarized')).fetchall()
            events=self.store.db.execute("SELECT payload FROM events WHERE session=? AND kind='inbound_decision'",(self.store.session,)).fetchall()
            if (not self.events_only and not segments) or not events:raise SessionStopped('M3_EVIDENCE_INCOMPLETE')
            write_json(self.store.directory/('M3_EVENT_READY.json' if self.events_only else 'M3_READY.json'),{'source':'LIVE_DEVICE_SCRIPTED_ORCHESTRATION_REAL_MODEL',
                'segments':[{'segment_id':r[0],'body':json.loads(r[1]),'summary':json.loads(r[2])} for r in segments],
                'event_decisions':[json.loads(r[0]) for r in events], 'social_outbound_dispatched':False})
            raise SessionStopped('M3_EVENT_ACCEPTANCE_DONE' if self.events_only else 'M3_SLICE_ACCEPTANCE_DONE')
        return {'intent':'rest' if action['type']=='rest' else 'explore','action':action,'note':'Explicit M3 live acceptance step'}


async def run(config,directory,video,events_only=False):
    digest=await media(config,video) if not events_only else None
    result=await SessionLoop(config,directory,minutes=5,brain_factory=lambda c,s:Probe(c,s,events_only)).run()
    if digest:write_json(Path(directory)/'MEDIA_SOURCE.json',{'local_sha256':digest,'local_path':str(video),'source_metadata':json.loads(video.with_name('source.json').read_text(encoding='utf-8'))})
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument('--session',required=True);p.add_argument('--profile',default='find-x6');p.add_argument('--video',default='data/m3-public-video/acceptance.mp4');p.add_argument('--events-only',action='store_true');args=p.parse_args()
    result=asyncio.run(run(Config.load(args.profile),args.session,Path(args.video).resolve(),args.events_only))
    print(json.dumps(result));return 0 if result['reason'] in {'M3_SLICE_ACCEPTANCE_DONE','M3_EVENT_ACCEPTANCE_DONE'} else 2
if __name__=='__main__':raise SystemExit(main())
