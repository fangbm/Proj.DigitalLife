"""Normal app share UI navigation; a picker query is never a sent message."""
import re
import uuid
from .input_bridge import InputBridge
from .social_ui import AndroidSocialUI,QQ
from .media import MediaPolicy
from .safety import Blocked


class NativeShareUI:
    def __init__(self,loop):self.loop=loop

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
