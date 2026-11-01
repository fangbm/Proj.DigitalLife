"""Bounded real WeChat voice notification window, at most one model reply."""
import argparse
import asyncio
import json
import time
import math
import re
from digitallife_v2.brain import LifeBrain
from digitallife_v2.config import Config
from digitallife_v2.session import SessionLoop,SessionStopped
from digitallife_v2.social import SocialPolicy
from digitallife_v2.social_ui import AndroidSocialUI
from digitallife_v2.store import write_json
from digitallife_v2.wechat_voice import DesktopWechatTransport,WechatVoiceAgent,WechatVoicePolicy
from digitallife_v2.safety import Blocked


def eligible_voice(item,opened,route_hint=None):
    return (item['source_app']=='com.tencent.mm' and item['posted_at']>=opened
            and any(s in item.get('preview','') for s in ('[语音','发来一条语音'))
            and (route_hint is None or item.get('preview','').startswith(route_hint+' ')))


class WindowSocialUI(AndroidSocialUI):
    """Keep an operator acceptance window on its requested conversation."""
    def __init__(self,loop,route_hint,open_existing=False):
        super().__init__(loop,'com.tencent.mm');self.route_hint=route_hint;self.open_existing=open_existing

    async def resolve(self,**kwargs):
        if self.open_existing and not kwargs.get('event') and not kwargs.get('binding_id'):
            await self.open_hint(self.route_hint)
        result=await super().resolve(**kwargs)
        if self.route_hint is not None:
            from digitallife_v2.wechat_voice import re_title
            if re_title(result[0].route_hint)!=self.route_hint:raise Blocked('WECHAT_WINDOW_OTHER_CONVERSATION')
        return result


