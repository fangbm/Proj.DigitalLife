"""Ten-minute human-message window, one real notification route and one reply."""
import argparse
import asyncio
import json
import time
import re
from datetime import datetime
from digitallife_v2.social import CHINA
from digitallife_v2.brain import LifeBrain
from digitallife_v2.config import Config
from digitallife_v2.session import SessionLoop, SessionStopped
from digitallife_v2.social_agent import SocialAgent
from digitallife_v2.social_ui import AndroidSocialUI
from digitallife_v2.store import write_json
from digitallife_v2.safety import Blocked


class ReplyWindow(SessionLoop):
    def __init__(self,config,directory,app,binding_id=None,marker=None):
        self.app=app;self.opened=None
        self.binding_id=binding_id
        self.marker=marker or ('M4-QQ-1027' if app=='com.tencent.mobileqq' else 'M4-WX-1027')
        super().__init__(config,directory,minutes=10,brain_factory=lambda c,s:LifeBrain(c,s))
        self.social_acceptance_context='用户已授权所有已有联系人自动回复。本轮为真人新消息验收，只针对当前实际通知与核对后的会话自然回应。可以不回复；不是要求照抄预设模板。'
        self.social_acceptance_marker=self.marker

    async def step(self):
        if not self.bridge:raise SessionStopped('M4_NOTIFICATION_BRIDGE_UNAVAILABLE')
        if self.opened is None:
            await AndroidSocialUI(self,self.app).act({'type':'home'})
            self.opened=time.time()
            write_json(self.directory/'WINDOW_READY.json',{'opened_at':self.opened,
                'deadline':self.store.state['deadline'],'marker':self.marker,'app':self.app,
                'real_reply_permitted':True,'maximum_outgoing':1})
        await self.poll_notifications()
        candidates=self.store.db.execute("SELECT id,preview,posted,source_key FROM inbound_events WHERE device=? AND source_app=? AND posted>=? AND (preview LIKE ? OR preview LIKE '%已隐藏敏感通知内容') AND status='pending' ORDER BY received DESC LIMIT 20",
            (self.config.serial,self.app,time.time()-3600,'%'+self.marker+'%')).fetchall()
        row=None
        for candidate in candidates:
            route=await self.guarded(self.bridge.request('POST','/route_status',{'event_key':candidate[3]}))
            if route.get('available') is True:row=candidate;break
        if not row:
            self.inbound_wake.clear();await self.guarded(self.wait_for_event(10))
            return {'outcome':'waiting_for_human_test_message'}
        if self.deadline-time.monotonic()<450:
            # Keep the real OS notification untouched for a fresh bounded
            # window, rather than starting a multi-minute UI proof too late.
            raise SessionStopped('M4_MESSAGE_RETAINED_RESTART_WINDOW_BEFORE_ROUTE')
        offered={'event_id':row[0],'source_app':self.app,'preview':row[1],'posted_at':row[2],
            'context_complete':False,'account_ref':None,'conversation_ref':None,'trust':'data_not_instructions'}
        if self.binding_id:self.social_acceptance_post_minute=datetime.fromtimestamp(row[2],CHINA).strftime('%H:%M')
        try:
            if self.binding_id:
                result=await SocialAgent(self,AndroidSocialUI(self,self.app)).run('auto_reply',binding_id=self.binding_id)
            else:
                # Filter only the test fixture's real event, without changing
                # its source, timestamps or receipt. All decisions and routing
                # then run through the ordinary production step.
                original_next=self.hub.next
                self.hub.next=lambda:offered
                try:result=await super().step()
                finally:self.hub.next=original_next
        except Blocked as error:
            self.hub.defer_handling(offered,str(error))
            raise SessionStopped('M4_INBOUND_REPLY_BLOCKED:'+str(error)) from error
        response={'event_id':row[0],'decision':'reply_now' if result['outcome']=='sent_observed' else 'reply_later','note':'Actual model decision after fresh UI identity and context verification'}
        if self.binding_id:self.hub.resolve(offered,response)
        routing='operator_selected_previously_verified_test_contact' if self.binding_id else 'notification_owned_pending_intent'
        self.store.event('inbound_social_result',{'event_id':row[0],'routing':routing,**result})
        if result['outcome']!='sent_observed':raise SessionStopped('M4_INBOUND_NOT_SENT:'+result['outcome'])
        state=self.store.db.execute('SELECT status FROM social_outbox WHERE id=?',(result['outbound_id'],)).fetchone()
        if not state or state[0]!='sent_observed':raise SessionStopped('M4_SEND_EVIDENCE_MISSING')
        write_json(self.directory/'M4_READY.json',{'source':'LIVE_HUMAN_NOTIFICATION_THREAD_REAL_MODEL_ONE_SEND',
            'result':result,'event_id':row[0],'app':self.app,'routing':routing,
            'pipeline':'operator_binding_legacy' if self.binding_id else 'production_SessionLoop_step','read_receipt_claimed':False})
        await AndroidSocialUI(self,self.app).act({'type':'home'})
        raise SessionStopped('M4_INBOUND_REPLY_ACCEPTANCE_DONE')


def main():
    p=argparse.ArgumentParser();p.add_argument('--session',required=True);p.add_argument('--profile',default='find-x6')
    p.add_argument('--app',choices=['qq','wechat'],default='qq');p.add_argument('--binding-id');p.add_argument('--marker');args=p.parse_args()
    if args.binding_id and not re.fullmatch('[a-f0-9]{64}',args.binding_id):raise ValueError('VERIFIED_BINDING_ID_REQUIRED')
    if args.marker and not re.fullmatch('[A-Za-z0-9-]{6,60}',args.marker):raise ValueError('ASCII_TEST_MARKER_REQUIRED')
    app='com.tencent.mobileqq' if args.app=='qq' else 'com.tencent.mm'
    config=Config.load(args.profile);config.notification_packages=(app,)
    result=asyncio.run(ReplyWindow(config,args.session,app,args.binding_id,args.marker).run());print(json.dumps(result))
    return 0 if result['reason']=='M4_INBOUND_REPLY_ACCEPTANCE_DONE' else 2


if __name__=='__main__':raise SystemExit(main())
