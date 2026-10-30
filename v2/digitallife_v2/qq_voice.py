"""Silent QQ record messages through a local NapCat bridge, one send attempt."""
import base64
import hashlib
import json
from pathlib import Path
import re
import time
from .local_http import endpoint,json_request
from .safety import Blocked
from .social import Binding,SocialPolicy,SocialStore


class QQVoiceSender:
    def __init__(self,config,store,speech):
        self.config,self.store,self.speech=config,store,speech
        self.policy=SocialPolicy(config);self.social=SocialStore(store) if store else None

    def settings(self):
        try:
            path=self.config.data/'voice.private.json'
            if path.stat().st_size>16384:raise ValueError()
            value=json.loads(path.read_text(encoding='utf-8'))
            if (value.get('version')!=1 or value.get('serial')!=self.config.serial
                    or value.get('enabled') is not True or value.get('provider')!='napcat'):
                raise ValueError()
            endpoint(value['endpoint'])
            if not isinstance(value['access_token'],str) or not 16<=len(value['access_token'])<=512:raise ValueError()
            return value
        except (ValueError,KeyError,TypeError,OSError):raise Blocked('QQ_VOICE_NOT_CONFIGURED') from None

    async def api(self,action,payload=None,settings=None):
        value=settings or self.settings()
        result=await json_request(value['endpoint'],'/'+action,payload or {},token=value['access_token'],timeout=45)
        if not isinstance(result,dict) or result.get('status')!='ok' or result.get('retcode')!=0:
            raise Blocked('QQ_VOICE_BRIDGE_REFUSED')
        return result.get('data')

    async def status(self):
        login=await self.api('get_login_info')
        expected=self.policy.read().get('accounts',{}).get('com.tencent.mobileqq')
        if not isinstance(login,dict) or str(login.get('user_id'))!=str(expected):raise Blocked('QQ_VOICE_LOGIN_ACCOUNT_MISMATCH')
        return login

    async def send(self,speech_ref,*,target='self',target_kind='dm',intent,kind='proactive_dm'):
        asset=self.speech.get(speech_ref)
        if asset.get('kind')!='tts':raise Blocked('QQ_VOICE_TTS_ASSET_REQUIRED')
        if target_kind not in {'dm','group'} or not isinstance(intent,str) or not 1<=len(intent)<=160:
            raise Blocked('QQ_VOICE_TARGET_OR_INTENT_INVALID')
        settings=self.settings();login=await self.status();account=str(login['user_id'])
        target=account if target=='self' and target_kind=='dm' else str(target)
        if not re.fullmatch(r'[1-9][0-9]{4,19}',target):raise Blocked('QQ_VOICE_TARGET_INVALID')
        if target!=account or target_kind!='dm':
            rows=await self.api('get_friend_list' if target_kind=='dm' else 'get_group_list',settings=settings)
            field='user_id' if target_kind=='dm' else 'group_id'
            if not isinstance(rows,list) or not any(str(r.get(field))==target for r in rows if isinstance(r,dict)):
                raise Blocked('QQ_VOICE_EXISTING_TARGET_REQUIRED')
        binding=Binding('com.tencent.mobileqq',account,target,target_kind,True,
            ('napcat:current-login-and-existing-target',),time.time(),source_type='live_scripted')
        if target_kind=='group':kind='group_post'
        oid=self.social.prepare(self.policy,binding,kind,intent,asset['text'],'发送已合成的原生语音',
            [speech_ref,'native:qq-record'])
        try:
            self.social.transition(oid,'prepared','voice_ready',{'speech_ref':speech_ref,'audio_sha256':asset['sha256']})
            # Re-read policy and source immediately before the sole HTTP send.
            if self.settings()!=settings:raise Blocked('QQ_VOICE_CONFIGURATION_CHANGED')
            asset=self.speech.get(speech_ref)
            self.social.before_send(self.policy,binding,oid)
            raw=Path(asset['path']).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=asset['sha256']:raise Blocked('SPEECH_ASSET_CHANGED')
            audio=base64.b64encode(raw).decode('ascii')
            payload={'message':[{'type':'record','data':{'file':'base64://'+audio}}],
                'user_id' if target_kind=='dm' else 'group_id':int(target)}
            self.social.transition(oid,'voice_ready','dispatch_attempted',{'speech_ref':speech_ref,'transport':'napcat_record'})
            result=await self.api('send_private_msg' if target_kind=='dm' else 'send_group_msg',payload,settings)
            message_id=result.get('message_id') if isinstance(result,dict) else None
            if type(message_id) is not int:raise Blocked('QQ_VOICE_MESSAGE_ID_MISSING')
            # Acceptance by the client is recorded separately from read receipt.
            observed=await self.api('get_msg',{'message_id':message_id},settings)
            valid=(isinstance(observed,dict) and str(observed.get('sender',{}).get('user_id'))==account
                and observed.get('message_id')==message_id
                and (target_kind=='dm' or str(observed.get('group_id'))==target)
                and any(m.get('type')=='record' for m in observed.get('message',[]) if isinstance(m,dict)))
            evidence={'message_id':message_id,'speech_ref':speech_ref,'audio_sha256':asset['sha256'],
                'transport':'napcat_record','speaker_used':False,'microphone_used':False,'read_receipt':False,
                'client_record_observed':valid,'audio_content_independently_verified':False}
            state='sent_observed' if valid else 'delivery_unknown'
            self.social.transition(oid,'dispatch_attempted',state,evidence)
            return {'outbound_id':oid,'outcome':state,**evidence}
        except BaseException:
            row=self.store.db.execute('SELECT status FROM social_outbox WHERE id=?',(oid,)).fetchone()
            if row and row[0]=='dispatch_attempted':self.social.transition(oid,row[0],'delivery_unknown',{'transport':'napcat_record','reason':'send_or_receipt_uncertain_no_retry'})
            elif row and row[0] in {'prepared','voice_ready'}:self.social.transition(oid,row[0],'cancelled_before_send',{'reason':'cancelled_before_HTTP_send'})
            raise
