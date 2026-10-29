"""Normal app share UI navigation; a picker query is never a sent message."""
import re
import uuid
import time,json,hashlib
from pathlib import Path
from dataclasses import replace
from PIL import Image,ImageChops,ImageStat
from .input_bridge import InputBridge
from .social_ui import AndroidSocialUI,QQ,WX
from .media import MediaPolicy,BILIBILI
from .safety import Blocked
from .social import digest


def plain(value):return re.sub(r'[\W_]+','',value).lower()


class NativeShareUI:
    def __init__(self,loop):self.loop=loop

    @staticmethod
    def wechat_group_row(obs,label):
        headings=[c for c in obs.controls if c.text=='最近聊天' and not c.covered]
        if len(headings)!=1:raise Blocked('MEDIA_WECHAT_NATIVE_RECENT_SECTION_UNVERIFIED')
        labels=[c for c in obs.controls if plain(c.text)==plain(label.text) and not c.covered and c.bounds[1]>headings[0].bounds[3]]
        if len(labels)!=1:raise Blocked('MEDIA_WECHAT_NATIVE_GROUP_ROW_AMBIGUOUS')
        return labels[0]

    @staticmethod
    def title_blocks(obs,title,controls=None,*,allow_truncated=False):
        wanted=plain(title);regions=[c for c in (obs.controls if controls is None else controls) if c.text and not c.covered]
        result=[]
        for first in regions:
            value=plain(first.text)
            if len(value)<min(4,len(wanted)) or not wanted.startswith(value):continue
            block=[first]
            for _ in range(3):
                if value==wanted:break
                last=block[-1];height=last.bounds[3]-last.bounds[1]
                def adjacent(c):
                    same_line=(abs((c.bounds[1]+c.bounds[3]-last.bounds[1]-last.bounds[3])/2)<=height*.35
                        and 0<=c.bounds[0]-last.bounds[2]<=height*1.5)
                    next_line=(c.bounds[1]>=last.bounds[3]-height*.15 and c.bounds[1]<=last.bounds[3]+max(30,height*1.5)
                        and abs(c.bounds[0]-first.bounds[0])<=max(20,obs.width*.04))
                    return same_line or next_line
                following=[c for c in regions if c not in block and adjacent(c) and plain(c.text) and wanted.startswith(value+plain(c.text))]
                if len(following)!=1:break
                block.append(following[0]);value+=plain(following[0].text)
            if value==wanted:result.append(block)
            elif allow_truncated and len(value)>=max(16,int(len(wanted)*.75)):
                from .native_pixels import trailing_ellipsis
                if block[-1].text.rstrip().endswith(('...','…')) or trailing_ellipsis(obs,block[-1]):result.append(block)
        return result

    def origin_matches(self,source):
        origin=getattr(self,'native_origin',{})
        return bool(origin.get('video_id') and origin.get('source_id')==source.get('source_id') and origin['video_id']==source.get('video_id')
            and origin.get('title')==source['title'] and 0<=time.monotonic()-origin.get('at',0)<=300)

    def confirmation(self,obs,source,binding):
        if obs.foreground.split('/')[0]!=binding.app:raise Blocked('MEDIA_NATIVE_CONFIRMATION_WRONG_APP')
        controls=[c for c in obs.controls if c.package==binding.app and not c.covered]
        if binding.app==WX:
            headers=[c for c in controls if plain(c.text)=='发送给']
            if len(headers)!=1 or 'HalfScreenTransparentActivity' not in obs.foreground:raise Blocked('MEDIA_WECHAT_OFFICIAL_CONFIRMATION_UNVERIFIED')
            controls=[c for c in controls if c.bounds[1]>=headers[0].bounds[1]]
        texts=[c.text or c.description for c in controls]
        blocks=self.title_blocks(obs,source['title'],controls,allow_truncated=self.origin_matches(source)) if binding.app==WX else []
        title_ok=len(blocks)==1 if binding.app==WX else any(plain(source['title']) in plain(t) for t in texts)
        if not title_ok:raise Blocked('MEDIA_NATIVE_CARD_TITLE_CHANGED')
        if blocks and ''.join(plain(c.text) for c in blocks[0])!=plain(source['title']):
            self.loop.store.event('media_native_preview_title_truncated',{'observation_id':obs.observation_id,'source_png_sha256':obs.png_sha256,
                'verified_video_id':self.native_origin['video_id'],'origin_observation_id':self.native_origin['observation_id'],'full_title_claimed_visible':False})
        name=re.sub(r'[（(]\d+[）)]$','',binding.route_hint)
        if not any(plain(name)==plain(re.sub(r'[（(]\d+人?[）)]$','',t)) or plain(t)==plain('发送给'+name) for t in texts):raise Blocked('MEDIA_NATIVE_RECIPIENT_CHANGED')
        if binding.app==QQ:
            if 'QPublicTransFragmentActivity' not in obs.foreground or not any(c.text=='发送给' for c in controls):raise Blocked('MEDIA_QQ_OFFICIAL_CONFIRMATION_UNVERIFIED')
            edits=[c for c in controls if c.resource==QQ+':id/btb']
            if len(edits)!=1 or edits[0].text not in {'','给好友留言'}:raise Blocked('MEDIA_NATIVE_EXTRA_CAPTION_NOT_EMPTY')
            sends=[c for c in controls if c.resource==QQ+':id/send_btn' and c.text=='发送']
        else:
            edits=[c for c in controls if c.is_editor]
            placeholders=[c for c in controls if c.text=='发消息']
            if edits and (len(edits)!=1 or edits[0].text not in {'','发消息'}) or not edits and len(placeholders)!=1:raise Blocked('MEDIA_NATIVE_EXTRA_CAPTION_NOT_EMPTY')
            sends=[c for c in controls if c.text=='发送']
        if len(sends)!=1:raise Blocked('MEDIA_NATIVE_SEND_CONTROL_UNAVAILABLE')
        return sends[0]

    def qq_card_evidence(self,before,after,source):
        def cards(obs):
            if obs is None:return set()
            avatars=[c for c in obs.controls if c.package==QQ and c.description.endswith('资料卡') and not c.covered]
            found=set()
            for c in obs.controls:
                if c.package!=QQ or c.covered or plain(c.text)!=plain(source['title']):continue
                preceding=[a for a in avatars if a.bounds[1]<=c.bounds[1]+25]
                if not preceding:continue
                a=max(preceding,key=lambda x:x.bounds[1])
                if c.bounds[1]-a.bounds[1]<obs.height*.3 and a.bounds[0]>obs.width*.6:found.add(a.bounds)
            return found
        if len(cards(after))>len(cards(before)):
            return {'observation_id':after.observation_id,'png_sha256':after.png_sha256,'xml_sha256':after.xml_sha256,'new_own_official_card_title':True,
                'expected_video_id':source['video_id'],'payload_origin':'fresh_Bilibili_native_BVID_and_official_QQ_SDK_mini_program_preview','server_delivery_or_read_claimed':False}
        return None

    @staticmethod
    def avatar(observation,bounds):
        if hashlib.sha256(Path(observation.png_path).read_bytes()).hexdigest()!=observation.png_sha256:raise Blocked('MEDIA_AVATAR_IMAGE_CHANGED')
        with Image.open(observation.png_path) as image:crop=image.convert('RGB').crop(bounds)
        w,h=crop.size
        if min(w,h)<30 or not .85<w/h<1.15:raise Blocked('MEDIA_AVATAR_REGION_UNVERIFIED')
        return crop.crop((w*.18,h*.18,w*.82,h*.82)).resize((32,32))

    @classmethod
    def avatar_matches(cls,left,left_bounds,right,right_bounds):
        a=cls.avatar(left,left_bounds);b=cls.avatar(right,right_bounds)
        if min(ImageStat.Stat(a).stddev)<12:return False
        return max(ImageStat.Stat(ImageChops.difference(a,b)).mean)<8

    async def avatar_region(self,obs,label,*,owner=False,sender=False):
        from .native_pixels import avatar_bounds
        detected=avatar_bounds(obs,label,owner=owner,sender=sender)
        if detected:
            self.loop.store.event('media_avatar_pixels_located',{'observation_id':obs.observation_id,'bounds':detected,'source_png_sha256':obs.png_sha256,'method':'connected_actual_avatar_pixels_at_verified_label','owner':owner,'sender':sender})
            return detected
        def validate(value):
            box=value.get('bounds') if isinstance(value,dict) else None
            if (not isinstance(box,list) or len(box)!=4 or any(type(n)is not int for n in box)
                    or not 0<=box[0]<box[2]<=1000 or not 0<=box[1]<box[3]<=1000
                    or type(value.get('confidence')) not in (int,float) or value['confidence']<.95):raise ValueError('AVATAR_REGION_UNAVAILABLE')
        value=await self.loop.guarded(self.loop.brain.structured(
            ('只定位微信“我”页中给定微信号所在个人资料头部左侧的本人方形头像。' if owner else '只定位给定视频卡片右侧、与卡片顶部同高的发送者头像。不是卡片封面，不是左边他人的头像。' if sender else '只定位真实截图中给定群名所在行左侧的方形群头像。')+
            '边界紧贴实际头像，不包括文字、时间或其他会话。只读识别，不执行操作。返回JSON {"bounds":[x1,y1,x2,y2],"confidence":0到1}；坐标归一化到0..1000，不可猜测。',
            {'exact_group_label':label.text,'label_pixel_bounds':list(label.bounds),'image_size':[obs.width,obs.height]},
            [{'observation_id':obs.observation_id,'png_path':obs.png_path,'png_sha256':obs.png_sha256}],validate))
        box=[int(value['bounds'][0]*obs.width/1000),int(value['bounds'][1]*obs.height/1000),int(value['bounds'][2]*obs.width/1000),int(value['bounds'][3]*obs.height/1000)]
        self.loop.store.event('media_avatar_region_proposed',{'observation_id':obs.observation_id,'bounds':box,'source_png_sha256':obs.png_sha256,'owner':owner,'sender':sender})
        if sender:
            if box[0]<obs.width*.8 or abs((box[1]+box[3]-label.bounds[1]-label.bounds[3])/2)>obs.height*.3:raise Blocked('MEDIA_AVATAR_NOT_AT_CARD_SENDER_POSITION')
        elif box[2]>label.bounds[0]+10 or abs((box[1]+box[3]-label.bounds[1]-label.bounds[3])/2)>box[3]-box[1]:raise Blocked('MEDIA_AVATAR_NOT_IN_NAMED_GROUP_ROW')
        self.avatar(obs,box)
        return box

    async def ensure_wechat_fullscreen(self,ui,obs):
        return await self.ensure_app_fullscreen(ui,obs,WX)

    async def ensure_app_fullscreen(self,ui,obs,app):
        if app not in {WX,BILIBILI}:raise Blocked('MEDIA_FULLSCREEN_APP_UNSUPPORTED')
        if obs.foreground.split('/')[0]!=app:return obs
        phone=self.loop.phone
        def full(frame_obs):
            blocks=re.split(r'(?=\bWindow #\d+ Window\{)',phone.last_windows)
            focused=next((b for b in blocks if phone.focus_window and re.search(r'Window\{'+re.escape(phone.focus_window)+r'\s',b.split('\n')[0])),None)
            frame=re.search(r'\bframe=\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]',focused or '')
            if not frame:raise Blocked('MEDIA_WECHAT_WINDOW_BOUNDS_UNAVAILABLE')
            x,y,r,b=map(int,frame.groups())
            return x<=20 and y<=frame_obs.height*.1 and r>=frame_obs.width-20 and b>=frame_obs.height*.9
        if full(obs):return obs
        def menu_present(frame):return {'全屏','分屏','最小化','关闭'}<={c.text for c in frame.controls}
        async def read_menu(frame):
            python=getattr(getattr(self.loop,'config',None),'ocr_python',None)
            if not menu_present(frame) and python:
                from .visual import OcrClient
                reader=OcrClient(python)
                try:await self.loop.guarded(reader.read(frame));self.loop.observed(frame)
                finally:await reader.close()
            return frame
        obs=await read_menu(obs)
        if not menu_present(obs):
            obs=await ui.visual_tap(obs,'floating_window_menu')
            obs=await read_menu(obs)
        menu=AndroidSocialUI(self.loop,obs.foreground.split('/')[0])
        if not menu_present(obs):raise Blocked('MEDIA_FLOATING_SYSTEM_MENU_UNVERIFIED')
        obs=await menu.visual_tap(obs,'floating_fullscreen')
        if obs.foreground.split('/')[0]!=app or not full(obs):raise Blocked('MEDIA_APP_FULLSCREEN_NOT_RESTORED')
        return obs

    async def restore_wechat(self,ui,obs):
        if obs.foreground.split('/')[0]==BILIBILI:
            obs=await self.ensure_app_fullscreen(AndroidSocialUI(self.loop,BILIBILI),obs,BILIBILI)
            obs=await ui.act({'type':'open_app','package_name':WX},obs)
        return await self.ensure_wechat_fullscreen(ui,obs)

    async def cancel_wechat_picker(self,ui,obs):
        # ColorOS may keep this SDK floating window above LauncherUI. BACK
        # reaches the underlying Activity, so close the observed window itself.
        if obs.foreground.split('/')[0]!=WX:return obs
        texts={plain(c.text) for c in obs.controls if not c.covered}
        if 'SendAppMessageWrapperUI' in obs.foreground and {'已发送','留在微信'}<=texts:
            return await ui.tap(obs,text='留在微信')
        for _ in range(2):
            texts={plain(c.text) for c in obs.controls if not c.covered}
            if '选择聊天' not in texts:return obs
            if '发送给' in texts:obs=await ui.tap(obs,text='取消')
            else:obs=await ui.visual_tap(obs,'official_share_close')
        if any(plain(c.text)=='选择聊天' for c in obs.controls):raise Blocked('MEDIA_WECHAT_STALE_PICKER_NOT_CLOSED')
        return obs

    async def prepare_wechat(self,source,binding_id):
        ui=NativeIdentityUI(self,WX);obs=await ui.observe()
        if obs.foreground=='com.oplus.multiapp/com.oplus.multiapp.chooser.MultiAppResolverActivity':obs=await ui.act({'type':'back'},obs)
        obs=await ui.act({'type':'open_app','package_name':WX})
        obs=await self.cancel_wechat_picker(ui,obs)
        obs=await self.restore_wechat(ui,obs)
        for _ in range(3):
            if not any(name in obs.foreground for name in ('SelectConversationUI','HalfScreenTransparentActivity')):break
            obs=await ui.act({'type':'back'},obs)
        binding,context,chat=await ui.resolve(binding_id=binding_id)
        MediaPolicy(self.loop.config).check(binding,'group_video_share')
        recent=ui.recent_hint_observation;label=ui.recent_hint_control;avatar=await self.avatar_region(recent,label)
        created=time.monotonic();obs=await self.open_wechat_picker(source)
        target_label=self.wechat_group_row(obs,label)
        target_avatar=await self.avatar_region(obs,target_label)
        if time.monotonic()-created>180 or not self.avatar_matches(recent,avatar,obs,target_avatar):raise Blocked('MEDIA_NATIVE_GROUP_AVATAR_MISMATCH')
        from .visual import add_control
        target=add_control(obs,label.text,target_avatar,1,'vision','official_share_recipient_avatar')
        self.loop.store.event('media_native_group_identity_matched',{'app':WX,'group_identity_sha256':digest('|'.join(binding.identity)),
            'witness_observation_id':recent.observation_id,'picker_observation_id':obs.observation_id,'method':'fresh_account_roster_fingerprint_membership_name_and_actual_avatar_pixels','native_card_not_sent':True})
        modal=await ui.act({'type':'tap','control_id':target.control_id},obs)
        self.ui=ui;self.binding=binding;self.source=source;self.before_chat=chat;self.context=context;self.witness={'created':created,'observation':recent};self.owner_avatar=ui.owner_avatar
        return replace(binding,verified_at=time.time(),observations=(*binding.observations,recent.observation_id,obs.observation_id,modal.observation_id)),modal

    async def wechat_card_evidence(self,obs,source):
        def titles(frame):
            return [block[0] for block in self.title_blocks(frame,source['title'],allow_truncated=self.origin_matches(source))
                if (frame.visual_controls.get(block[0].control_id,{}).get('bubble_hint') or {}).get('kind')!='outgoing_green']
        candidates=titles(obs)
        if len(candidates)!=1:return None
        old=0
        if self.before_chat:
            for c in titles(self.before_chat):
                a=await self.avatar_region(self.before_chat,c,sender=True)
                if self.avatar_matches(self.owner_avatar[0],self.owner_avatar[1],self.before_chat,a):old+=1
        avatar=await self.avatar_region(obs,candidates[0],sender=True)
        if old or not self.avatar_matches(self.owner_avatar[0],self.owner_avatar[1],obs,avatar):return None
        return {'observation_id':obs.observation_id,'png_sha256':obs.png_sha256,'expected_video_id':source['video_id'],
            'new_own_official_card_title':True,'owner_avatar_matched_to_verified_account':True,'sender_avatar_bounds':avatar,
            'payload_origin':'fresh_Bilibili_native_BVID_and_official_WeChat_SDK_preview','server_delivery_or_read_claimed':False}

    async def open_wechat_picker(self,source):
        from .bilibili import BilibiliUI
        bili,obs=await self.open_source_video(source)
        obs=await bili.select(obs,resource='frame_share');obs=await bili.select(obs,text='微信')
        if obs.foreground=='com.oplus.multiapp/com.oplus.multiapp.chooser.MultiAppResolverActivity':
            labels=[c for c in obs.controls if c.package=='com.oplus.multiapp' and c.resource=='com.oplus.multiapp:id/info_lable' and c.text=='微信' and not c.covered]
            if len(labels)!=1:raise Blocked('MEDIA_PRIMARY_WECHAT_CHOOSER_UNVERIFIED')
            from .visual import add_control
            target=add_control(obs,'微信',list(labels[0].bounds),1,'vision','select_primary_wechat')
            obs=await AndroidSocialUI(self.loop,'com.oplus.multiapp').act({'type':'tap','control_id':target.control_id},obs)
        if obs.foreground.split('/')[0]!=WX:raise Blocked('MEDIA_WECHAT_OFFICIAL_PICKER_NOT_OPEN')
        return obs

    async def open_source_video(self,source):
        from .bilibili import BilibiliUI
        bili=BilibiliUI(self.loop);obs=await bili.act({'type':'open_app','package_name':'tv.danmaku.bili'})
        # An unfinished SDK Activity can be on top of the source App's task.
        # Cancel that selector, then read the actual video instead of launching
        # MAIN again (which would restore the same unfinished SDK Activity).
        for _ in range(3):
            if 'QPublicTransFragmentActivity' in obs.foreground:
                obs=await AndroidSocialUI(self.loop,QQ).act({'type':'back'},obs)
            elif obs.foreground.split('/')[0]==WX and 'SelectConversationUI' in obs.foreground:
                obs=await AndroidSocialUI(self.loop,WX).act({'type':'back'},obs)
            else:break
        if 'UnitedBizDetailsActivity' in obs.foreground:
            try:
                identity,obs=await bili.ensure_identity(obs)
                if identity['video_id']==source['video_id'] and identity['title']==source['title']:
                    self.remember_origin(source,identity,obs);return bili,obs
            except Blocked as error:
                if str(error) not in {'BILI_VISIBLE_VIDEO_ID_UNAVAILABLE','BILI_UI_TARGET_MISSING_OR_AMBIGUOUS'}:raise
        identity,obs=await bili.open_watched_video(source);self.remember_origin(source,identity,obs)
        return bili,obs

    def remember_origin(self,source,identity,obs):
        if identity['video_id']!=source['video_id'] or identity['title']!=source['title']:raise Blocked('MEDIA_NATIVE_SOURCE_CHANGED')
        self.native_origin={'source_id':source['source_id'],'video_id':identity['video_id'],'title':identity['title'],'at':time.monotonic(),
            'observation_id':obs.observation_id,'png_sha256':obs.png_sha256,'xml_sha256':obs.xml_sha256}
        self.loop.store.event('media_native_source_observed',{k:v for k,v in self.native_origin.items() if k!='at'})

    async def prepare_qq(self,source,binding_id):
        ui=AndroidSocialUI(self.loop,QQ)
        obs=await ui.act({'type':'open_app','package_name':QQ})
        for _ in range(3):
            if 'QPublicTransFragmentActivity' not in obs.foreground:break
            obs=await ui.act({'type':'back'},obs)
        binding,context,chat=await ui.resolve(binding_id=binding_id)
        MediaPolicy(self.loop.config).check(binding,'group_video_share')
        info=await ui.tap(chat,description='聊天设置')
        if ui.one(info,resource=QQ+':id/rpd').text!=binding.peer:raise Blocked('MEDIA_QQ_GROUP_CHANGED')
        icon=ui.one(info,resource=QQ+':id/rng')
        member_label=ui.one(info,resource=QQ+':id/zlj',text='群成员');cy=(member_label.bounds[1]+member_label.bounds[3])/2
        counts=[c.text for c in info.controls if c.resource==QQ+':id/zls' and re.fullmatch(r'\d+人',c.text) and not c.covered
            and abs((c.bounds[1]+c.bounds[3])/2-cy)<(member_label.bounds[3]-member_label.bounds[1])*.6]
        if len(counts)!=1:raise Blocked('MEDIA_GROUP_MEMBER_COUNT_UNAVAILABLE')
        count=counts[0]
        witness={'observation':info,'bounds':icon.bounds,'label':binding.route_hint+'，'+count,'created':time.monotonic()}
        await ui.act({'type':'back'},info)
        bili,obs=await self.open_source_video(source)
        obs=await bili.select(obs,resource='frame_share');obs=await bili.select(obs,text='QQ')
        if 'QPublicTransFragmentActivity' not in obs.foreground:raise Blocked('MEDIA_QQ_OFFICIAL_PICKER_NOT_OPEN')
        rows=[c for c in obs.controls if c.package==QQ and c.description.removesuffix('，当前聊天')==witness['label'] and not c.covered]
        if len(rows)!=1:raise Blocked('MEDIA_QQ_NATIVE_GROUP_ROW_AMBIGUOUS')
        row=rows[0]
        icons={c.bounds for c in obs.controls if c.package==QQ and c.kind=='android.widget.ImageView'
            and row.bounds[1]<=c.bounds[1] and c.bounds[3]<=row.bounds[3] and c.bounds[2]<obs.width*.3}
        if len(icons)!=1:raise Blocked('MEDIA_NATIVE_GROUP_AVATAR_MISMATCH')
        picker_icon=icons.pop()
        if time.monotonic()-witness['created']>180 or not self.avatar_matches(info,icon.bounds,obs,picker_icon):raise Blocked('MEDIA_NATIVE_GROUP_AVATAR_MISMATCH')
        self.loop.store.event('media_native_group_identity_matched',{'app':QQ,'group_identity_sha256':__import__('hashlib').sha256('|'.join(binding.identity).encode()).hexdigest(),
            'witness_observation_id':info.observation_id,'picker_observation_id':obs.observation_id,'method':'fresh_group_number_membership_name_member_count_and_actual_avatar_pixels','native_card_not_sent':True})
        from .visual import add_control
        target=add_control(obs,witness['label'],list(picker_icon),1,'vision','official_share_recipient_avatar')
        modal=await ui.act({'type':'tap','control_id':target.control_id},obs)
        self.witness=witness;self.binding=binding;self.source=source;self.ui=ui;self.before_chat=chat;self.context=context
        return replace(binding,verified_at=time.time(),observations=(*binding.observations,info.observation_id,obs.observation_id,modal.observation_id)),modal

    async def search_qq_group(self,peer=None):
        if peer is None:
            policy=MediaPolicy(self.loop.config).read()
            keys=policy.get('shares',{}).get('allowed_groups',[])
            peers=[k.split('/',1)[1] for k in keys if isinstance(k,str) and k.startswith(QQ+'/')]
            if policy.get('enabled') is not True or len(peers)!=1:raise Blocked('MEDIA_SINGLE_APPROVED_QQ_GROUP_REQUIRED')
            peer=peers[0]
        if not re.fullmatch(r'[0-9]{5,12}',peer):raise Blocked('MEDIA_QQ_GROUP_NUMBER_REQUIRED')
        ui=AndroidSocialUI(self.loop,QQ);obs=await ui.observe()
        if 'QPublicTransFragmentActivity' not in obs.foreground:raise Blocked('MEDIA_QQ_SHARE_PICKER_NOT_OPEN')
        fields=[c for c in obs.controls if c.package==QQ and c.is_editor and c.focused and '搜索框' in c.description and not c.text]
        if len(fields)!=1:raise Blocked('MEDIA_QQ_PICKER_SEARCH_NOT_FOCUSED_OR_EMPTY')
        controller=InputBridge(self.loop)
        async with controller.temporary():
            obs=await ui.observe()
            after=await controller.insert(obs,'target-search:'+uuid.uuid4().hex,peer,operation_kind='media_target_search_text')
            await controller.observe_editor(after)
        self.loop.store.event('media_picker_group_number_queried',{'observation_id':after.observation_id,
            'read_only_search':True,'recipient_identity_verified':False,'outgoing_sends':0})
        return after


class NativeIdentityUI(AndroidSocialUI):
    def __init__(self,controller,app):super().__init__(controller.loop,app);self.controller=controller
    async def self_account(self):
        account=await super().self_account()
        if self.app==WX:
            profile=await self.observe();labels=[c for c in profile.controls if re.match(r'^微信号\s*[:：]',c.text)]
            if len(labels)!=1:raise Blocked('MEDIA_WECHAT_OWN_AVATAR_ANCHOR_UNAVAILABLE')
            bounds=await self.controller.avatar_region(profile,labels[0],owner=True)
            self.owner_avatar=(profile,bounds)
        return account
