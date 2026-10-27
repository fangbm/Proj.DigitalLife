"""Source-bound QQ/WeChat navigation. Display names are routing hints only."""
import json
import re
import time
from datetime import datetime
from difflib import SequenceMatcher
from .safety import Blocked, guard, rebind
from .social import APPS, Binding, SocialStore, digest, incoming_reference, CHINA

QQ = 'com.tencent.mobileqq'
WX = 'com.tencent.mm'


def event_match_source(event,incoming,routed):
    if not event or not incoming:return None
    last=incoming[-1]
    if event['preview'].strip().endswith(last['text'].strip()):return 'notification_preview_exact'
    if (routed and event['preview'].strip().endswith('已隐藏敏感通知内容')
            and last.get('time_label')==datetime.fromtimestamp(event['posted_at'],CHINA).strftime('%H:%M')):
        return 'package_owned_notification_route_and_UI_post_minute'
    return None


class AndroidSocialUI:
    def __init__(self, loop, app=None):
        self.loop, self.app = loop, app
        self.sources = []
        self.last_binding = None

    async def observe(self):
        obs = await self.loop.guarded(self.loop.phone.observe())
        self.loop.observed(obs); self.sources.append(obs.observation_id)
        return obs

    def one(self, obs, *, resource=None, description=None, text=None):
        found = [c for c in obs.controls if c.package == self.app and not c.covered and c.visible is not False
            and (resource is None or c.resource == resource) and (description is None or c.description == description)
            and (text is None or c.text == text)]
        if len(found) != 1:
            raise Blocked('SOCIAL_UI_ROLE_UNAVAILABLE_OR_AMBIGUOUS')
        return found[0]

    def visual_text(self,obs,text,area=None):
        found=[c for c in obs.controls if c.text==text and c.control_id in obs.visual_controls
               and (area!='bottom' or c.bounds[1]>obs.height*.9)]
        if len(found)!=1:raise Blocked('SOCIAL_VISUAL_TEXT_AMBIGUOUS_OR_MISSING')
        return found[0]

    def wx_identity(self,obs):
        prefixes=[c for c in obs.controls if re.match(r'^微信号\s*[:：]',c.text)]
        if len(prefixes)!=1:raise Blocked('WECHAT_VISIBLE_ACCOUNT_ID_UNAVAILABLE')
        prefix=prefixes[0];raw=re.sub(r'^微信号\s*[:：]\s*','',prefix.text).strip();value=prefix
        if not raw:
            cy=(prefix.bounds[1]+prefix.bounds[3])/2
            right=[c for c in obs.controls if c.bounds[0]>=prefix.bounds[2] and abs((c.bounds[1]+c.bounds[3])/2-cy)<(prefix.bounds[3]-prefix.bounds[1])*.6 and re.fullmatch(r'[A-Za-z0-9_-]{3,64}',c.text)]
            if len(right)!=1:raise Blocked('WECHAT_VISIBLE_ACCOUNT_ID_AMBIGUOUS')
            value=right[0];raw=value.text
        if not re.fullmatch(r'[A-Za-z0-9_-]{3,64}',raw):raise Blocked('WECHAT_VISIBLE_ACCOUNT_ID_UNVERIFIED')
        glyph=obs.visual_controls.get(value.control_id,{}).get('glyph_sha256')
        if not glyph:raise Blocked('WECHAT_ID_FIELD_IMAGE_PROOF_MISSING')
        return raw+'@'+glyph

    async def visual_tap(self,obs,role):
        from .visual import locate
        control=await locate(self.loop,obs,role)
        return await self.act({'type':'tap','control_id':control.control_id},obs)

    def wx_root(self,obs):
        tabs={c.text for c in obs.controls if c.bounds[1]>obs.height*.9}
        return len(tabs&{'微信','通讯录','发现','我'})>=2

    def chat_hint(self,obs):
        if self.app==QQ:return self.one(obs,resource=QQ+':id/3o4').text
        if obs.foreground.split('/')[0]!=WX or self.wx_root(obs):raise Blocked('WECHAT_CHAT_NOT_OPEN')
        header=[c for c in obs.controls if c.text and obs.height*.05<(c.bounds[1]+c.bounds[3])/2 and c.bounds[3]<=obs.height*.10
                and obs.width*.15<(c.bounds[0]+c.bounds[2])/2<obs.width*.8]
        if len(header)!=1:raise Blocked('WECHAT_CHAT_HEADER_AMBIGUOUS')
        return header[0].text

    async def act(self, action, before=None):
        before = before or await self.observe()
        # Deterministic next-step navigation selects from the immediately
        # preceding independent post-observation; no model wait intervenes.
        # Older sources are recaptured and rebound before preparing input.
        fresh = before if time.monotonic()-before.monotonic <= 2 else await self.observe()
        if fresh is not before and before.visual_controls.get(action.get('control_id'),{}).get('kind')=='vision':
            from .visual import rebind_region
            old=next(c for c in before.controls if c.control_id==action['control_id'])
            rebind_region(before,fresh,old)
        apps = await self.loop.guarded(self.loop.phone.catalog()) if action['type']=='open_app' else {}
        if action['type'] == 'open_app':
            guard(action, fresh, apps, full_access=self.loop.config.full_access); target=None
        else:
            target = rebind(action, before, fresh, apps, full_access=self.loop.config.full_access)
            if target:action={**action,'control_id':target.control_id}
        command = await self.loop.guarded(self.loop.phone.prepare(action, target, fresh))
        after, _ = await self.loop.send(command, action, fresh)
        self.sources.append(after.observation_id)
        return after

    async def tap(self, obs, **selector):
        return await self.act({'type':'tap','control_id':self.one(obs,**selector).control_id},obs)

    async def root(self):
        obs=await self.observe()
        if obs.foreground.split('/')[0] != self.app:
            obs=await self.act({'type':'open_app','package_name':self.app},obs)
        for _ in range(7):
            if self.app==QQ:
                main=any(c.resource==QQ+':id/ba1' and not c.covered for c in obs.controls)
                drawer=any(c.resource==QQ+':id/psc' for c in obs.controls)
                if main and not drawer and not any(c.resource==QQ+':id/input' for c in obs.controls):return obs
            else:
                if self.wx_root(obs):return obs
            obs=await self.act({'type':'back'},obs)
        raise Blocked('SOCIAL_ROOT_UNAVAILABLE')

    async def self_account(self):
        obs=await self.root()
        if self.app==WX:
            if any(re.match(r'^微信号\s*[:：]',c.text) for c in obs.controls) and len({c.text for c in obs.controls}&{'服务','收藏','表情','设置'})>=2:
                return self.wx_identity(obs)
            try:control=self.visual_text(obs,'我','bottom');obs=await self.act({'type':'tap','control_id':control.control_id},obs)
            except Blocked:obs=await self.visual_tap(obs,'tab_me')
            if len({c.text for c in obs.controls}&{'服务','收藏','表情','设置'})<2:raise Blocked('WECHAT_SELF_PROFILE_ROLE_UNVERIFIED')
            return self.wx_identity(obs)
        obs=await self.tap(obs,resource=QQ+':id/ba1')
        obs=await self.tap(obs,resource=QQ+':id/psc')
        if any(c.text=='恭喜你获得新勋章' for c in obs.controls):
            obs=await self.tap(obs,resource=QQ+':id/close')
        if self.one(obs,resource=QQ+':id/ivTitleName').text!='我的资料':
            raise Blocked('SOCIAL_SELF_ACCOUNT_ROLE_UNVERIFIED')
        names=[c.text for c in obs.controls if c.resource==QQ+':id/ww3' and c.text]
        if len(names)==1:self.account_display_name=names[0]
        match=re.fullmatch(r'QQ:\s*(\d{5,12})',self.one(obs,resource=QQ+':id/gmx').text)
        if not match:raise Blocked('SOCIAL_SELF_ACCOUNT_ID_UNAVAILABLE')
        return match[1]

    async def open_hint(self, hint):
        obs=await self.root()
        if self.app==QQ:
            rows=[c for c in obs.controls if c.resource==QQ+':id/aua' and c.description.startswith(hint+',') and not c.covered]
            if len(rows)!=1:raise Blocked('SOCIAL_RECENT_THREAD_HINT_AMBIGUOUS_OR_MISSING')
            return await self.act({'type':'tap','control_id':rows[0].control_id},obs)
        control=self.visual_text(obs,'微信','bottom');obs=await self.act({'type':'tap','control_id':control.control_id},obs)
        hints={hint,re.sub(r'[（(]\d+[）)]$','',hint)}
        found=[c for c in obs.controls if c.text in hints and obs.height*.12<c.bounds[1]<obs.height*.85]
        if len(found)!=1:raise Blocked('SOCIAL_RECENT_THREAD_HINT_AMBIGUOUS_OR_MISSING')
        return await self.act({'type':'tap','control_id':found[0].control_id},obs)

    def composer(self, obs):
        if obs.foreground.split('/')[0] != self.app or obs.redacted or obs.incomplete:
            raise Blocked('SOCIAL_CHAT_SCENE_UNVERIFIED')
        if self.app==QQ:return self.one(obs,resource=self.app+':id/input')
        self.chat_hint(obs)
        return None  # The actual editor is verified through InputConnection.

    def verify_composer(self, obs, text):
        editor=self.composer(obs)
        if self.app==WX:
            proof=obs.input_editor
            if proof.get('source')!='Android_InputConnection_independent_read' or proof.get('package')!=WX or proof.get('observation_id')!=obs.observation_id or proof.get('text_sha256')!=digest(text):
                raise Blocked('SOCIAL_DRAFT_OR_THREAD_CHANGED')
        elif editor.text!=text:raise Blocked('SOCIAL_DRAFT_OR_THREAD_CHANGED')

    async def verify_peer(self, chat, account, hint, expected=None):
        self.composer(chat)
        if self.app==QQ and (expected and expected.kind=='group' or any(c.resource==QQ+':id/3o2' for c in chat.controls)):
            from .groups import verify_qq
            return await verify_qq(self,chat,account,hint,expected)
        if self.app==WX:
            info=await self.visual_tap(chat,'chat_info')
            if info.foreground.endswith('chatroom.ui.ChatroomInfoUI'):
                from .groups import verify_wechat
                return await verify_wechat(self,info,account,hint,expected)
            if expected and expected.kind=='group':raise Blocked('WECHAT_GROUP_BECAME_DM')
            contact=self.visual_text(info,hint)
            profile=await self.act({'type':'tap','control_id':contact.control_id},info)
            if not profile.foreground.endswith('ContactInfoUI'):raise Blocked('WECHAT_CONTACT_PROFILE_ROLE_UNVERIFIED')
            peer=self.wx_identity(profile)
            if peer.split('@',1)[0]==account.split('@',1)[0]:raise Blocked('SOCIAL_SELF_CONVERSATION_NOT_A_PEER')
            if any(c.text=='添加到通讯录' for c in profile.controls):raise Blocked('SOCIAL_EXISTING_CONTACT_NOT_VERIFIED')
            settings=await self.visual_tap(profile,'profile_more')
            if not settings.foreground.endswith('ProfileSettingUI'):raise Blocked('WECHAT_FRIEND_SETTINGS_ROLE_UNVERIFIED')
            if not any(c.text=='删除' for c in settings.controls):raise Blocked('SOCIAL_EXISTING_CONTACT_NOT_VERIFIED')
            profile=await self.act({'type':'back'},settings)
            info=await self.act({'type':'back'},profile)
            after=await self.act({'type':'back'},info)
            if self.chat_hint(after)!=hint:raise Blocked('SOCIAL_CHAT_TITLE_CHANGED_AFTER_PROFILE')
            source='v2_autonomous' if self.loop.run_kind=='autonomous' else 'live_scripted' if self.loop.run_kind=='scripted' else 'fixture'
            binding=Binding(WX,account,peer,'dm',True,tuple(self.sources),time.time(),source,hint)
            if expected and binding.identity!=expected.identity:raise Blocked('SOCIAL_CONVERSATION_CHANGED')
            self.last_binding=binding
            return binding,after
        profile=await self.tap(chat,resource=QQ+':id/3o4')
        match=re.fullmatch(r'QQ:\s*(\d{5,12})',self.one(profile,resource=QQ+':id/gmx').text)
        if not match or match[1]==account:raise Blocked('SOCIAL_PEER_ID_UNVERIFIED')
        peer=match[1]
        # Membership is derived from the app's contact-management screen,
        # never from the nickname or a message claiming friendship.
        settings=await self.tap(profile,description='设置')
        existing=any(c.text=='删除好友' and c.package==self.app and not c.covered for c in settings.controls)
        if not existing:raise Blocked('SOCIAL_EXISTING_CONTACT_NOT_VERIFIED')
        profile=await self.act({'type':'back'},settings)
        after=await self.act({'type':'back'},profile)
        self.composer(after)
        if self.one(after,resource=QQ+':id/3o4').text!=hint:raise Blocked('SOCIAL_CHAT_TITLE_CHANGED_AFTER_PROFILE')
        source='v2_autonomous' if self.loop.run_kind=='autonomous' else 'live_scripted' if self.loop.run_kind=='scripted' else 'fixture'
        binding=Binding(self.app,account,peer,'dm',True,tuple(self.sources),time.time(),source,hint)
        if expected and binding.identity!=expected.identity:raise Blocked('SOCIAL_CONVERSATION_CHANGED')
        self.last_binding=binding
        return binding,after

    async def resolve(self, *, event=None, binding_id=None):
        self.sources=[]
        expected=None
        routed_from_other_app=False
        initial_chat=None
        if binding_id:
            row=self.loop.store.db.execute('SELECT app,account,peer,kind,body FROM social_bindings WHERE id=? AND device=?',(binding_id,self.loop.config.serial)).fetchone()
            if not row:raise Blocked('SOCIAL_BINDING_NOT_OFFERED')
            body=json.loads(row[4]);self.app=row[0];hint=body['route_hint']
            expected=Binding(row[0],row[1],row[2],row[3],body['existing_contact'],tuple(body['observations']),time.time(),body['source_type'],hint)
        else:
            if event:
                self.app=event['source_app']
                row=self.loop.store.db.execute('SELECT source_key FROM inbound_events WHERE id=? AND device=?',(event['event_id'],self.loop.config.serial)).fetchone()
                if not row or not self.loop.bridge:raise Blocked('SOCIAL_EVENT_ROUTE_UNAVAILABLE')
                before=await self.observe()
                if before.foreground.split('/')[0]==self.app:
                    before=await self.act({'type':'home'},before)
                route=await self.loop.guarded(self.loop.bridge.request('POST','/route_status',{'event_key':row[0]}))
                if route.get('available') is not True:raise Blocked('SOCIAL_NOTIFICATION_ROUTE_UNAVAILABLE')
                async def open_event():
                    value=await self.loop.bridge.request('POST','/open_event',{'event_key':row[0]})
                    if value.get('dispatched') is not True:raise Blocked('SOCIAL_EVENT_ROUTE_UNCONFIRMED')
                opened,_=await self.loop.send_operation(open_event,{'type':'social_open_event','event_id':event['event_id']},before)
                routed_from_other_app=(before.foreground.split('/')[0]!=self.app and opened.foreground.split('/')[0]==self.app)
            chat=await self.observe()
            self.app=self.app or chat.foreground.split('/')[0]
            if self.app not in APPS:raise Blocked('SOCIAL_APP_UNSUPPORTED')
            self.composer(chat)
            hint=self.chat_hint(chat)
            initial_chat=chat
        account=await self.self_account()
        chat=await self.open_hint(hint)
        binding,chat=await self.verify_peer(chat,account,hint,expected)
        required=getattr(self.loop,'social_acceptance_marker',None) if self.loop.run_kind=='scripted' else None
        if binding.kind=='group' and required:
            for _ in range(3):
                if any(required in m['text'] for m in self.messages(chat)):break
                chips=[c for c in chat.controls if '有人@我' in c.text and c.bounds[0]>chat.width*.6]
                if len(chips)!=1:break
                chat=await self.act({'type':'tap','control_id':chips[0].control_id},chat)
            if (not any(required in m['text'] for m in self.messages(chat)) and initial_chat is not None
                    and any(required in m['text'] for m in self.messages(initial_chat))):
                # Context captured before account/group navigation is still an
                # observed receipt, even if WeChat reopens at the latest chat.
                # Inputs below are always refreshed; this frame is model data.
                chat=initial_chat
                self.loop.store.event('group_receipt_context_retained',{'observation_id':chat.observation_id,
                    'fresh_group_identity_verified':True,'used_for_model_context_only':True})
            receipt=getattr(self.loop,'group_receipt_context',None)
            if not any(required in m['text'] for m in self.messages(chat)) and receipt:
                if receipt['identity']!=binding.identity:raise Blocked('GROUP_RECEIPT_TARGET_MISMATCH')
                old=receipt['observation']
                normalize=lambda s:re.sub(r'[（(]\d+[）)]$','',s)
                if normalize(self.chat_hint(old))!=normalize(hint):raise Blocked('GROUP_RECEIPT_TITLE_MISMATCH')
                if not any(required in m['text'] for m in self.messages(old)):raise Blocked('GROUP_RECEIPT_MESSAGE_MISSING')
                chat=old
                self.loop.store.event('group_receipt_context_retained',{'observation_id':old.observation_id,
                    'proof_observations':receipt['proof_observations'],'fresh_group_identity_verified':True,
                    'used_for_model_context_only':True,'source':'actual_previously_observed_receipt'})
        messages=self.messages(chat)
        incoming=[m for m in messages if m['direction']=='incoming']
        match=event_match_source(event,incoming,routed_from_other_app)
        context={'kind':binding.kind,'messages':messages[-12:],'incoming_ref':incoming_reference(binding,incoming[-1]) if incoming else None,
                 'trigger_ref':event['event_id'] if event else incoming_reference(binding,incoming[-1]) if incoming else None,
                 'event_matched':bool(match),'event_match_source':match}
        if binding.kind=='group':
            name=getattr(self,'group_own_name','')
            for message in messages:
                message['mentions_self']=bool(name and re.search(r'[@＠]\s*'+re.escape(name)+r'(?:\s|\u2005|[，,:：]|(?=M4-GROUP-)|$)',message['text']))
            context['mentions_self']=bool(incoming and incoming[-1].get('mentions_self'))
            context['mention_evidence']='visible_at_own_group_name' if context['mentions_self'] else None
        return binding,context,chat

    async def recheck(self,binding,expected_draft='',input_controller=None):
        self.sources=[]
        self.app=binding.app
        account=await self.self_account()
        if account!=binding.account:raise Blocked('SOCIAL_ACCOUNT_CHANGED')
        chat=await self.open_hint(binding.route_hint)
        fresh,chat=await self.verify_peer(chat,account,binding.route_hint,binding)
        if self.app==WX:
            if not input_controller:raise Blocked('WECHAT_EDITOR_READBACK_REQUIRED')
            chat=await self.enrich_editor(chat,input_controller,expected_draft)
        self.verify_composer(chat,expected_draft)
        return fresh,chat

    async def refresh(self,binding):
        chat=await self.observe()
        if self.app==QQ:self.verify_composer(chat,'')
        else:self.composer(chat)
        return await self.verify_peer(chat,binding.account,binding.route_hint,binding)

    async def focus_editor(self,obs):
        if self.app==WX:return await self.visual_tap(obs,'chat_editor')
        self.verify_composer(obs,'')
        return await self.act({'type':'tap','control_id':self.composer(obs).control_id},obs)

    def send_control(self,obs):
        if self.app==WX:return self.visual_text(obs,'发送')
        return self.one(obs,resource=self.app+':id/send_btn',text='发送')

    async def enrich_editor(self,obs,controller,expected):
        if self.app!=WX:return obs
        try:await controller.observe_editor(obs)
        except Blocked:
            obs=await self.focus_editor(obs);await controller.observe_editor(obs)
        self.verify_composer(obs,expected)
        self.draft_text=expected
        # An explicit image-grounded editing area excludes multiline drafts
        # from the incoming-message OCR context.
        from .visual import locate
        area=await locate(self.loop,obs,'chat_editor')
        self.editor_bounds=obs.visual_controls[area.control_id].get('grounded_bounds',area.bounds)
        return obs

    def messages(self,obs):
        if self.app==WX:
            groups={};stamp=''
            ceiling=getattr(self,'editor_bounds',(0,int(obs.height*.87),0,0))[1]
            for c in sorted(obs.controls,key=lambda c:(c.bounds[1],c.bounds[0])):
                if not c.text:continue
                if (c.bounds[1]>obs.height*.11 and re.fullmatch(r'(?:\d{1,2}:\d{2}|(?:昨天|前天|\d{1,2}月\d{1,2}日)\s*\d{1,2}:\d{2})',c.text)):
                    stamp=c.text;continue
                proof=obs.visual_controls.get(c.control_id,{});bubble=proof.get('bubble_hint')
                if not bubble or c.bounds[1]<obs.height*.12 or c.bounds[3]>ceiling:continue
                box=tuple(bubble['bounds']);direction='outgoing' if bubble['kind']=='outgoing_green' else 'incoming'
                if direction=='incoming' and box[0]>obs.width*.35:continue
                key=(box,direction)
                if key not in groups:groups[key]={'text':'','direction':direction,'time_label':stamp,'source_observation':obs.observation_id,'bubble_bounds':box}
                groups[key]['text']+=c.text
            return sorted(groups.values(),key=lambda m:m['bubble_bounds'][1])
        if self.app!=QQ:return []
        avatars=[c for c in obs.controls if c.description.endswith('资料卡') and not c.covered]
        result=[]
        stamp=''
        for c in obs.controls:
            if c.bounds[1]>obs.height*.11 and not c.resource and c.text==c.description and re.fullmatch(r'(?:\d{1,2}:\d{2}|(?:昨天|前天|\d{1,2}-\d{1,2})\s*\d{1,2}:\d{2})',c.text):
                stamp=c.text
            if c.resource!=QQ+':id/mjm' or not c.text or c.covered:continue
            beside=[a for a in avatars if max(a.bounds[1],c.bounds[1])<min(a.bounds[3],c.bounds[3])]
            if len(beside)!=1:continue
            side='outgoing' if (beside[0].bounds[0]+beside[0].bounds[2])/2>obs.width/2 else 'incoming'
            result.append({'text':c.text,'direction':side,'time_label':stamp,'source_observation':obs.observation_id})
        return result

    def outgoing_evidence(self,before,after,text):
        try:self.verify_composer(after,'')
        except Blocked:return None
        def matches(value):
            if self.app==QQ:return value==text
            normalized=lambda s:re.sub(r'[^\w\u3400-\u9fff]','',s)
            expected=normalized(text);actual=normalized(value)
            return bool(expected and actual and SequenceMatcher(None,expected,actual).ratio()>=.95)
        old=[m for m in self.messages(before) if m['direction']=='outgoing' and matches(m['text'])]
        new=[m for m in self.messages(after) if m['direction']=='outgoing' and matches(m['text'])]
        failure=any(re.search(r'发送失败|重新发送|发送中',c.text+' '+c.description) for c in after.controls if not c.covered)
        if len(new)>len(old) and not failure:
            return {'observation_id':after.observation_id,'png_sha256':after.png_sha256,'xml_sha256':after.xml_sha256,
                    'text_sha256':digest(text),'new_own_bubble':True,'editor_cleared':True,'read_receipt_claimed':False,
                    'post_text_match':'native_XML_exact' if self.app==QQ else 'OCR_normalized_with_green_bubble_and_exact_editor_readback'}
        return None
