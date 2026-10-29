"""Official-app card intents share the durable outbox and ordinary quotas."""
import json,time
from .media import MediaPolicy,MediaStore,VideoSources
from .social import SocialStore,digest
from .safety import Blocked

CARD_SOURCE='native:official-share-card'


class NativeCardPolicy(MediaPolicy):
    def __init__(self,config,*,retest_binding=None,deadline=0,acceptance_source=None,acceptance_limit=None):
        super().__init__(config);self.retest_binding=retest_binding;self.retest_deadline=deadline
        self.acceptance_source=acceptance_source;self.acceptance_limit=acceptance_limit

    def check(self,binding,kind,now=None):
        now=time.time() if now is None else now;limits=super().check(binding,kind,now)
        bid=digest(self.config.serial+'\0'+'\0'.join(binding.identity))
        if self.retest_binding==bid and 0<self.retest_deadline-now<=600:
            # One explicit official-card acceptance after the already accepted
            # link test. Ordinary autonomy never creates this temporary scope.
            limits['video_shares_group_day']=self.acceptance_limit if self.acceptance_source else 2
        return limits


class NativeCardStore(MediaStore):
    def reconcile_card(self,outbound_id,binding,source_id,evidence):
        row=self.db.execute('SELECT binding_id,kind,status,attempted,sources,evidence FROM social_outbox WHERE id=? AND device=?',(outbound_id,self.store.serial)).fetchone()
        source=VideoSources(self.store).get(source_id);bid=digest(self.store.serial+'\0'+'\0'.join(binding.identity))
        if (not row or row[0]!=bid or row[1]!='group_video_share' or row[2]!='delivery_unknown' or row[3] is None
                or source_id not in json.loads(row[4]) or CARD_SOURCE not in json.loads(row[4])
                or evidence.get('expected_video_id')!=source['video_id'] or not evidence.get('new_own_official_card_title')
                or not evidence.get('png_sha256') or not evidence.get('original_before_observation_id')
                or binding.app=='com.tencent.mm' and not evidence.get('owner_avatar_matched_to_verified_account')):raise Blocked('MEDIA_NATIVE_UNKNOWN_REVIEW_EVIDENCE_INSUFFICIENT')
        binding.validate(row[3])
        proof={**evidence,'original_unknown_evidence':json.loads(row[5] or '{}'),'review_session':self.store.session,'outgoing_sends_in_review':0}
        cursor=self.db.execute("UPDATE social_outbox SET status='sent_observed',observed=?,evidence=? WHERE id=? AND device=? AND status='delivery_unknown'",(time.time(),json.dumps(proof),outbound_id,self.store.serial));self.db.commit()
        if cursor.rowcount!=1:raise Blocked('MEDIA_NATIVE_REVIEW_STATE_CHANGED')
        self.store.event('social_native_card_read_only_reconciled',{'outbound_id':outbound_id,'evidence':proof,'dispatch_replayed':False})
        self.record_exchange(bid,'group_video_share',outbound_id)
        return {'outcome':'sent_observed','outbound_id':outbound_id,'mode':'official_SDK_card','review_only':True}

    def prepare_native(self,policy,binding,source_id,motive,*,requested_retest=False):
        source=VideoSources(self.store).get(source_id);bid=self.bind(binding)
        if getattr(policy,'acceptance_source',None):
            if (policy.acceptance_source!=source_id or policy.retest_binding!=bid or not 0<policy.retest_deadline-time.time()<=600
                    or type(policy.acceptance_limit)is not int or not 1<=policy.acceptance_limit<=5):raise Blocked('MEDIA_NATIVE_ACCEPTANCE_SCOPE_REQUIRED')
            self.store.event('media_native_explicit_acceptance_scope',{'source_id':source_id,'binding_id':bid,'deadline':policy.retest_deadline,
                'fixed_group_share_limit':policy.acceptance_limit,'maximum_additional_cards':1,'ordinary_daily_limits_unchanged':True})
        if policy.read().get('shares',{}).get('following_only',True) and not source['followed']:raise Blocked('MEDIA_UPLOADER_NOT_FOLLOWED')
        previous=self.db.execute("SELECT status,sources FROM social_outbox WHERE device=? AND binding_id=? AND kind='group_video_share' AND trigger_id=?",(self.store.serial,bid,source['video_id'])).fetchone()
        trigger=source['video_id']
        if requested_retest:
            if (not previous or previous[0]!='sent_observed' or CARD_SOURCE in json.loads(previous[1])
                    or getattr(policy,'retest_binding',None)!=bid or not 0<policy.retest_deadline-time.time()<=600):raise Blocked('MEDIA_NATIVE_RETEST_SCOPE_REQUIRED')
            trigger+=':official_card_acceptance'
            self.store.event('media_native_explicit_retest_scope',{'binding_id':bid,'source_id':source_id,'deadline':policy.retest_deadline,'maximum_additional_cards':1,'ordinary_daily_limits_unchanged':True})
        elif previous:raise Blocked('MEDIA_VIDEO_GROUP_INTENT_ALREADY_RECORDED_NO_RESHARE')
        description='[Bilibili official card] '+' '.join(source['title'].split())[:120]+' '+source['public_url']
        return SocialStore.prepare(self,policy,binding,'group_video_share',trigger,description,motive,[source_id,CARD_SOURCE])

    def before_send(self,policy,binding,outbound_id,now=None):
        row=self.db.execute('SELECT sources,text,status FROM social_outbox WHERE id=? AND device=?',(outbound_id,self.store.serial)).fetchone()
        if not row or row[2]!='card_ready' or CARD_SOURCE not in json.loads(row[0]):raise Blocked('MEDIA_NATIVE_CARD_NOT_PREPARED')
        sources=[s for s in json.loads(row[0]) if s.startswith('video:')]
        if len(sources)!=1 or not row[1].endswith(' '+VideoSources(self.store).get(sources[0])['public_url']):raise Blocked('MEDIA_NATIVE_CARD_SOURCE_CHANGED')
        if getattr(policy,'acceptance_source',None) and sources[0]!=policy.acceptance_source:raise Blocked('MEDIA_NATIVE_ACCEPTANCE_SCOPE_REQUIRED')
        return SocialStore.before_send(self,policy,binding,outbound_id,now)


