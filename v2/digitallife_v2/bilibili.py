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
            from .visual import locate,add_control
            if resource=='arrow' and visual=='bili_video_details':
                # The unique native detail-arrow bounds are observed, even
                # when Android does not declare a clickable role. Bind those
                # actual pixels instead of letting a model shift the arrow.
                control=add_control(obs,'',list(found[0].bounds),1,'vision',visual)
            else:control=await locate(self.loop,obs,visual)
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
        from .bili_threads import native_profile_uid
        from pathlib import Path
        import hashlib
        try:
            raw=Path(obs.xml_path).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=obs.xml_sha256:raise ValueError()
            uid=native_profile_uid(raw)
        except (OSError,TypeError,ValueError):raise Blocked('BILI_PROFILE_UID_UNAVAILABLE') from None
        names=self.controls(obs,'header_info_name')
        fields=[c for c in self.controls(obs) if not c.resource and c.text==uid and c.clickable is True
            and BILIBILI+':id/bottom_tags_layout' in c.ancestry.split()
            and 0<=c.bounds[0]<c.bounds[2]<=obs.width and 0<=c.bounds[1]<c.bounds[3]<=obs.height]
        if not uid or len(names)!=1 or len(fields)!=1:raise Blocked('BILI_PROFILE_UID_UNAVAILABLE')
        return uid,names[0].text

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
        identity,obs=await self.ensure_identity()
        capture_before=self.identity_capture(identity,obs)
        replay=self.controls(obs,'replay')
        if replay:obs=await self.select(obs,resource='replay')
        result=await ContentPerception(self.loop).run({'intent':'explore','action':{'type':'watch_video','seconds':seconds},'note':'Bounded current Bilibili video perception with visible native video ID'},obs)
        fresh,after=await self.ensure_identity()
        if fresh['video_id']!=identity['video_id'] or fresh['title']!=identity['title']:raise Blocked('BILI_VIDEO_CHANGED_DURING_WATCH')
        source=VideoSources(self.loop.store).bind(result['segment_id'],public_url=canonical_video(capture_before['url'])[1],title=identity['title'],uploader=None,uploader_name=identity['author_name'],followed=identity['followed'],captures=[capture_before,self.identity_capture(fresh,after)])
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
        obs=await self.observe()
        if obs.foreground.split('/')[0]!=BILIBILI:
            obs=await self.act({'type':'open_app','package_name':BILIBILI},obs)
        if obs.foreground.split('/')[0]!=BILIBILI:raise Blocked('BILI_APP_FOREGROUND_CHANGED')
        obs=await self.dismiss_empty_editor(obs)
        for _ in range(7):
            if obs.foreground.split('/')[0]!=BILIBILI:raise Blocked('BILI_APP_FOREGROUND_CHANGED')
            if self.controls(obs,'tab_text',text='我的'):return obs
            if self.controls(obs,'back_to_home'):
                obs=await self.select(obs,resource='back_to_home',visual='bili_video_home')
            else:obs=await self.act({'type':'back'},obs)
        raise Blocked('BILI_HOME_UNAVAILABLE')

    async def own_account(self):
        from .media import MediaPolicy
        import json
        uid=MediaPolicy(self.loop.config).read().get('accounts',{}).get(BILIBILI)
        if not uid:raise Blocked('BILI_ACCOUNT_NOT_CONFIGURED')
        cached=getattr(self.loop,'bili_actor',{})
        name=cached.get('name','') if cached.get('id')==uid else ''
        if not name:
            rows=self.loop.store.db.execute("SELECT c.body FROM content_segments c JOIN sessions s ON s.id=c.session WHERE c.kind='comment_thread' AND s.serial=? ORDER BY c.started DESC LIMIT 20",(self.loop.config.serial,)).fetchall()
            for row in rows:
                body=json.loads(row[0])
                if body.get('account')==uid and body.get('actor_name'):name=body['actor_name'];break
        self.actor={'id':uid,'name':name};self.loop.bili_actor=self.actor
        return self.actor

    async def open_watched_video(self,source):
        obs=await self.observe()
        if obs.foreground.split('/')[0]==BILIBILI and 'UnitedBizDetailsActivity' in obs.foreground:
            obs=await self.dismiss_empty_editor(obs)
            if self.controls(obs,'title',text='评论详情'):obs=await self.act({'type':'back'},obs)
            if self.controls(obs,'comment_container'):obs=await self.select(obs,resource='tab_title',text='简介')
            try:
                identity,obs=await self.ensure_identity(obs)
                if identity['video_id']==source['video_id'] and identity['title']==source['title']:return identity,obs
            except Blocked as error:
                if str(error) not in {'BILI_VISIBLE_VIDEO_ID_UNAVAILABLE','BILI_UI_TARGET_MISSING_OR_AMBIGUOUS','BILI_VIDEO_TITLE_AMBIGUOUS'}:raise
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
            (context_id,comments.observation_id),time.time(),source_type,identity['title'])
        return binding,{'visible_comments':self.visible_comments(comments),'actor_name':actor['name']},comments

    async def focus_comment(self,obs):return await self.select(obs,resource='input')

    async def inspect_replies(self,source,expected=None,notification_preview=None):
        from .bili_threads import comment_rows,normalized,ThreadSources
        binding,_,comments=await self.resolve_comment(source)
        comments=await self.read_comments(comments,expand=True)
        roots=self.loop.store.db.execute("SELECT id,text FROM social_outbox WHERE device=? AND binding_id=? AND kind='post_video_comment' AND status='sent_observed' ORDER BY created DESC",(self.loop.config.serial,__import__('hashlib').sha256((self.loop.config.serial+'\0'+'\0'.join(binding.identity)).encode()).hexdigest())).fetchall()
        candidates=[]
        for row in comment_rows(self,comments):
            prefix=normalized(re.sub(r'(?:\.{3}|…)\s*展开.*$','',row['text']))
            for root in roots:
                if (not self.actor['name'] or row['author']==self.actor['name']) and len(prefix)>=30 and normalized(root[1]).startswith(prefix):candidates.append((root,row))
        if len(candidates)!=1:raise Blocked('BILI_OWN_ROOT_COMMENT_NOT_UNIQUE')
        root,row=candidates[0]
        self.actor['name']=row['author'];self.loop.bili_actor=self.actor
        next_names=[c.bounds[1] for c in self.controls(comments,'user_name') if c.bounds[1]>row['name_control'].bounds[1]]
        bottom=min(next_names) if next_names else comments.height
        previews=[c for c in self.controls(comments,'secondary_message') if row['name_control'].bounds[1]<c.bounds[1]<bottom]
        if len(previews)!=1:raise Blocked('BILI_REPLY_PREVIEW_UNAVAILABLE')
        detail=await self.act({'type':'tap','control_id':previews[0].control_id},comments)
        async def scroll(obs,direction):
            lists=self.controls(obs,'cmt3_recycler')
            if len(lists)!=1:raise Blocked('BILI_REPLY_LIST_UNAVAILABLE')
            obs=await self.act({'type':'swipe','control_id':lists[0].control_id,'direction':direction},obs)
            await self.loop.guarded(__import__('asyncio').sleep(.4))
            return await self.observe()
        anchor=normalized(previews[0].text)
        def entered_parent(obs):
            return self.controls(obs,'title',text='评论详情') and any(
                normalized(r['text'])==normalized(root[1]) and r['author']==self.actor['name']
                or normalized(r['author']+':'+r['text'])==anchor for r in comment_rows(self,obs))
        for _ in range(2):
            if entered_parent(detail):break
            detail=await scroll(detail,'down')
        if not entered_parent(detail):raise Blocked('BILI_REPLY_PARENT_CHANGED')
        entry=detail
        def target_rows(obs):
            rows=comment_rows(self,obs)
            if not self.controls(obs,'title',text='评论详情'):raise Blocked('BILI_REPLY_PARENT_CHANGED')
            targets=[r for r in rows if r['author']!=self.actor['name']]
            if expected:targets=[r for r in targets if normalized(r['text'])==normalized(expected['text']) and r['author']==expected['sender_name']]
            if notification_preview:targets=[r for r in targets if len(normalized(r['text']))>=12 and normalized(r['text']) in normalized(notification_preview)]
            return targets
        for attempt in range(4):
            targets=target_rows(detail)
            if targets and (expected or notification_preview) or attempt==3:break
            old=[(r['author'],r['text'],r['body_control'].bounds) for r in comment_rows(self,detail)]
            fresh=await scroll(detail,'up')
            new=[(r['author'],r['text'],r['body_control'].bounds) for r in comment_rows(self,fresh)]
            detail=fresh
            if old==new:break
        targets=target_rows(detail)
        if not targets:raise Blocked('BILI_NO_INCOMING_PUBLIC_REPLY')
        incoming=targets[-1];selected=(incoming['author'],normalized(incoming['text']))
        # The actual native reply row is the target. A separate profile UID is
        # optional metadata and must not add a navigation prerequisite.
        name=incoming['author'];after=await self.observe()
        targets=[r for r in target_rows(after) if (r['author'],normalized(r['text']))==selected]
        if len(targets)!=1:raise Blocked('BILI_REPLY_SENDER_CHANGED')
        incoming=targets[0]
        if expected:
            thread=expected
            if root[0]!=thread['root_outbound_id']:raise Blocked('BILI_REPLY_ROOT_CHANGED')
        else:
            thread_id=ThreadSources(self.loop.store).bind(source['source_id'],root[0],None,name,incoming['text'],self.actor,(detail,after),None,parent=comments,entry=entry,preview_text=previews[0].text)
            thread=ThreadSources(self.loop.store).get(thread_id)
        self.reply_route={'thread_id':thread['thread_id'],'sender_name':name,'text':incoming['text'],'root_text':root[1]}
        self.comment_route.update(opened=time.monotonic(),followed=binding.existing_contact,thread_key=thread['ui_thread_key'])
        self.loop.bili_reply_context={'thread_id':thread['thread_id'],'source_id':source['source_id'],'actor':dict(self.actor),
            'route':dict(self.comment_route),'reply':dict(self.reply_route),'dispatch_count':self.loop.store.state['dispatched']}
        return thread,incoming,after

    async def resolve_reply(self,source,thread):
        from .bili_threads import comment_rows,normalized
        cached=getattr(self.loop,'bili_reply_context',{})
        reused=False
        if (cached.get('thread_id')==thread['thread_id'] and cached.get('source_id')==source['source_id']
                and cached.get('dispatch_count')==self.loop.store.state['dispatched']):
            after=await self.observe()
            rows=[r for r in comment_rows(self,after) if r['author']==thread['sender_name'] and normalized(r['text'])==normalized(thread['text'])]
            if after.foreground==cached['route']['activity'] and self.controls(after,'title',text='评论详情') and len(rows)==1:
                self.actor=dict(cached['actor']);self.comment_route=dict(cached['route']);self.reply_route=dict(cached['reply']);reused=True
        if not reused:thread,incoming,after=await self.inspect_replies(source,thread)
        self.comment_route['thread_key']=thread['ui_thread_key']
        binding=VideoBinding(BILIBILI,self.actor['id'],source['video_id']+':'+thread['ui_thread_key'],'comment_thread',self.comment_route['followed'],
            tuple(self.sources),time.time(),'v2_autonomous' if self.loop.run_kind=='autonomous' else 'live_scripted',source['title'])
        return binding,{'visible_comments':[thread['root_text'],thread['sender_name']+': '+thread['text']],'actor_name':self.actor['name'],'reply':thread},after

    async def focus_reply(self,obs):
        from .bili_threads import comment_rows,normalized
        route=self.reply_route;rows=[r for r in comment_rows(self,obs) if r['author']==route['sender_name'] and normalized(r['text'])==normalized(route['text'])]
        if len(rows)!=1:raise Blocked('BILI_REPLY_TARGET_AMBIGUOUS')
        after=await self.act({'type':'tap','control_id':rows[0]['reply_control'].control_id},obs)
        editors=self.controls(after,'edit')
        if len(editors)!=1 or route['sender_name'] not in editors[0].text:raise Blocked('BILI_REPLY_EDITOR_TARGET_UNVERIFIED')
        return after

    async def read_comments(self,obs,expand=False):
        """Bili renders comment rows outside the accessibility tree on this build."""
        if not self.controls(obs,'comment_container') and not self.controls(obs,'title',text='评论详情'):return obs
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
                or binding.peer.split(':')[0]!=route.get('video_id') or not self.actor or binding.account!=self.actor['id']
                or len(self.controls(obs,'edit'))!=1):
            raise Blocked('BILI_COMMENT_ROUTE_CHANGED')
        if binding.kind=='comment_thread' and binding.peer.split(':')[1]!=route.get('thread_key'):raise Blocked('BILI_REPLY_ROUTE_CHANGED')

    def bound_reply_parent(self,before,preceding):
        """A clipped older own reply is not a different root comment."""
        from .bili_threads import ThreadSources,comment_rows,normalized,native_comment_text
        import json
        try:
            threads=ThreadSources(self.loop.store);thread=threads.get(self.reply_route['thread_id'])
            route=self.comment_route
            if (not self.actor or self.actor['id']!=thread['account'] or route['source_id']!=thread['source_id']
                    or route['video_id']!=thread['video_id'] or route['thread_key']!=thread['ui_thread_key']
                    or normalized(thread['root_text'])!=normalized(self.reply_route['root_text'])
                    or thread['sender_name']!=self.reply_route['sender_name'] or normalized(thread['text'])!=normalized(self.reply_route['text'])):return None
            if not self.controls(before,'title',text='评论详情') or not any(r['author']==thread['sender_name'] and normalized(r['text'])==normalized(thread['text']) for r in comment_rows(self,before)):return None
            known=set()
            rows=self.loop.store.db.execute("SELECT o.text,o.sources,o.evidence FROM social_outbox o JOIN social_bindings b ON b.id=o.binding_id WHERE o.device=? AND b.account=? AND b.app=? AND o.kind='reply_video_comment' AND o.status='sent_observed'",(self.loop.store.serial,thread['account'],BILIBILI)).fetchall()
            for text,sources,evidence in rows:
                corrected=json.loads(evidence or '{}').get('corrected_thread_source_id')
                for source in [*json.loads(sources),*([corrected] if isinstance(corrected,str) else [])]:
                    if not source.startswith('thread:'):continue
                    try:old=threads.get(source)
                    except Blocked:continue
                    if old['root_outbound_id']==thread['root_outbound_id'] and old['account']==thread['account']:
                        known.add(normalized('回复 '+old['sender_name']+': '+text))
            if not all(normalized(native_comment_text(c.text)) in known for c in preceding):return None
            return {'parent_identity_proof':'bound_original_thread_and_known_prior_own_replies','bound_thread_id':thread['thread_id'],
                'parent_proof_observation_ids':[c['observation_id'] for c in thread['captures']]}
        except (AttributeError,KeyError,TypeError,ValueError,OSError,Blocked):return None

    def reply_evidence(self,before,after,text,actor_name):
        from .bili_threads import comment_rows,normalized,native_comment_text
        if after.foreground!=before.foreground or self.controls(after,'edit') or not self.controls(after,'title',text='评论详情'):return None
        rows=comment_rows(self,after)
        root_visible=any(normalized(native_comment_text(c.text))==normalized(self.reply_route['root_text']) for c in self.controls(after,'primary_message'))
        targets=[r for r in rows if r['author']==self.reply_route['sender_name'] and normalized(r['text'])==normalized(self.reply_route['text'])]
        target_visible=bool(targets)
        ancestry={}
        preceding=[c for c in self.controls(after,'primary_message') if targets and c.bounds[1]<targets[0]['body_control'].bounds[1]]
        if targets and preceding and not root_visible:
            ancestry=self.bound_reply_parent(before,preceding)
            if not ancestry:return None
        if not root_visible and not target_visible:return None
        expected=normalized('回复 '+self.reply_route['sender_name']+': '+text)
        count=lambda obs:sum(r['author']==actor_name and normalized(r['text'])==expected for r in comment_rows(self,obs))
        if count(after)>count(before):return {'observation_id':after.observation_id,'png_sha256':after.png_sha256,'xml_sha256':after.xml_sha256,'text_sha256':digest(text),
            'same_original_parent':True,'parent_text_currently_present':root_visible,'verified_incoming_question_currently_present':target_visible,
            'new_own_reply_exact_text':True,'actor_label_observed':True,'composer_closed':True,'server_delivery_or_read_claimed':False,**ancestry}

    async def read_reply_after(self,obs):
        messages=self.controls(obs,'primary_message')
        if messages and '展开' in messages[-1].text:
            lists=self.controls(obs,'cmt3_recycler')
            if len(lists)!=1:raise Blocked('BILI_REPLY_LIST_UNAVAILABLE')
            obs=await self.act({'type':'swipe','control_id':lists[0].control_id,'direction':'up'},obs)
        return await self.read_comments(obs,expand=True)

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
        if not actor_name:
            from .bili_threads import comment_rows
            rows=[r for r in comment_rows(self,after) if r['text'].strip()==text]
            if len(rows)==1:
                actor_name=rows[0]['author']
                if self.actor:self.actor['name']=actor_name;self.loop.bili_actor=self.actor
        actor=bool(actor_name) and any(c.text.strip()==actor_name or c.description.startswith(actor_name+'，') for c in after.controls if c.package==BILIBILI and not c.covered)
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
