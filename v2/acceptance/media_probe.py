"""Explicit bounded M5 acceptance using real production media components."""
import argparse,asyncio,json
from digitallife_v2.brain import LifeBrain
from digitallife_v2.bilibili import BilibiliUI
from digitallife_v2.config import Config
from digitallife_v2.session import SessionLoop,SessionStopped
from digitallife_v2.store import write_json
from digitallife_v2.media import MediaPolicy,VideoSources
from digitallife_v2.safety import Blocked


class Probe:
    def __init__(self,config,store,loop,kind,source_id=None,binding_id=None,resume=None,thread_id=None):
        self.real=LifeBrain(config,store);self.loop=loop;self.kind=kind;self.source_id=source_id;self.binding_id=binding_id;self.resume=resume
        self.thread_id=thread_id
    async def structured(self,*args):return await self.real.structured(*args)
    async def decide(self,observation,apps,recent):
        try:return await self._decide(observation,apps,recent)
        except Blocked as error:raise SessionStopped('M5_ACCEPTANCE_BLOCKED:'+str(error)) from error

    async def _decide(self,observation,apps,recent):
        if self.kind=='review':
            from dataclasses import fields
            from pathlib import Path
            import hashlib
            from digitallife_v2.media import MediaStore
            from digitallife_v2.perception import Observation,parse_xml
            from digitallife_v2.safety import Blocked
            source=VideoSources(self.loop.store).get(self.source_id)
            rows=self.loop.store.db.execute("SELECT id,text,session FROM social_outbox WHERE device=? AND kind='post_video_comment' AND status='delivery_unknown' AND sources LIKE ?",(self.loop.config.serial,'%'+self.source_id+'%')).fetchall()
            if len(rows)!=1:raise Blocked('MEDIA_SINGLE_UNKNOWN_COMMENT_REQUIRED')
            oid,text,original_session=rows[0];baseline=None;actor_observation=None
            for raw in (Path(original_session)/'events.jsonl').read_text(encoding='utf-8').splitlines():
                event=json.loads(raw)
                if event['kind']=='social_prepared' and event['outbound_id']==oid:break
                if event['kind']=='observed':baseline=event
                if event['kind']=='media_self_account_observed':actor_observation=event['observation_id']
            if not baseline:raise Blocked('MEDIA_ORIGINAL_COMMENT_BASELINE_MISSING')
            values={k:v for k,v in baseline.items() if k in {f.name for f in fields(Observation)}}
            old=Observation(**values)
            if hashlib.sha256(Path(old.xml_path).read_bytes()).hexdigest()!=old.xml_sha256:raise Blocked('MEDIA_BASELINE_CHANGED')
            _,old.controls,_,_=parse_xml(Path(old.xml_path).read_text(encoding='utf-8'),old.observation_id,full_access=True)
            import xml.etree.ElementTree as ET
            from digitallife_v2.media import BILIBILI,VideoBinding
            row=self.loop.store.db.execute('SELECT b.app,b.account,b.peer,b.kind,b.body FROM social_bindings b JOIN social_outbox o ON o.binding_id=b.id WHERE o.id=?',(oid,)).fetchone()
            body=json.loads(row[4]);binding=VideoBinding(*row[:4],body['existing_contact'],tuple(body['observations']),body['verified_at'],body['source_type'],body['route_hint'])
            actor_xml=Path(original_session)/'screens'/(str(actor_observation)+'.xml')
            names=[n.get('text') for n in ET.fromstring(actor_xml.read_bytes()).iter('node') if n.get('resource-id')==BILIBILI+':id/header_info_name']
            if len(names)!=1:raise Blocked('MEDIA_ORIGINAL_ACTOR_PROOF_MISSING')
            ui=BilibiliUI(self.loop);after=await ui.observe()
            if ui.controls(after,'comment_container'):after=await ui.select(after,resource='tab_title',text='简介')
            identity,after=await ui.ensure_identity(after)
            if identity['video_id']!=source['video_id']:raise Blocked('MEDIA_REVIEW_VIDEO_CHANGED')
            after=await ui.select(after,resource='tab_title',text='评论')
            old=await ui.read_comments(old);after=await ui.read_comments(after,expand=True)
            evidence=ui.comment_evidence(old,after,text,names[0])
            if not evidence:raise Blocked('MEDIA_UNKNOWN_COMMENT_NOT_CONFIRMED')
            evidence['original_before_observation_id']=old.observation_id
            result=MediaStore(self.loop.store).reconcile_comment(oid,binding,self.source_id,evidence)
            self.loop.observed(after)
            write_json(self.loop.directory/'M5_REVIEW_READY.json',{'source':'INDEPENDENT_READ_ONLY_REVIEW','result':result,'dispatch_replayed':False,'outgoing_sends':0})
            await ui.act({'type':'home'},after)
            raise SessionStopped('M5_COMMENT_REVIEW_DONE')
        if self.kind!='watch':
            from digitallife_v2.media_agent import MediaAgent
            agent=MediaAgent(self.loop)
            if self.kind=='native':
                from digitallife_v2.native_card import NativeCardAgent
                result=await NativeCardAgent(self.loop).run(self.source_id,self.binding_id,requested_retest=True)
            elif self.kind=='reply':result=await agent.reply(self.thread_id)
            elif self.kind=='comment':result=await agent.comment(self.source_id)
            elif self.kind=='share':result=await agent.share(self.source_id,self.binding_id)
            else:result=await agent.share_link(self.source_id,self.binding_id,fallback_reason='operator_explicit_link_acceptance',resume_outbound=self.resume)
            if result['outcome']!='sent_observed':raise SessionStopped('M5_NOT_SENT:'+result['outcome'])
            write_json(self.loop.directory/'M5_READY.json',{'source':'LIVE_MEDIA_SOURCE_REAL_MODEL_ONE_ATTEMPT_POST_OBSERVATION',
                'action_kind':self.kind,'result':result,'read_receipt_claimed':False})
            await BilibiliUI(self.loop).act({'type':'home'})
            raise SessionStopped('M5_REPLY_ACCEPTANCE_DONE' if self.kind=='reply' else 'M5_COMMENT_ACCEPTANCE_DONE' if self.kind=='comment' else 'M5_SHARE_ACCEPTANCE_DONE')
        result=await BilibiliUI(self.loop).watch(15)
        source=VideoSources(self.loop.store).get(result['source_id'])
        write_json(self.loop.directory/'M5_SOURCE_READY.json',{'source':'REAL_CURRENT_VIDEO_NATIVE_ID_SAMPLED_FRAMES_MODEL_SUMMARY',
            'source_id':source['source_id'],'video_id':source['video_id'],'scope':'sampled_segment_only','outgoing_sends':0})
        await BilibiliUI(self.loop).act({'type':'home'})
        raise SessionStopped('M5_VIDEO_SOURCE_ACCEPTANCE_DONE')


