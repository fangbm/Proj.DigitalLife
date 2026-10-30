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

    async def api(self,action,payload=None,settings=None,*,max_bytes=1_000_000,timeout=45):
        value=settings or self.settings()
        result=await json_request(value['endpoint'],'/'+action,payload or {},token=value['access_token'],timeout=timeout,max_bytes=max_bytes)
        if not isinstance(result,dict) or result.get('status')!='ok' or result.get('retcode')!=0:
            raise Blocked('QQ_VOICE_BRIDGE_REFUSED')
        return result.get('data')

    async def status(self):
        login=await self.api('get_login_info')
        expected=self.policy.read().get('accounts',{}).get('com.tencent.mobileqq')
        if not isinstance(login,dict) or str(login.get('user_id'))!=str(expected):raise Blocked('QQ_VOICE_LOGIN_ACCOUNT_MISMATCH')
        return login

    async def binding(self,target,target_kind='dm'):
        login=await self.status();account=str(login['user_id'])
        target=account if target=='self' and target_kind=='dm' else str(target)
        if target_kind not in {'dm','group'} or not re.fullmatch(r'[1-9][0-9]{4,19}',target):raise Blocked('QQ_VOICE_TARGET_INVALID')
        if target!=account or target_kind!='dm':
            rows=await self.api('get_friend_list' if target_kind=='dm' else 'get_group_list')
            field='user_id' if target_kind=='dm' else 'group_id'
            if not isinstance(rows,list) or not any(str(r.get(field))==target for r in rows if isinstance(r,dict)):
                raise Blocked('QQ_VOICE_EXISTING_TARGET_REQUIRED')
        return Binding('com.tencent.mobileqq',account,target,target_kind,True,
            ('napcat:current-login-and-existing-target',),time.time(),
            source_type='v2_autonomous' if self.store and self.store.state.get('run_kind')=='autonomous' else 'live_scripted')

    async def send(self,speech_ref,*,target='self',target_kind='dm',intent,kind='proactive_dm',sources=(),motive='发送已合成的原生语音'):
        asset=self.speech.get(speech_ref)
        if asset.get('kind')!='tts':raise Blocked('QQ_VOICE_TTS_ASSET_REQUIRED')
        if target_kind not in {'dm','group'} or not isinstance(intent,str) or not 1<=len(intent)<=160:
            raise Blocked('QQ_VOICE_TARGET_OR_INTENT_INVALID')
        settings=self.settings();binding=await self.binding(target,target_kind)
        account,target=binding.account,binding.peer
        if target_kind=='group':kind='group_post'
        bid=self.social.bind(binding)
        oid=self.social.prepare(self.policy,binding,kind,intent,asset['text'],motive,
            [speech_ref,'native:qq-record',*sources])
        try:
            self.social.transition(oid,'prepared','voice_ready',{'speech_ref':speech_ref,'audio_sha256':asset['sha256']})
            # Re-read policy and source immediately before the sole HTTP send.
            if self.settings()!=settings:raise Blocked('QQ_VOICE_CONFIGURATION_CHANGED')
            asset=self.speech.get(speech_ref)
            self.social.before_send(self.policy,binding,oid)
            raw=Path(asset['path']).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=asset['sha256']:raise Blocked('SPEECH_ASSET_CHANGED')
            audio=base64.b64encode(raw).decode('ascii')
            payload={'message':[{'type':'record','data':{'file':'base64://'+audio}}],'timeout':45000,
                'user_id' if target_kind=='dm' else 'group_id':int(target)}
            if self.store.state['dispatched']>=self.config.max_actions:raise Blocked('VOICE_ACTION_BUDGET_EXHAUSTED')
            self.social.transition(oid,'voice_ready','dispatch_attempted',{'speech_ref':speech_ref,'transport':'napcat_record'})
            self.store.update(dispatched=self.store.state['dispatched']+1)
            result=await self.api('send_private_msg' if target_kind=='dm' else 'send_group_msg',payload,settings,timeout=60)
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
            if valid:self.social.record_exchange(bid,kind,oid,'own_outgoing_native_record_observed_not_read_receipt')
            return {'outbound_id':oid,'outcome':state,**evidence}
        except BaseException:
            row=self.store.db.execute('SELECT status FROM social_outbox WHERE id=?',(oid,)).fetchone()
            if row and row[0]=='dispatch_attempted':self.social.transition(oid,row[0],'delivery_unknown',{'transport':'napcat_record','reason':'send_or_receipt_uncertain_no_retry'})
            elif row and row[0] in {'prepared','voice_ready'}:self.social.transition(oid,row[0],'cancelled_before_send',{'reason':'cancelled_before_HTTP_send'})
            raise

    async def reconcile(self,outbound_id,message_id):
        """Operator inspection of a late record. This method cannot send."""
        if not re.fullmatch('[a-f0-9]{32}',outbound_id) or type(message_id) is not int:raise Blocked('QQ_VOICE_RECONCILIATION_ARGUMENTS_INVALID')
        row=self.store.db.execute('SELECT o.status,o.attempted,o.sources,b.account,b.peer,b.kind,o.binding_id,o.kind,o.trigger_id FROM social_outbox o JOIN social_bindings b ON b.id=o.binding_id WHERE o.id=? AND o.device=?',
            (outbound_id,self.config.serial)).fetchone()
        if not row or row[0]!='delivery_unknown' or row[1] is None or 'native:qq-record' not in json.loads(row[2]):raise Blocked('QQ_VOICE_NOT_UNKNOWN_NATIVE_SEND')
        login=await self.status()
        if str(login['user_id'])!=row[3]:raise Blocked('QQ_VOICE_LOGIN_ACCOUNT_MISMATCH')
        group=row[5]=='group';payload={'group_id' if group else 'user_id':int(row[4]),'message_seq':0,'count':20}
        history=await self.api('get_group_msg_history' if group else 'get_friend_msg_history',payload)
        candidates=[m for m in history.get('messages',[]) if isinstance(m,dict)
            and str(m.get('sender',{}).get('user_id'))==row[3]
            and type(m.get('time')) in (int,float) and row[1]-1<=m['time']<=row[1]+120
            and (not group or str(m.get('group_id'))==row[4])
            and any(p.get('type')=='record' for p in m.get('message',[]) if isinstance(p,dict))]
        if len(candidates)!=1 or candidates[0].get('message_id')!=message_id:raise Blocked('QQ_VOICE_LATE_RECORD_AMBIGUOUS_OR_UNMATCHED')
        actual=await self.api('get_msg',{'message_id':message_id})
        if (not isinstance(actual,dict) or actual.get('message_id')!=message_id
                or str(actual.get('sender',{}).get('user_id'))!=row[3]
                or (str(actual.get('group_id'))!=row[4] if group else actual.get('message_type')!='private')
                or not any(p.get('type')=='record' for p in actual.get('message',[]) if isinstance(p,dict))):
            raise Blocked('QQ_VOICE_LATE_RECORD_UNVERIFIED')
        evidence={'message_id':message_id,'transport':'napcat_record','client_record_observed':True,
            'reconciled_read_only':True,'recipient_history_verified':True,'late_confirmation_seconds':round(candidates[0]['time']-row[1],3),
            'speaker_used':False,'microphone_used':False,'read_receipt':False,'audio_content_independently_verified':False}
        self.social.reconcile_native_record(outbound_id,evidence)
        self.social.record_exchange(row[6],row[7],outbound_id,'own_outgoing_native_record_observed_not_read_receipt')
        return {'outcome':'sent_observed','outbound_id':outbound_id,'trigger_id':row[8],'additional_messages_sent':0,**evidence}
