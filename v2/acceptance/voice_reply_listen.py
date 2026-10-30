"""Bounded live QQ voice window; ordinary SessionLoop voice path, one reply."""
import argparse
import asyncio
import base64
import hashlib
import json
import time
import unicodedata
import uuid
from digitallife_v2.brain import LifeBrain
from digitallife_v2.config import Config
from digitallife_v2.session import SessionLoop,SessionStopped
from digitallife_v2.qq_voice import QQVoiceSender
from digitallife_v2.speech import inspect_wav,run_worker
from digitallife_v2.store import write_json


class VoiceWindow(SessionLoop):
    def __init__(self,config,directory,minutes=10,opened_after=None):
        super().__init__(config,directory,minutes=minutes,brain_factory=lambda c,s:LifeBrain(c,s))
        self.opened=None
        self.opened_after=opened_after
        self.voice_acceptance_context='用户授权已有联系人全部功能。本轮第一条新QQ好友语音为真人验收，理解实际语音后自然决定是否回复；最多发送一次原生语音，不照抄预设回复。'

    async def step(self):
        if not self.voice_inbox:raise SessionStopped('M6_QQ_VOICE_STREAM_UNAVAILABLE')
        if self.opened is None:
            self.opened=self.opened_after or time.time()
            if self.opened_after:
                # Recover actual messages from the same still-open human test
                # interval, without replaying an outbound or trusting a label.
                api=QQVoiceSender(self.config,self.store,self.speech)
                friends=await self.guarded(api.api('get_friend_list'))
                for friend in friends[:20]:
                    try:response=await self.guarded(asyncio.wait_for(api.api('get_friend_msg_history',{'user_id':friend['user_id'],'count':10}),3))
                    except (ValueError,asyncio.TimeoutError):continue
                    for msg in response.get('messages',[]):
                        if self.opened<=msg.get('time',0)<=time.time():
                            self.voice_inbox.ingest({**msg,'self_id':int(self.voice_inbox.account),
                                'user_id':msg.get('sender',{}).get('user_id'),'message_type':'private','post_type':'message'})
            write_json(self.directory/'WINDOW_READY.json',{'opened_at':self.opened,'deadline':self.store.state['deadline'],
                'real_incoming_voice_required':True,'maximum_outgoing':1,'channel':'qq_friend_voice',
                'pipeline':'production_SessionLoop_step','speaker_used':False,'microphone_used':False})
        self.voice_inbox.poll()
        if self.voice_inbox.task.done() and self.voice_inbox.error:
            raise SessionStopped('M6_QQ_VOICE_STREAM_INTERRUPTED:'+self.voice_inbox.error)
        row=self.store.db.execute("SELECT id FROM voice_inbound WHERE device=? AND channel='dm' AND posted>=? AND status='pending' ORDER BY posted LIMIT 1",(self.config.serial,self.opened)).fetchone()
        if not row:
            self.inbound_wake.clear();await self.guarded(self.wait_for_event(10))
            return {'outcome':'waiting_for_human_QQ_voice'}
        original_next=self.voice_inbox.next
        # Select only the first real post-window friend voice. Processing and
        # model decisions below use the unchanged production path.
        def selected():
            candidate=self.store.db.execute('SELECT id,account,peer,channel,sender,message_id,posted,body FROM voice_inbound WHERE id=?',(row[0],)).fetchone()
            return dict(zip(('event_id','account','peer','channel','sender','message_id','posted_at','body'),candidate))
        self.voice_inbox.next=selected
        try:result=await super().step()
        finally:self.voice_inbox.next=original_next
        evidence={'source':'LIVE_FRIEND_VOICE_REAL_ASR_MODEL_NATIVE_QQ_REPLY','pipeline':'production_SessionLoop_step',
            'result':result,'maximum_outgoing':1,'speaker_used':False,'microphone_used':False,'read_receipt_claimed':False,
            'outgoing_native_full_text_match':False}
        if result.get('outcome')=='sent_observed':
            sender=QQVoiceSender(self.config,self.store,self.speech)
            actual=await self.guarded(sender.api('get_msg',{'message_id':result['message_id']}))
            record=next(m['data'] for m in actual['message'] if m['type']=='record')
            audio=await self.guarded(sender.api('get_record',{'file':record['file'],'out_format':'wav'},max_bytes=9_000_000))
            raw=base64.b64decode(audio['base64'],validate=True)
            path=self.speech.cache/('readback-'+uuid.uuid4().hex+'.wav');path.write_bytes(raw)
            info,_=inspect_wav(path,20)
            self.speech.asset('native_readback',{'path':str(path),**info,'source_ref':'qq-outgoing:'+str(result['message_id'])},600)
            transcript=await self.guarded(self.speech.transcribe({'path':str(path),'sha256':hashlib.sha256(raw).hexdigest(),'source_ref':'qq-outgoing:'+str(result['message_id'])},'zh-CN'))
            expected=self.store.db.execute('SELECT text FROM social_outbox WHERE id=?',(result['outbound_id'],)).fetchone()[0]
            code="import sys,json;from opencc import OpenCC;d=json.loads(sys.stdin.buffer.read().decode('utf8'));c=OpenCC('t2s');sys.stdout.reconfigure(encoding='utf8');json.dump({k:c.convert(v) for k,v in d.items()},sys.stdout,ensure_ascii=False)"
            normalized=await self.guarded(run_worker([str(self.config.data/'tts-env/Scripts/python.exe'),'-c',code],{'expected':expected,'actual':transcript['text']},30))
            normalize=lambda text:''.join(c for c in unicodedata.normalize('NFKC',text).casefold() if c.isalnum())
            evidence.update(outgoing_native_full_text_match=normalize(normalized['expected'])==normalize(normalized['actual']),
                outgoing_expected_text=expected,outgoing_transcript=transcript['text'],outgoing_audio=info)
        write_json(self.directory/'M6_READY.json',evidence)
        raise SessionStopped('M6_QQ_VOICE_REPLY_ACCEPTANCE_DONE' if evidence['outgoing_native_full_text_match'] else 'M6_QQ_VOICE_REPLY_PARTIAL:'+result.get('outcome','unknown'))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--session',required=True);parser.add_argument('--profile',default='find-x6');parser.add_argument('--minutes',type=float,default=10);parser.add_argument('--opened-after',type=float)
    args=parser.parse_args();result=asyncio.run(VoiceWindow(Config.load(args.profile),args.session,args.minutes,args.opened_after).run())
    print(json.dumps(result));return 0 if result['reason']=='M6_QQ_VOICE_REPLY_ACCEPTANCE_DONE' else 2
if __name__=='__main__':raise SystemExit(main())
