"""Scripted placement, real UI identity, real model draft, actual one-attempt send."""
import argparse
import asyncio
import json
import re
from digitallife_v2.brain import LifeBrain
from digitallife_v2.config import Config
from digitallife_v2.session import SessionLoop, SessionStopped
from digitallife_v2.safety import Blocked
from digitallife_v2.social_agent import SocialAgent
from digitallife_v2.social_ui import AndroidSocialUI
from digitallife_v2.store import write_json


class Probe:
    def __init__(self,config,store,loop,app,backs,kind,resume=None,binding=None):
        self.real=LifeBrain(config,store);self.store=store;self.loop=loop
        self.app=app;self.backs=backs;self.stage=0;self.kind=kind
        self.resume=resume
        self.binding=binding

    async def structured(self,*args):return await self.real.structured(*args)

    async def decide(self,observation,apps,recent):
        if self.stage<self.backs:
            self.stage+=1;action={'type':'back'}
        elif self.stage==self.backs:
            agent=SocialAgent(self.loop,AndroidSocialUI(self.loop,self.app))
            try:
                result=await agent.resume_known_unsent(self.resume) if self.resume else await agent.run(self.kind,binding_id=self.binding)
            except Blocked as error:
                raise SessionStopped('M4_ACCEPTANCE_BLOCKED:'+str(error)) from error
            if result['outcome']!='sent_observed':raise SessionStopped('M4_MODEL_OR_SEND_NOT_CONFIRMED:'+result['outcome'])
            write_json(self.store.directory/'M4_READY.json',{'source':'LIVE_UI_IDENTITY_REAL_MODEL_ONE_SEND_POST_OBSERVATION',
                'result':result,'action_kind':self.kind,'app':self.app,'read_receipt_claimed':False})
            self.stage+=1
            ui=AndroidSocialUI(self.loop,self.app)
            await ui.act({'type':'home'})
            action={'type':'rest','seconds':1}
        else:
            if not (self.store.directory/'M4_READY.json').exists():raise SessionStopped('M4_SEND_EVIDENCE_MISSING')
            proof=json.loads((self.store.directory/'M4_READY.json').read_text(encoding='utf-8'))
            row=self.store.db.execute('SELECT status FROM social_outbox WHERE id=?',(proof['result']['outbound_id'],)).fetchone()
            if not row or row[0]!='sent_observed':raise SessionStopped('M4_SEND_EVIDENCE_MISSING')
            raise SessionStopped('M4_SOCIAL_ACCEPTANCE_DONE')
        return {'intent':'rest' if action['type']=='rest' else 'explore','action':action,'note':'Explicit M4 real social acceptance within human all-contact scope'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--session',required=True);p.add_argument('--profile',default='find-x6')
    p.add_argument('--app',choices=['qq','wechat'],default='qq');p.add_argument('--initial-backs',type=int,default=0)
    p.add_argument('--kind',choices=['auto_reply','proactive_dm'],default='auto_reply')
    p.add_argument('--resume-outbound');p.add_argument('--binding-id');args=p.parse_args()
    if args.resume_outbound and not re.fullmatch('[a-f0-9]{32}',args.resume_outbound):raise ValueError('OUTBOUND_ID_REQUIRED')
    if args.binding_id and not re.fullmatch('[a-f0-9]{64}',args.binding_id):raise ValueError('BINDING_ID_REQUIRED')
    if not 0<=args.initial_backs<=4:raise ValueError('BOUNDED_INITIAL_NAVIGATION_REQUIRED')
    config=Config.load(args.profile)
    app='com.tencent.mobileqq' if args.app=='qq' else 'com.tencent.mm'
    loop=SessionLoop(config,args.session,minutes=10,brain_factory=lambda c,s:Probe(c,s,loop,app,args.initial_backs,args.kind,args.resume_outbound,args.binding_id))
    loop.social_acceptance_context=('用户已授权所有已有联系人主动联系，本轮是单次主动联系验收，不是新来信触发。仅在实际提供的公开经历能产生具体话题时，向当前联系人分享一句简短观察并自然邀请交流，表明数字生命身份；标注脚本实测经历，不冒充自主观看全片，不转发视频或链接。无合适话题则defer。' if args.kind=='proactive_dm' else
        '用户明确要求M4真实外发验收，并已授权所有已有联系人。当前是此前发来测试短句的实际测试会话；请基于真实上下文生成一条简短中文确认回复，表明数字生命身份并确认收到测试消息。若对方已要求停止或实际身份/权限不成立则不发。此为脚本安排的单次验收，不是普通自主聊天提示。')
    result=asyncio.run(loop.run());print(json.dumps(result));return 0 if result['reason']=='M4_SOCIAL_ACCEPTANCE_DONE' else 2


if __name__=='__main__':raise SystemExit(main())
