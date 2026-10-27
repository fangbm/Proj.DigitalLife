"""One explicitly requested group @ acceptance; no forced model send."""
import argparse
import asyncio
import json
import re
from digitallife_v2.brain import LifeBrain
from digitallife_v2.config import Config
from digitallife_v2.session import SessionLoop,SessionStopped
from digitallife_v2.social_agent import SocialAgent
from digitallife_v2.social_ui import AndroidSocialUI
from digitallife_v2.store import write_json
from digitallife_v2.safety import Blocked

class Probe:
 def __init__(self,config,store,loop,app,hint,receipt=None):
  self.real=LifeBrain(config,store);self.store=store;self.loop=loop;self.app=app;self.hint=hint
  if receipt:
   from .group_receipt import load
   loop.group_receipt_context=load(loop,receipt)
 async def structured(self,*args):return await self.real.structured(*args)
 async def decide(self,observation,apps,recent):
  ui=AndroidSocialUI(self.loop,self.app)
  try:
   current=False
   if self.hint:
    try:current=re.sub(r'[（(]\d+[）)]$','',ui.chat_hint(observation))==self.hint
    except Blocked:pass
   if self.hint and not current:await ui.open_hint(self.hint)
   result=await SocialAgent(self.loop,ui).run('group_post')
  except Blocked as error:raise SessionStopped('GROUP_ACCEPTANCE_BLOCKED:'+str(error)) from error
  if result['outcome']!='sent_observed':raise SessionStopped('GROUP_MODEL_OR_SEND_NOT_CONFIRMED:'+result['outcome'])
  row=self.store.db.execute('SELECT status FROM social_outbox WHERE id=?',(result['outbound_id'],)).fetchone()
  if not row or row[0]!='sent_observed':raise SessionStopped('GROUP_SEND_PROOF_MISSING')
  write_json(self.store.directory/'GROUP_READY.json',{'source':'LIVE_SELF_MENTION_GROUP_ID_MEMBERSHIP_REAL_MODEL_ONE_SEND','result':result,'app':self.app,'read_receipt_claimed':False})
  await ui.act({'type':'home'})
  raise SessionStopped('M4_GROUP_ACCEPTANCE_DONE')

def main():
 p=argparse.ArgumentParser();p.add_argument('--session',required=True);p.add_argument('--profile',default='find-x6');p.add_argument('--app',choices=['qq','wechat'],required=True);p.add_argument('--route-hint');p.add_argument('--requested-now',action='store_true');p.add_argument('--receipt');args=p.parse_args()
 cfg=Config.load(args.profile);app='com.tencent.mobileqq' if args.app=='qq' else 'com.tencent.mm'
 loop=SessionLoop(cfg,args.session,minutes=10,brain_factory=lambda c,s:Probe(c,s,loop,app,args.route_hint,args.receipt))
 loop.requested_group_test_now=args.requested_now;loop.group_acceptance_mention_required=True
 loop.social_acceptance_marker='M4-GROUP-QQ-1027' if args.app=='qq' else 'M4-GROUP-WX-1027'
 loop.social_acceptance_context='用户刚要求对已加入的测试群进行即时真实验收。只对实际群内@自己的新消息决定是否参与，合适时简短介绍数字生命身份，像正常群聊那样回应，不罗列技术校验或送达说明。本轮最多一次，可以不发。'
 r=asyncio.run(loop.run());print(json.dumps(r));return 0 if r['reason']=='M4_GROUP_ACCEPTANCE_DONE' else 2

if __name__=='__main__':raise SystemExit(main())
