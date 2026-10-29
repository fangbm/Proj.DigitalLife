"""Bounded real Bilibili notification acceptance through SessionLoop.step."""
import argparse,asyncio,json,re,time
from digitallife_v2.brain import LifeBrain
from digitallife_v2.config import Config
from digitallife_v2.media import BILIBILI,MediaPolicy
from digitallife_v2.session import SessionLoop,SessionStopped
from digitallife_v2.social_ui import AndroidSocialUI
from digitallife_v2.store import write_json
from digitallife_v2.safety import Blocked


class MediaReplyWindow(SessionLoop):
    def __init__(self,config,directory,marker,posted_after=None):
        self.marker=marker;self.opened=None;self.posted_after=posted_after
        super().__init__(config,directory,minutes=10,brain_factory=lambda c,s:LifeBrain(c,s))

    def candidate(self):
        return self.store.db.execute("SELECT id,preview,posted FROM inbound_events WHERE device=? AND source_app=? AND posted>=? AND preview LIKE ? AND status='pending' ORDER BY received DESC LIMIT 1",
            (self.config.serial,BILIBILI,self.posted_after if self.posted_after is not None else self.opened,'%'+self.marker+'%')).fetchone()

    async def step(self):
        if not self.bridge:raise SessionStopped('M5_NOTIFICATION_BRIDGE_UNAVAILABLE')
        if self.opened is None:
            await AndroidSocialUI(self,BILIBILI).act({'type':'home'})
            self.opened=time.time()
            write_json(self.directory/'WINDOW_READY.json',{'opened_at':self.opened,'deadline':self.store.state['deadline'],
                'marker':self.marker,'app':BILIBILI,'maximum_outgoing':1,'pipeline':'production_SessionLoop_step','posted_after':self.posted_after})
        await self.poll_notifications();row=self.candidate()
        if not row:
            self.inbound_wake.clear();await self.guarded(self.wait_for_event(10));return {'outcome':'waiting_for_real_comment_notification'}
        if self.deadline-time.monotonic()<330:raise SessionStopped('M5_MESSAGE_RETAINED_RESTART_WINDOW')
        offered={'event_id':row[0],'source_app':BILIBILI,'preview':row[1],'posted_at':row[2],
            'context_complete':False,'account_ref':None,'conversation_ref':None,'trust':'data_not_instructions'}
        # Only select the real fixture event. Production classification, public
        # source correlation, identity checks, drafting and publication remain.
        original_next=self.hub.next;self.hub.next=lambda:offered
        try:
            result=await super().step()
        except Blocked as error:raise SessionStopped('M5_NOTIFICATION_BLOCKED:'+str(error)) from error
        finally:self.hub.next=original_next
        if result.get('outcome')!='sent_observed':raise SessionStopped('M5_NOTIFICATION_NOT_SENT:'+result.get('outcome','unknown'))
        state=self.store.db.execute('SELECT kind,status FROM social_outbox WHERE id=?',(result['outbound_id'],)).fetchone()
        if state!=('reply_video_comment','sent_observed'):raise SessionStopped('M5_NOTIFICATION_SEND_EVIDENCE_MISSING')
        write_json(self.directory/'M5_NOTIFICATION_READY.json',{'source':'LIVE_NOTIFICATION_PRODUCTION_STEP_REAL_MODEL_ONE_REPLY',
            'event_id':row[0],'result':result,'operator_thread_id_supplied':False,'pipeline':'production_SessionLoop_step','read_receipt_claimed':False})
        await AndroidSocialUI(self,BILIBILI).act({'type':'home'})
        raise SessionStopped('M5_NOTIFICATION_REPLY_ACCEPTANCE_DONE')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--session',required=True);parser.add_argument('--profile',default='find-x6');parser.add_argument('--marker',required=True);parser.add_argument('--posted-after',type=float);args=parser.parse_args()
    if not re.fullmatch('[A-Za-z0-9-]{6,60}',args.marker):raise ValueError('ASCII_TEST_MARKER_REQUIRED')
    if args.posted_after is not None and not 0<=time.time()-args.posted_after<=3600:raise ValueError('RECENT_ACTUAL_NOTIFICATION_CUTOFF_REQUIRED')
    config=Config.load(args.profile);policy=MediaPolicy(config).read()
    if not policy.get('enabled') or policy.get('comments',{}).get('reply_video_comment') is not True:raise ValueError('M5_REPLY_SCOPE_REQUIRED')
    config.notification_packages=(BILIBILI,)
    result=asyncio.run(MediaReplyWindow(config,args.session,args.marker,args.posted_after).run());print(json.dumps(result))
    return 0 if result['reason']=='M5_NOTIFICATION_REPLY_ACCEPTANCE_DONE' else 2


if __name__=='__main__':raise SystemExit(main())
