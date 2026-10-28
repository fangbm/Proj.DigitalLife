"""Bilibili UI adapter. IDs below come from observed Find X6 native UI."""
import re
import time
from .social_ui import AndroidSocialUI
from .media import BILIBILI,VideoSources,VideoBinding,canonical_video
from .social import digest
from .input_bridge import InputBridge
from .safety import Blocked
from .content import ContentPerception


class BilibiliUI(AndroidSocialUI):
    def __init__(self,loop):super().__init__(loop,BILIBILI);self.actor=None

    def controls(self,obs,resource=None,text=None):
        return [c for c in obs.controls if c.package==BILIBILI and not c.covered and c.visible is not False
            and (resource is None or c.resource==BILIBILI+':id/'+resource)
            and (text is None or c.text.strip()==text)]

    async def select(self,obs,*,resource=None,text=None,visual=None):
        found=self.controls(obs,resource,text)
        if len(found)!=1:raise Blocked('BILI_UI_TARGET_MISSING_OR_AMBIGUOUS')
        try:return await self.act({'type':'tap','control_id':found[0].control_id},obs)
        except Blocked as error:
            # Only an explicit pre-dispatch affordance refusal can use a
            # separately grounded target. Unknown operations never get replayed.
            if not visual or str(error)!='UNKNOWN_ACTION_ROLE':raise
            from .visual import locate
            control=await locate(self.loop,obs,visual)
            return await self.act({'type':'tap','control_id':control.control_id},obs)

    def identity(self,obs):
        if obs.foreground.split('/')[0]!=BILIBILI or 'UnitedBizDetailsActivity' not in obs.foreground:
            raise Blocked('BILI_VIDEO_NOT_OPEN')
        descriptions=self.controls(obs,'desc')
        matches=[re.match(r'^(BV[0-9A-Za-z]{10})(?:\s|$)',c.text.strip()) for c in descriptions]
        if len(matches)!=1 or not matches[0]:raise Blocked('BILI_VISIBLE_VIDEO_ID_UNAVAILABLE')
        authors=self.controls(obs,'author_name');layouts=self.controls(obs,'author_layout')
        if len(authors)!=1 or len(layouts)!=1:raise Blocked('BILI_VIDEO_UPLOADER_UNVERIFIED')
        titles=[c for c in self.controls(obs,'title') if layouts[0].bounds[3]<c.bounds[1]<layouts[0].bounds[3]+obs.height*.15]
        if len(titles)!=1:raise Blocked('BILI_VIDEO_TITLE_AMBIGUOUS')
        following=self.controls(obs,'follow')
        followed=len(following)==1 and following[0].description.startswith('取消关注')
        return {'video_id':matches[0][1],'title':titles[0].text.strip(),'author_name':authors[0].text,'followed':followed}

    async def ensure_identity(self,obs=None):
        obs=obs or await self.observe()
        try:return self.identity(obs),obs
        except Blocked as error:
            if str(error)!='BILI_VISIBLE_VIDEO_ID_UNAVAILABLE':raise
        obs=await self.select(obs,resource='arrow',visual='bili_video_details')
        return self.identity(obs),obs

    def profile_uid(self,obs,own=False):
        if 'AuthorSpaceActivity' not in obs.foreground:raise Blocked('BILI_PROFILE_NOT_OPEN')
        if own and len(self.controls(obs,'edit_info'))!=1:raise Blocked('BILI_SELF_ACCOUNT_NOT_VERIFIED')
        ids=[c.text for c in obs.controls if c.package==BILIBILI and not c.resource and re.fullmatch('[0-9]{5,24}',c.text) and not c.covered]
        names=self.controls(obs,'header_info_name')
        if len(set(ids))!=1 or len(names)!=1:raise Blocked('BILI_PROFILE_UID_UNAVAILABLE')
        return ids[0],names[0].text

    async def author_identity(self,identity,obs):
        profile=await self.select(obs,resource='author_layout')
        try:uid,name=self.profile_uid(profile)
        except Blocked as error:
            if str(error)!='BILI_PROFILE_UID_UNAVAILABLE':raise
            profile=await self.select(profile,resource='header_info_no_verify_expand_button')
            uid,name=self.profile_uid(profile)
        if name!=identity['author_name']:raise Blocked('BILI_UPLOADER_CHANGED')
        followed=self.controls(profile,'follow_text',text='已关注')
        if bool(followed)!=identity['followed']:raise Blocked('BILI_FOLLOW_RELATION_CHANGED')
        self.loop.store.event('media_uploader_identity_observed',{'uploader_sha256':__import__('hashlib').sha256(uid.encode()).hexdigest(),'observation_id':profile.observation_id,'followed':bool(followed)})
        after=await self.act({'type':'back'},profile);fresh,after=await self.ensure_identity(after)
        if fresh['video_id']!=identity['video_id']:raise Blocked('BILI_VIDEO_CHANGED_AFTER_PROFILE')
        return uid,after

    def identity_capture(self,identity,obs):
        return {'app':BILIBILI,'kind':'native_video_identity','url':'https://www.bilibili.com/video/'+identity['video_id'],
            'title':identity['title'],'observation_id':obs.observation_id,'png_path':obs.png_path,'png_sha256':obs.png_sha256,
            'xml_path':obs.xml_path,'xml_sha256':obs.xml_sha256}

    async def watch(self,seconds=15):
        identity,obs=await self.ensure_identity();uid,obs=await self.author_identity(identity,obs)
        capture_before=self.identity_capture(identity,obs)
        replay=self.controls(obs,'replay')
        if replay:obs=await self.select(obs,resource='replay')
        result=await ContentPerception(self.loop).run({'intent':'explore','action':{'type':'watch_video','seconds':seconds},'note':'Bounded current Bilibili video perception with visible native video ID'},obs)
        fresh,after=await self.ensure_identity()
        if fresh['video_id']!=identity['video_id'] or fresh['title']!=identity['title']:raise Blocked('BILI_VIDEO_CHANGED_DURING_WATCH')
        source=VideoSources(self.loop.store).bind(result['segment_id'],public_url=canonical_video(capture_before['url'])[1],title=identity['title'],uploader=uid,followed=identity['followed'],captures=[capture_before,self.identity_capture(fresh,after)])
        self.loop.store.event('media_watched_source_ready',{'source_id':source,'video_id':identity['video_id'],'scope':'sampled_segment_only'})
        return {**result,'source_id':source}

    async def dismiss_empty_editor(self,obs):
        if not self.controls(obs,'edit'):return obs
        controller=InputBridge(self.loop)
        async with controller.temporary():
            obs=await self.observe();await controller.observe_editor(obs)
            if obs.input_editor.get('text_sha256')!=digest(''):raise Blocked('BILI_EXISTING_COMMENT_DRAFT')
            for _ in range(3):
                if not self.controls(obs,'edit'):break
                obs=await self.act({'type':'back'},obs)
        if self.controls(obs,'edit'):raise Blocked('BILI_COMMENT_EDITOR_DID_NOT_CLOSE')
        return obs

    async def home_page(self):
        obs=await self.dismiss_empty_editor(await self.observe())
        for _ in range(7):
            if obs.foreground.split('/')[0]!=BILIBILI:raise Blocked('BILI_APP_FOREGROUND_CHANGED')
            if self.controls(obs,'tab_text',text='我的'):return obs
            if self.controls(obs,'back_to_home'):
                obs=await self.select(obs,resource='back_to_home',visual='bili_video_home')
            else:obs=await self.act({'type':'back'},obs)
        raise Blocked('BILI_HOME_UNAVAILABLE')

    async def own_account(self):
        obs=await self.home_page();obs=await self.select(obs,resource='tab_text',text='我的')
        profile=await self.select(obs,resource='tv_go_to_space')
        try:uid,name=self.profile_uid(profile,own=True)
        except Blocked as error:
            if str(error)!='BILI_PROFILE_UID_UNAVAILABLE':raise
            profile=await self.select(profile,resource='header_info_no_verify_expand_button');uid,name=self.profile_uid(profile,own=True)
        self.actor={'id':uid,'name':name,'observation_id':profile.observation_id}
        self.loop.store.event('media_self_account_observed',{'account_sha256':digest(uid),'observation_id':profile.observation_id})
        return self.actor

    async def open_watched_video(self,source):
        obs=await self.home_page();obs=await self.select(obs,resource='tab_text',text='我的')
        history=await self.select(obs,resource='title',text='历史记录')
        video=await self.select(history,text=source['title'])
        identity,video=await self.ensure_identity(video)
        if identity['video_id']!=source['video_id'] or identity['title']!=source['title']:raise Blocked('BILI_HISTORY_VIDEO_MISMATCH')
        return identity,video

    def visible_comments(self,obs):
        containers=self.controls(obs,'comment_container')
        if len(containers)!=1:return []
        top=containers[0].bounds[1]
        return [c.text[:180] for c in obs.controls if c.package==BILIBILI and c.text
            and top<c.bounds[1]<obs.height*.93 and c.resource not in {BILIBILI+':id/input',BILIBILI+':id/left_button',BILIBILI+':id/right_button'}][:10]

    async def resolve_comment(self,source):
        actor=await self.own_account();identity,video=await self.open_watched_video(source)
        context_id=video.observation_id
        comments=await self.select(video,resource='tab_title',text='评论')
        if not self.controls(comments,'comment_container'):raise Blocked('BILI_COMMENT_PANEL_UNVERIFIED')
        self.comment_route={'source_id':source['source_id'],'video_id':identity['video_id'],
            'activity':comments.foreground,'origin_observation_id':context_id,'opened':time.monotonic()}
        source_type='v2_autonomous' if self.loop.run_kind=='autonomous' else 'live_scripted'
        binding=VideoBinding(BILIBILI,actor['id'],identity['video_id'],'video',identity['followed'],
            (actor['observation_id'],context_id,comments.observation_id),time.time(),source_type,identity['title'])
        return binding,{'visible_comments':self.visible_comments(comments),'actor_name':actor['name']},comments

    async def focus_comment(self,obs):return await self.select(obs,resource='input')

    async def read_comments(self,obs,expand=False):
        """Bili renders comment rows outside the accessibility tree on this build."""
        if not self.controls(obs,'comment_container'):return obs
        from .visual import OcrClient,add_control
        if not self.loop.config.ocr_python:raise Blocked('MEDIA_COMMENT_OCR_UNAVAILABLE')
        reader=OcrClient(self.loop.config.ocr_python)
        try:await self.loop.guarded(reader.read(obs))
        finally:await reader.close()
        if expand and any('展开' in c.text for c in obs.controls if c.control_id in obs.visual_controls):
            # The expand link is cyan within the last OCR line. Derive its
            # actual pixels instead of using a model's approximate tap box.
            from .media_pixels import blue_expand_bounds
            lines=[c for c in obs.controls if c.control_id in obs.visual_controls and c.text.endswith('展开')]
            if len(lines)!=1:raise Blocked('MEDIA_EXPAND_LINK_AMBIGUOUS')
            control=add_control(obs,'展开',blue_expand_bounds(obs,lines[0]),1,'vision','bili_comment_expand')
            obs=await self.act({'type':'tap','control_id':control.control_id},obs)
            reader=OcrClient(self.loop.config.ocr_python)
            try:await self.loop.guarded(reader.read(obs))
            finally:await reader.close()
        return obs

    def verify_comment_route(self,obs,binding,source):
        route=getattr(self,'comment_route',{})
        if (obs.foreground!=route.get('activity') or source['source_id']!=route.get('source_id')
                or binding.peer!=route.get('video_id') or not self.actor or binding.account!=self.actor['id']
                or time.monotonic()-route.get('opened',0)>120 or len(self.controls(obs,'edit'))!=1):
            raise Blocked('BILI_COMMENT_ROUTE_CHANGED')

    async def check_dynamic_off(self,obs):
        from types import SimpleNamespace
        from .media_pixels import empty_circle,checkbox_bounds
        labels=self.controls(obs,text='转到动态')
        if len(labels)!=1:raise Blocked('MEDIA_REPOST_TOGGLE_NOT_OBSERVED')
        bounds=checkbox_bounds(obs,labels[0]);control=SimpleNamespace(bounds=bounds)
        if not empty_circle(obs,control,labels[0]):raise Blocked('MEDIA_DYNAMIC_REPOST_NOT_VERIFIED_OFF')
        self.loop.store.event('media_dynamic_repost_verified_off',{'observation_id':obs.observation_id,
            'source_png_sha256':obs.png_sha256,'pixel_bounds':list(bounds),'method':'native_label_and_actual_empty_circle_pixels'})
        return control

    async def publish_control(self,obs):
        from .safety import guard
        found=self.controls(obs,text='发布')
        if len(found)!=1:raise Blocked('BILI_PUBLISH_CONTROL_AMBIGUOUS')
        try:
            guard({'type':'tap','control_id':found[0].control_id},obs,{},full_access=self.loop.config.full_access)
            return found[0]
        except Blocked as error:
            if str(error)!='UNKNOWN_ACTION_ROLE':raise
            from .visual import locate
            return await locate(self.loop,obs,'bili_publish_comment')

    def comment_evidence(self,before,after,text,actor_name):
        if after.foreground!=before.foreground or self.controls(after,'edit') or not self.controls(after,'comment_container'):return None
        def count(obs):return sum(c.text.strip()==text and not c.covered for c in obs.controls if c.package==BILIBILI and not c.is_editor)
        actor=any(c.text.strip()==actor_name or c.description.startswith(actor_name+'，') for c in after.controls if c.package==BILIBILI and not c.covered)
        failure=any(re.search(r'发布失败|发送失败|审核中|网络异常',c.text) for c in after.controls if not c.covered)
        if count(after)>count(before) and actor and not failure:
            return {'observation_id':after.observation_id,'png_sha256':after.png_sha256,'xml_sha256':after.xml_sha256,
                'text_sha256':digest(text),'new_comment_exact_text':True,'actor_label_observed':True,
                'composer_closed':True,'server_delivery_or_read_claimed':False}
        # Require the complete text in a single author-bounded screenshot row.
        # OCR controls retain PNG/crop hashes; they are not native UI attributes.
        import unicodedata
        normalize=lambda s:''.join(c for c in unicodedata.normalize('NFKC',s) if not c.isspace() and not unicodedata.category(c).startswith('P'))
        def rows(obs):
            regions=sorted([c for c in obs.controls if c.control_id in obs.visual_controls
                and obs.visual_controls[c.control_id].get('kind')=='ocr'
                and c.bounds[1]>self.controls(obs,'comment_container')[0].bounds[1]],key=lambda c:(c.bounds[1],c.bounds[0]))
            authors=[i for i,c in enumerate(regions) if normalize(c.text)==normalize(actor_name)]
            values=[]
            for i in authors:
                lines=[]
                for c in regions[i+1:]:
                    if re.search(r'刚刚|\d+分钟前|\d+小时前|回复|发布失败|审核中',c.text):break
                    if re.fullmatch(r'LV\d+',c.text,re.I):continue
                    lines.append(c.text)
                values.append(normalize(re.sub(r'收起$','',''.join(lines))))
            return values
        wanted=normalize(text)
        if len(wanted)>=30 and rows(after).count(wanted)>rows(before).count(wanted) and not failure:
            return {'observation_id':after.observation_id,'png_sha256':after.png_sha256,'xml_sha256':after.xml_sha256,
                'text_sha256':digest(text),'new_comment_exact_normalized_OCR':True,'actor_label_observed':True,
                'composer_closed':True,'method':'author_bounded_full_text_local_OCR','server_delivery_or_read_claimed':False}
        return None
