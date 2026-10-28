"""Real native notification route -> target UI; no model or outgoing message."""
import argparse,asyncio,json
from digitallife_v2.config import Config
from digitallife_v2.session import SessionLoop,SessionStopped
from digitallife_v2.social_ui import AndroidSocialUI
from digitallife_v2.bridge import PACKAGE
from digitallife_v2.safety import KnownNonDispatch
from digitallife_v2.store import write_json
class Probe:
 def __init__(self,c,s,loop,app):self.s=s;self.loop=loop;self.app=app
 async def decide(self,obs,apps,recent):
  loop=self.loop
  raw=await loop.bridge.request('GET','/events')
  rows=[e for e in raw.get('events',[]) if e['package']==self.app]
  if not rows:raise SessionStopped('ROUTE_PROBE_NO_RETAINED_NOTIFICATION')
  e=rows[-1];route=await loop.bridge.request('POST','/route_status',{'event_key':e['event_key']})
  if not route.get('available'):raise SessionStopped('ROUTE_PROBE_UNAVAILABLE')
  ui=AndroidSocialUI(loop,self.app);before=await ui.act({'type':'open_app','package_name':PACKAGE})
  async def operation():
   result=await loop.bridge.request('POST','/open_event',{'event_key':e['event_key']})
   if result.get('not_dispatched') is True:raise KnownNonDispatch('ROUTE_PROBE_NOT_DISPATCHED')
   if result.get('dispatched') is not True:raise RuntimeError('ROUTE_RESULT_UNCERTAIN')
  after,_=await loop.send_operation(operation,{'type':'social_open_event','scope':'route_only_no_reply'},before)
  if after.foreground.split('/')[0]!=self.app:raise SessionStopped('ROUTE_PROBE_TARGET_NOT_OBSERVED')
  write_json(self.s.directory/'ROUTE_READY.json',{'source':'REAL_RETAINED_NOTIFICATION_PENDING_INTENT_TARGET_UI','app':self.app,'foreground':after.foreground,'source_key':e['event_key'],'notification_age_seconds':e['age_seconds'],'scope':'navigation_only_stale_notifications_not_message_reply','UI_observation':after.observation_id,'outgoing_sends':0,'model_requests':0})
  await ui.act({'type':'home'});raise SessionStopped('NOTIFICATION_ROUTE_ACCEPTANCE_DONE')
def main():
 p=argparse.ArgumentParser();p.add_argument('--session',required=True);p.add_argument('--profile',default='find-x6');p.add_argument('--app',choices=['qq','wechat'],required=True);a=p.parse_args();cfg=Config.load(a.profile);app='com.tencent.mobileqq' if a.app=='qq' else 'com.tencent.mm';cfg.notification_packages=(app,)
 loop=SessionLoop(cfg,a.session,minutes=3,brain_factory=lambda c,s:Probe(c,s,loop,app));r=asyncio.run(loop.run());print(json.dumps(r));return 0 if r['reason']=='NOTIFICATION_ROUTE_ACCEPTANCE_DONE' else 2
if __name__=='__main__':raise SystemExit(main())
