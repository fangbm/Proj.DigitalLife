"""Group identity from actual group settings and the current member's profile."""
import re
import time
import hashlib
from pathlib import Path
from .safety import Blocked
from .social import Binding,digest
from .visual import crop_sha,locate

QQ='com.tencent.mobileqq'
WX='com.tencent.mm'


async def verify_qq(ui,chat,account,hint,expected=None):
    if not any(c.resource==QQ+':id/3o2' and re.fullmatch(r'\(\d+\)',c.text) for c in chat.controls):
        raise Blocked('SOCIAL_GROUP_CHAT_ROLE_UNVERIFIED')
    info=await ui.tap(chat,description='聊天设置')
    peer=ui.one(info,resource=QQ+':id/rpd').text
    if not re.fullmatch(r'\d{5,12}',peer) or not any(c.text=='群成员' for c in info.controls):
        raise Blocked('SOCIAL_GROUP_ID_UNAVAILABLE')
    own=getattr(ui,'account_display_name',None)
    if not own:raise Blocked('SOCIAL_OWN_GROUP_MEMBER_UNVERIFIED')
    profile=await ui.tap(info,description=own)
    found=re.fullmatch(r'QQ:\s*(\d{5,12})',ui.one(profile,resource=QQ+':id/gmx').text)
    if not found or found[1]!=account or not any('加入该群' in c.text for c in profile.controls):
        raise Blocked('SOCIAL_OWN_GROUP_MEMBER_UNVERIFIED')
    info=await ui.act({'type':'back'},profile)
    if ui.one(info,resource=QQ+':id/rpd').text!=peer:raise Blocked('SOCIAL_GROUP_CHANGED')
    after=await ui.act({'type':'back'},info)
    if ui.chat_hint(after)!=hint:raise Blocked('SOCIAL_GROUP_CHAT_TITLE_CHANGED')
    source='v2_autonomous' if ui.loop.run_kind=='autonomous' else 'live_scripted' if ui.loop.run_kind=='scripted' else 'fixture'
    binding=Binding(QQ,account,peer,'group',True,tuple(ui.sources),time.time(),source,hint)
    if expected and binding.identity!=expected.identity:raise Blocked('SOCIAL_CONVERSATION_CHANGED')
    ui.group_own_name=own;ui.last_binding=binding
    return binding,after


async def scroll_info(ui,obs,direction):
    area=await locate(ui.loop,obs,'group_info_scroll')
    return await ui.act({'type':'swipe','control_id':area.control_id,'direction':direction},obs)


def wechat_metadata(info):
    labels=[c for c in info.controls if c.text=='群聊名称' and c.bounds[0]<info.width*.4]
    if len(labels)!=1:raise Blocked('WECHAT_GROUP_NAME_ROW_UNAVAILABLE')
    label=labels[0];cy=(label.bounds[1]+label.bounds[3])/2
    values=[c for c in info.controls if c.text and c.bounds[0]>info.width*.5 and abs((c.bounds[1]+c.bounds[3])/2-cy)<45]
    headers=[c for c in info.controls if re.fullmatch(r'聊天信息[（(]\d+[）)]',c.text)]
    if len(values)!=1 or len(headers)!=1:raise Blocked('WECHAT_GROUP_METADATA_AMBIGUOUS')
    roster=[c.text for c in info.controls if c.text and headers[0].bounds[3]<c.bounds[1]<label.bounds[1]]
    region=[0,int(info.height*.11),info.width,(label.bounds[1]//50)*50]
    if region[3]<=region[1]:raise Blocked('WECHAT_GROUP_ROSTER_UNAVAILABLE')
    if hashlib.sha256(Path(info.png_path).read_bytes()).hexdigest()!=info.png_sha256:raise Blocked('WECHAT_GROUP_IMAGE_SOURCE_CHANGED')
    count=re.search(r'\d+',headers[0].text)[0]
    peer='wxgroup:'+digest(values[0].text+'|'+count+'|'+crop_sha(info.png_path,region))
    return peer,roster


async def verify_wechat(ui,info,account,hint,expected=None):
    if not info.foreground.endswith('chatroom.ui.ChatroomInfoUI'):
        raise Blocked('WECHAT_GROUP_INFO_ROLE_UNVERIFIED')
    for _ in range(3):
        labels=[c for c in info.controls if c.text=='群聊名称' and c.bounds[0]<info.width*.4]
        if len(labels)==1:break
        info=await scroll_info(ui,info,'down')
    else:raise Blocked('WECHAT_GROUP_NAME_ROW_UNAVAILABLE')
    # The QR page explicitly says re-entry renews it. Bind the observed name,
    # member-count and roster image instead; this is a UI fingerprint, not a
    # claimed internal WeChat group ID. Duplicate recent entries are rejected.
    peer,roster=wechat_metadata(info)
    if expected and expected.peer!=peer:raise Blocked('WECHAT_GROUP_FINGERPRINT_CHANGED')
    own=None
    for _ in range(4):
        fields=[c for c in info.controls if c.text in {'我在本群的昵称','我在群里的昵称'} and c.bounds[0]<info.width*.5]
        if len(fields)==1:
            field=fields[0];center=(field.bounds[1]+field.bounds[3])/2
            names=[c.text for c in info.controls if c.text and c.bounds[0]>info.width*.55 and abs((c.bounds[1]+c.bounds[3])/2-center)<45]
            if len(names)==1:own=names[0]
            break
        info=await scroll_info(ui,info,'up')
    if not own or own not in roster or not any(c.text=='退出群聊' for c in info.controls):
        raise Blocked('WECHAT_OWN_GROUP_NICKNAME_UNVERIFIED')
    after=await ui.act({'type':'back'},info)
    normalize=lambda s:re.sub(r'[（(]\d+[）)]$','',s)
    if normalize(ui.chat_hint(after))!=normalize(hint):raise Blocked('SOCIAL_GROUP_CHAT_TITLE_CHANGED')
    source='v2_autonomous' if ui.loop.run_kind=='autonomous' else 'live_scripted' if ui.loop.run_kind=='scripted' else 'fixture'
    binding=Binding(WX,account,peer,'group',True,tuple(ui.sources),time.time(),source,hint)
    if expected and binding.identity!=expected.identity:raise Blocked('SOCIAL_CONVERSATION_CHANGED')
    ui.group_own_name=own;ui.last_binding=binding
    return binding,after