class NativeCardAgent:
    def __init__(self,loop):self.loop=loop

    async def run(self,source_id,binding_id,*,requested_retest=False):
        from dataclasses import replace
        from .native_share import NativeShareUI,QQ,WX
        from .visual import OcrClient
        loop=self.loop;loop.check_stop();source=VideoSources(loop.store).get(source_id)
        row=loop.store.db.execute('SELECT app FROM social_bindings WHERE id=? AND device=?',(binding_id,loop.config.serial)).fetchone()
        if not row:raise Blocked('MEDIA_NATIVE_GROUP_BINDING_REQUIRED')
        if requested_retest and loop.run_kind!='scripted':raise Blocked('MEDIA_NATIVE_RETEST_SCOPE_REQUIRED')
        trigger=source['video_id']+(':official_card_acceptance' if requested_retest else '')
        if loop.store.db.execute("SELECT 1 FROM social_outbox WHERE device=? AND binding_id=? AND kind='group_video_share' AND trigger_id=?",(loop.config.serial,binding_id,trigger)).fetchone():raise Blocked('MEDIA_VIDEO_GROUP_INTENT_ALREADY_RECORDED_NO_RESHARE')
        acceptance=loop.run_kind=='scripted' and bool(getattr(loop,'media_acceptance_context',None)) and not requested_retest
        count=loop.store.db.execute("SELECT count(*) FROM social_outbox WHERE device=? AND binding_id=? AND kind='group_video_share' AND created>? AND status!='cancelled_before_send'",(loop.config.serial,binding_id,time.time()-86400)).fetchone()[0]
        if acceptance:
            scope=getattr(loop,'_media_acceptance_scope',None)
            if scope is None:
                scope=loop._media_acceptance_scope={'source_id':source_id,'binding_id':binding_id,'limit':count+1}
            if scope['source_id']!=source_id or scope['binding_id']!=binding_id:raise Blocked('MEDIA_NATIVE_ACCEPTANCE_SCOPE_REQUIRED')
            count=scope['limit']-1
        policy=NativeCardPolicy(loop.config,retest_binding=binding_id if requested_retest or acceptance else None,
            deadline=loop.store.state['deadline'] if requested_retest or acceptance else 0,
            acceptance_source=source_id if acceptance else None,acceptance_limit=count+1 if acceptance else None)
        cards=NativeCardStore(loop.store);ui=NativeShareUI(loop)
        if row[0]==QQ:binding,modal=await ui.prepare_qq(source,binding_id)
        elif row[0]==WX:binding,modal=await ui.prepare_wechat(source,binding_id)
        else:raise Blocked('MEDIA_NATIVE_APP_UNSUPPORTED')
        ui.confirmation(modal,source,binding)
        if any(cards.opt_out(binding_id,m['text']) for m in getattr(ui,'context',{}).get('messages',[]) if m.get('direction')=='incoming'):
            return {'outcome':'group_opted_out','binding_id':binding_id}
        def validate(value):
            if (not isinstance(value,dict) or set(value)!={'decision','motive','sources'} or value['decision'] not in {'send','defer','ignore'}
                    or not isinstance(value['motive'],str) or len(value['motive'])>200 or value['sources'] not in ([],[source_id])
                    or value['decision']=='send' and (not value['motive'] or value['sources']!=[source_id])):raise ValueError('NATIVE_SHARE_DECISION_INVALID')
        draft=await loop.guarded(loop.brain.structured('决定是否将提供的真实公开视频，使用当前官方分享卡片发到这个已授权并核对身份的群。只选意图与动机，不生成其他文字，不引用群外私聊；普通分享应与本群话题有关。可暂缓或忽略。返回JSON {"decision":"send/defer/ignore","motive":"具体理由","sources":["给定source_id"]}。',
            {'video':{k:source[k] for k in ('source_id','title','summary','uncertainty','source_type')},'current_group':getattr(ui,'context',{}),
             'operator_test_context':('用户明确要求完成官方分享功能验收，之前的链接发送已验收；本轮只再发一张该视频的官方卡片，不重复链接。这是功能测试，因此不要求群里恰好在讨论视频主题。无退订或权限问题时可选择send；仍不得忽略真实群内的停止要求。' if requested_retest
                 else getattr(loop,'media_acceptance_context',None) if loop.run_kind=='scripted' else None)},[],validate))
        loop.store.event('media_native_share_decision',{'binding_id':binding_id,'source_id':source_id,'decision':draft['decision'],'motive':draft['motive'],'explicit_acceptance':requested_retest})
        if draft['decision']!='send':return {'outcome':draft['decision'],'binding_id':binding_id}
        if loop.deadline-time.monotonic()<150:raise Blocked('MEDIA_NATIVE_POST_OBSERVATION_TIME_REQUIRED')
        fresh=await ui.ui.observe();target=ui.confirmation(fresh,source,binding)
        binding=replace(binding,verified_at=time.time(),observations=(*binding.observations,fresh.observation_id))
        oid=cards.prepare_native(policy,binding,source_id,draft['motive'],requested_retest=requested_retest);phase='prepared'
        try:
            cards.transition(oid,'prepared','card_ready',{'observation_id':fresh.observation_id,'payload_mode':'official_SDK_card','text_input_attempted':False});phase='card_ready'
            if target.control_id in fresh.visual_controls or target.clickable is not True:
                from .visual import add_control
                target=add_control(fresh,'发送',list(target.bounds),1,'vision','official_share_send')
            command=await loop.guarded(loop.phone.prepare({'type':'tap','control_id':target.control_id},target,fresh))
            loop.check_stop();cards.before_send(policy,binding,oid)
            cards.transition(oid,'card_ready','dispatch_attempted',{'observation_id':fresh.observation_id,'source_id':source_id,'payload_mode':'official_SDK_card'});phase='dispatch_attempted'
            after,_=await loop.send(command,{'type':'native_official_card_send','outbound_id':oid,'binding_id':binding_id},fresh)
            if row[0]==WX:
                after=await ui.cancel_wechat_picker(ui.ui,after)
                after=await ui.restore_wechat(ui.ui,after)
            for _ in range(3):
                if 'QPublicTransFragmentActivity' not in after.foreground and 'SelectConversationUI' not in after.foreground:break
                after=await ui.ui.act({'type':'back'},after)
            # Re-enter the actual group through the normal independent account,
            # group number/membership or WeChat roster-fingerprint checks.
            verified,context,chat=await ui.ui.resolve(binding_id=binding_id)
            if verified.identity!=binding.identity:raise Blocked('MEDIA_NATIVE_POST_TARGET_CHANGED')
            if row[0]==QQ:
                evidence=ui.qq_card_evidence(ui.before_chat,chat,source)
                if not evidence and loop.config.ocr_python:
                    reader=OcrClient(loop.config.ocr_python)
                    try:
                        if ui.before_chat:await loop.guarded(reader.read(ui.before_chat))
                        chat=await loop.guarded(reader.read(chat));loop.observed(chat)
                    finally:await reader.close()
                    evidence=ui.qq_card_evidence(ui.before_chat,chat,source)
            else:evidence=await ui.wechat_card_evidence(chat,source)
            phase='sent_observed' if evidence else 'delivery_unknown'
            cards.transition(oid,'dispatch_attempted',phase,evidence or {'observation_id':chat.observation_id,'reason':'native_card_after_send_not_confirmed','payload_mode':'official_SDK_card'})
            if evidence:cards.record_exchange(binding_id,'group_video_share',oid)
            return {'outcome':phase,'outbound_id':oid,'binding_id':binding_id,'mode':'official_SDK_card'}
        except BaseException:
            if phase=='dispatch_attempted':cards.transition(oid,phase,'delivery_unknown',{'reason':'native_dispatch_or_post_observation_uncertain'})
            elif phase in {'prepared','card_ready'}:cards.transition(oid,phase,'cancelled_before_send',{'reason':'native_card_cancelled_before_send'})
            raise