def main():
    p=argparse.ArgumentParser();p.add_argument('--session',required=True);p.add_argument('--profile',default='find-x6');p.add_argument('--kind',choices=['watch','comment','share','link','review','reply','native'],default='watch');p.add_argument('--source-id');p.add_argument('--binding-id');p.add_argument('--resume-outbound');p.add_argument('--thread-id');args=p.parse_args()
    import re
    if args.kind!='watch' and (not args.source_id or not re.fullmatch(r'video:[a-f0-9]{32}',args.source_id)):raise ValueError('VERIFIED_VIDEO_SOURCE_REQUIRED')
    if args.kind in {'share','native','link'} and (not args.binding_id or not re.fullmatch('[a-f0-9]{64}',args.binding_id)):raise ValueError('VERIFIED_GROUP_BINDING_REQUIRED')
    if args.resume_outbound and (args.kind!='link' or not re.fullmatch('[a-f0-9]{32}',args.resume_outbound)):raise ValueError('KNOWN_UNSENT_SHARE_ID_REQUIRED')
    if args.kind=='reply':
        from digitallife_v2.bili_threads import ThreadSources
        if not args.thread_id or not re.fullmatch('thread:[a-f0-9]{32}',args.thread_id):raise ValueError('OBSERVED_PUBLIC_THREAD_REQUIRED')
    c=Config.load(args.profile)
    if MediaPolicy(c).read().get('enabled') is not True:raise ValueError('M5_SCOPE_REQUIRED')
    loop=SessionLoop(c,args.session,minutes=10,brain_factory=lambda cfg,s:Probe(cfg,s,loop,args.kind,args.source_id,args.binding_id,args.resume_outbound,args.thread_id))
    if args.kind=='share':
        loop.media_acceptance_context='用户要求验证官方群分享的完整流程。本轮只向当前已核对的授权测试群分享一次这条尚未分享过的公开视频；不要求群中恰好正在讨论视频主题。保留退订、账号、来源、去重和普通每日额度；不额外发文字或重复旧视频。'
    if args.kind=='link':
        loop.social_acceptance_context=('用户要求M5真实分享验收，已允许向当前这个单独授权的测试群分享来源视频。本次是脚本安排的单次测试，不需要新来信或新@触发。'
            '请只基于提供的真实公开视频采样写一句简短分享理由，表明数字生命测试身份和采样局限，末尾保留提供的公开链接。'
            '不是声称自主看完全片；身份、权限、来源不符或群内要求停止时仍不发。')
    result=asyncio.run(loop.run());print(json.dumps(result));return 0 if result['reason'] in {'M5_VIDEO_SOURCE_ACCEPTANCE_DONE','M5_COMMENT_ACCEPTANCE_DONE','M5_SHARE_ACCEPTANCE_DONE','M5_COMMENT_REVIEW_DONE','M5_REPLY_ACCEPTANCE_DONE'} else 2


if __name__=='__main__':raise SystemExit(main())