class WechatVoiceWindow(SessionLoop):
    def __init__(self,config,directory,minutes,opened_after=None,confirmed_binding_id=None,route_hint=None,confirmed_route=False):
        if opened_after is not None and (not math.isfinite(opened_after) or not 0<=time.time()-opened_after<=3600):
            raise ValueError('WECHAT_RECOVERY_REQUIRES_A_CONFIRMED_RECENT_WINDOW')
        config.notification_packages=('com.tencent.mm',)
        super().__init__(config,directory,minutes=minutes,brain_factory=lambda c,s:LifeBrain(c,s))
        self.disable_qq_voice=True
        self.opened=None;self.handled=False;self.opened_after=opened_after
        if confirmed_binding_id is not None and not re.fullmatch('[a-f0-9]{64}',confirmed_binding_id):
            raise ValueError('CONFIRMED_EXISTING_WECHAT_BINDING_REQUIRED')
        self.confirmed_binding_id=confirmed_binding_id
        if route_hint is not None and (not isinstance(route_hint,str) or not route_hint.strip()
                or len(route_hint)>100 or any(ord(c)<32 for c in route_hint)):
            raise ValueError('WECHAT_WINDOW_CONVERSATION_NAME_REQUIRED')
        self.route_hint=route_hint
        if type(confirmed_route) is not bool or confirmed_route and (not route_hint or confirmed_binding_id):
            raise ValueError('CONFIRMED_VISIBLE_ROUTE_REQUIRES_CONVERSATION_NAME_ONLY')
        self.confirmed_route=confirmed_route
        self.notification_route_used=not (confirmed_binding_id or confirmed_route)
        self.source=('human_confirmed_existing_bound_voice' if confirmed_binding_id else
                     'human_confirmed_existing_visible_voice' if confirmed_route else 'real_wechat_voice_notification')

    async def step(self):
        if self.handled:raise SessionStopped('WECHAT_VOICE_SINGLE_INPUT_WINDOW_CLOSED')
        if not self.bridge:raise SessionStopped('WECHAT_NOTIFICATION_BRIDGE_UNAVAILABLE')
        if self.opened is None:
            settings=WechatVoicePolicy(self.config).read()
            try:
                await self.guarded(DesktopWechatTransport(self.config,self.store).call({'operation':'status',
                    'account':settings['account'],'title':'文件传输助手'}))
            except Blocked as error:raise SessionStopped('WECHAT_VOICE_PREFLIGHT_UNAVAILABLE:'+str(error)) from None
            self.opened=self.opened_after if self.opened_after is not None else time.time()
            await AndroidSocialUI(self,'com.tencent.mm').act({'type':'home'})
            write_json(self.directory/'WINDOW_READY.json',{'opened_at':self.opened,'deadline':self.store.state['deadline'],
                'source':self.source,
                'maximum_outgoing':1,'private_or_group':True,'requested_conversation':self.route_hint,
                'pipeline':'production_WechatVoiceAgent','incoming_source':'official_WeChat_platform_transcription'})
            if self.opened_after is not None:
                self.store.event('wechat_confirmed_window_recovery',{'opened_after':self.opened_after,
                    'source':'direct_user_confirmed_voice_sent','maximum_outgoing':1,'automatic_replay':False})
        item=None
        if self.notification_route_used:
            item=self.hub.next()
            if not item:
                self.inbound_wake.clear();await self.guarded(self.wait_for_event(10));return {'outcome':'waiting_for_wechat_voice'}
            if not eligible_voice(item,self.opened,self.route_hint):
                self.hub.resolve(item,{'event_id':item['event_id'],'decision':'read_only','note':'Outside this bounded voice window'})
                return {'outcome':'non_voice_notification_observed'}
            self.hub.resolve(item,{'event_id':item['event_id'],'decision':'reply_now','note':'Human-authorized single voice acceptance'})
        self.handled=True
        try:result=await WechatVoiceAgent(self,WindowSocialUI(self,self.route_hint,self.confirmed_route)).run('auto_reply',event=item,binding_id=self.confirmed_binding_id)
        except BaseException as error:
            detail=str(error) or type(error).__name__
            write_json(self.directory/'WECHAT_VOICE_FAILURE.json',{'reason':detail[:300],
                'automatic_retry':False,'maximum_attempted_inputs':1,'native_delivery_may_be_unknown':True})
            raise SessionStopped('WECHAT_VOICE_ACCEPTANCE_STOPPED:'+detail[:120]) from None
        full_match=False
        report={'result':result,'source':self.source,'notification_route_used':self.notification_route_used,
            'pipeline':'production_WechatVoiceAgent','maximum_outgoing':1,'incoming_raw_audio_acquired':False,
            'incoming_platform_transcript_used':result.get('outcome') not in {'no_unanswered_incoming','incoming_already_recorded'},
            'outgoing_audio_content_independently_verified':False}
        # Keep the observed send even when the independent transcription menu fails.
        write_json(self.directory/'WECHAT_VOICE_RESULT.json',report)
        if result.get('outcome')=='sent_observed' and result.get('reply_format')=='voice':
            row=self.store.db.execute('SELECT b.body,o.text FROM social_outbox o JOIN social_bindings b ON b.id=o.binding_id WHERE o.id=?',(result['outbound_id'],)).fetchone()
            settings=WechatVoicePolicy(self.config).read()
            from digitallife_v2.wechat_voice import re_title
            try:
                readback=await self.guarded(DesktopWechatTransport(self.config,self.store).call({'operation':'readback',
                    'account':settings['account'],'title':re_title(json.loads(row[0])['route_hint'])}))
            except (Blocked,OSError,ValueError,asyncio.TimeoutError) as error:
                detail=str(error) or type(error).__name__
                write_json(self.directory/'WECHAT_READBACK_FAILURE.json',{'reason':detail[:300],
                    'observed_send_preserved':True,'additional_messages_sent':0,'automatic_retry':False})
                raise SessionStopped('WECHAT_VOICE_READBACK_UNAVAILABLE:'+detail[:120]) from None
            normalize=lambda text:''.join(c for c in text.casefold() if c.isalnum())
            full_match=normalize(readback.get('outgoing',{}).get('text',''))==normalize(row[1])
            write_json(self.directory/'WECHAT_OUTGOING_READBACK.json',{**readback,'expected_text':row[1],
                'full_text_match':full_match,'human_listening_verified':False})
        report['outgoing_audio_content_independently_verified']=full_match
        write_json(self.directory/'WECHAT_VOICE_RESULT.json',report)
        passed=result.get('outcome')=='sent_observed' and result.get('accepted') is True and full_match
        raise SessionStopped('WECHAT_VOICE_ACCEPTANCE_PASSED' if passed else 'WECHAT_VOICE_ACCEPTANCE_PARTIAL:'+result['outcome'])


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--session',required=True)
    parser.add_argument('--profile',default='find-x6');parser.add_argument('--minutes',type=float,default=10)
    parser.add_argument('--opened-after',type=float)
    parser.add_argument('--binding-id')
    parser.add_argument('--route-hint')
    parser.add_argument('--confirmed-existing-route',action='store_true')
    args=parser.parse_args();result=asyncio.run(WechatVoiceWindow(Config.load(args.profile),args.session,args.minutes,args.opened_after,args.binding_id,args.route_hint,args.confirmed_existing_route).run())
    print(json.dumps(result,ensure_ascii=False));return 0 if result['reason']=='WECHAT_VOICE_ACCEPTANCE_PASSED' else 2


if __name__=='__main__':raise SystemExit(main())
