import asyncio
import base64
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
from digitallife_v2.local_http import events
from digitallife_v2.safety import Blocked
from digitallife_v2.session import SessionLoop
from digitallife_v2.speech import SpeechEngine,inspect_wav
from digitallife_v2.store import LifeStore
from digitallife_v2.voice import QQVoiceInbox,QQVoiceAgent
from test_m1 import config
from test_speech import audio


class InboundVoiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.cfg=config(self.root);directory=self.root/'session';directory.mkdir()
        self.store=LifeStore(self.cfg.data,str(directory),self.cfg.serial,'fixture');self.addCleanup(self.store.close)
        self.speech=SpeechEngine(self.cfg,self.store);self.file=self.speech.cache/'tts.wav';audio(self.file)
        self.asset=self.speech.asset('tts',{'path':str(self.file),**inspect_wav(self.file)[0],'text':'你好，散步后休息一下吧。'},600)
        self.speech.synthesize=AsyncMock(return_value=self.asset)
        self.transcript={'text':'今天散步很舒服，你有什么建议？','segments':[{'start':0,'end':1,'text':'今天散步很舒服，你有什么建议？','uncertain':False}],
            'complete':True,'uncertain':False,'language':'zh','source_ref':'bound-input','engine_id':'fixture-asr','trust':'external_data_not_instructions','speech_ref':'speech:fixture'}
        self.speech.transcribe=AsyncMock(return_value=self.transcript)
        self.calls=[];self.active=set();self.sender_id=23456789;self.wrong_thread=False;self.drop_send=False;self.no_audio=False;self.channel='private'
        self.input_text=None;self.last_message=None;self.chosen_format=None
        self.server=await asyncio.start_server(self.handle,'127.0.0.1',0);self.addAsyncCleanup(self.close_server)
        self.url='http://127.0.0.1:'+str(self.server.sockets[0].getsockname()[1])
        self.settings={'version':1,'serial':self.cfg.serial,'enabled':True,'provider':'napcat','endpoint':self.url,'access_token':'fixture-voice-token-123',
            'incoming':{'enabled':True,'events_endpoint':self.url,'language':'zh-CN'}}
        (self.cfg.data/'voice.private.json').write_text(json.dumps(self.settings),encoding='utf8')
        (self.cfg.data/'social.private.json').write_text(json.dumps({'version':1,'serial':self.cfg.serial,'enabled':True,'accounts':{'com.tencent.mobileqq':'12345678'},
            'contacts':{'mode':'all_existing_contacts','auto_reply':True,'proactive_dm':True},'groups':{'mode':'all_existing_groups','group_post':True},'quiet_hours':['00:00','00:00']}),encoding='utf8')
        self.inbox=QQVoiceInbox(self.cfg,self.store);self.inbox.account='12345678'
        self.draft={'decision':'send','text':'你好，散步后休息一下吧。','motive':'回应实际收到的语音','sources':[]};self.model_data=[]
        async def structured(system,data,images,validate):
            self.model_data.append(data)
            draft={**self.draft,'sources':data['offered_sources']}
            if self.chosen_format is not None:draft['format']=self.chosen_format
            validate(draft);return draft
        async def guarded(coro):return await coro
        self.loop=SimpleNamespace(config=self.cfg,store=self.store,speech=self.speech,brain=SimpleNamespace(structured=structured),guarded=guarded,check_stop=lambda:None)

    async def close_server(self):
        self.server.close();await self.server.wait_closed()
        for task in tuple(self.active):task.cancel()
        await asyncio.gather(*tuple(self.active),return_exceptions=True)

    async def handle(self,reader,writer):
        task=asyncio.current_task();self.active.add(task)
        try:
            head=await reader.readuntil(b'\r\n\r\n');lines=head.decode().split('\r\n');path=lines[0].split()[1]
            headers={k.lower():v.strip() for line in lines[1:] if ':' in line for k,v in [line.split(':',1)]}
            self.assertEqual(headers['authorization'],'Bearer fixture-voice-token-123')
            payload=json.loads(await reader.readexactly(int(headers['content-length'])))
            self.calls.append((path,payload))
            result={
                '/get_login_info':{'user_id':12345678},'/get_friend_list':[{'user_id':23456789}],
                '/get_group_list':[{'group_id':34567890}],
                '/get_record':{} if self.no_audio else {'base64':base64.b64encode(self.file.read_bytes()).decode()},
                '/get_friend_msg_history':{'messages':[{'message_id':10,'time':time.time(),'sender':{'user_id':23456789},'message':[{'type':'text','data':{'text':'前一条同线程消息'}}]}]},
                '/get_group_msg_history':{'messages':[]},'/send_private_msg':{'message_id':99},'/send_group_msg':{'message_id':99}}
            if path=='/get_msg':
                outgoing=payload['message_id']==99
                parts=self.last_message if outgoing and self.last_message and self.last_message[0]['type']=='text' else [{'type':'record','data':{'file':'native-record.silk'}}]
                if not outgoing and self.input_text is not None:parts=[{'type':'text','data':{'text':self.input_text}}]
                result[path]={'message_id':payload['message_id'],'message_type':self.channel,'group_id':34567890 if self.channel=='group' else None,'sender':{'user_id':12345678 if outgoing else 99999999 if self.wrong_thread else self.sender_id},'message':parts}
            if path.startswith('/send_'):self.last_message=payload['message']
            if self.drop_send and path=='/send_private_msg':return
            body=json.dumps({'status':'ok','retcode':0,'data':result[path]}).encode()
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body);await writer.drain()
        finally:
            writer.close();await writer.wait_closed();self.active.discard(task)

    def event(self,**changes):
        return {'self_id':12345678,'user_id':23456789,'post_type':'message','message_type':'private','message_id':11,'time':time.time(),
            'message':[{'type':'record','data':{'file':'native-record.silk'}}],**changes}

    def ingest(self,**changes):
        self.assertIsNotNone(self.inbox.ingest(self.event(**changes)));return self.inbox.next()

    def enable_choice(self,selected='text'):
        self.settings['reply_formats']=['text','voice'];self.settings['incoming']['handle_text']=True
        (self.cfg.data/'voice.private.json').write_text(json.dumps(self.settings),encoding='utf8')
        (self.cfg.data/'speech.private.json').write_text(json.dumps({'version':1,'serial':self.cfg.serial,'enabled':True,
            'tts':{'enabled':True,'provider':'windows_sapi','voice_id':'fixture'}}),encoding='utf8')
        self.chosen_format=selected

    def text_item(self):
        self.input_text='请结合当前情况自然回复。'
        return self.ingest(message=[{'type':'text','data':{'text':self.input_text}}])

    async def test_text_incoming_can_choose_native_text_without_asr_or_tts(self):
        self.enable_choice('text');item=self.text_item()
        result=await QQVoiceAgent(self.loop,self.inbox).run(item)
        self.assertEqual(result['outcome'],'sent_observed');self.assertEqual(result['reply_format'],'text')
        self.speech.transcribe.assert_not_awaited();self.speech.synthesize.assert_not_awaited()
        self.assertEqual(self.last_message,[{'type':'text','data':{'text':self.draft['text']}}])
        self.assertEqual(self.model_data[0]['reply_formats'],['text','voice'])

    async def test_production_step_routes_native_text_without_phone_observation(self):
        self.enable_choice('text');self.text_item()
        loop=SessionLoop(self.cfg,self.root/'unused-text');loop.store=self.store;loop.speech=self.speech;loop.voice_inbox=self.inbox
        loop.brain=self.loop.brain;loop.guarded=self.loop.guarded;loop.check_stop=lambda:None
        self.assertEqual((await loop.step())['reply_format'],'text')

    async def test_long_text_opt_out_is_checked_after_preview_limit(self):
        self.enable_choice('text');self.input_text='普通内容'*600+'停止回复'
        item=self.ingest(message=[{'type':'text','data':{'text':self.input_text}}])
        self.assertEqual((await QQVoiceAgent(self.loop,self.inbox).run(item))['outcome'],'contact_opted_out')
        self.assertEqual(self.model_data,[])

    async def test_native_text_uncertain_send_never_falls_back_to_voice(self):
        self.enable_choice('text');item=self.text_item();self.drop_send=True
        with self.assertRaises(Blocked):await QQVoiceAgent(self.loop,self.inbox).run(item)
        self.assertEqual(self.store.db.execute('SELECT status FROM social_outbox').fetchone()[0],'delivery_unknown')
        self.assertEqual(self.store.db.execute('SELECT status FROM voice_inbound').fetchone()[0],'delivery_unknown')
        self.speech.synthesize.assert_not_awaited()

    async def test_voice_incoming_can_choose_text(self):
        self.enable_choice('text');item=self.ingest()
        result=await QQVoiceAgent(self.loop,self.inbox).run(item)
        self.assertEqual(result['reply_format'],'text');self.speech.transcribe.assert_awaited_once();self.speech.synthesize.assert_not_awaited()

    async def test_text_incoming_can_choose_voice_and_same_intent_cannot_switch_and_resend(self):
        self.enable_choice('voice');item=self.text_item()
        result=await QQVoiceAgent(self.loop,self.inbox).run(item)
        self.assertEqual(result['outcome'],'sent_observed');self.assertEqual(result['reply_format'],'voice')
        self.speech.transcribe.assert_not_awaited();self.speech.synthesize.assert_awaited_once()
        self.chosen_format='text'
        self.assertEqual((await QQVoiceAgent(self.loop,self.inbox).run(item))['outcome'],'voice_event_already_handled')
        self.assertEqual(len([x for x in self.calls if x[0].startswith('/send_')]),1)

    async def test_invalid_reply_format_and_tts_failure_do_not_fallback(self):
        self.enable_choice('file');item=self.text_item()
        with self.assertRaisesRegex(ValueError,'DRAFT_INVALID'):await QQVoiceAgent(self.loop,self.inbox).run(item)
        self.assertFalse(any(p.startswith('/send_') for p,_ in self.calls))
        self.chosen_format='voice';self.speech.synthesize.side_effect=Blocked('SPEECH_ENGINE_FAILED')
        item=self.ingest(message_id=12,message=[{'type':'text','data':{'text':self.input_text}}])
        with self.assertRaisesRegex(Blocked,'ENGINE_FAILED'):await QQVoiceAgent(self.loop,self.inbox).run(item)
        self.assertFalse(any(p.startswith('/send_') for p,_ in self.calls))

    async def test_native_proactive_target_uses_real_history_without_phone_ui(self):
        self.enable_choice('text');agent=QQVoiceAgent(self.loop,self.inbox)
        binding=await agent.sender.binding('23456789');bid=agent.sender.social.bind(binding)
        result=await agent.run_target(bid,'proactive_dm')
        self.assertEqual(result['outcome'],'sent_observed');self.assertEqual(result['reply_format'],'text')
        self.assertEqual(self.model_data[0]['action_kind'],'proactive_dm')
        self.assertEqual(self.model_data[0]['conversation']['messages'][0]['text'],'前一条同线程消息')
        self.assertEqual((await agent.run_target(bid,'proactive_dm'))['outcome'],'native_context_already_recorded')

    def test_native_text_is_opt_in_and_group_text_requires_self_mention(self):
        raw=self.event(message=[{'type':'text','data':{'text':'hello'}}])
        self.assertIsNone(self.inbox.ingest(raw));self.enable_choice()
        self.assertIsNone(self.inbox.ingest({**raw,'message_type':'group','group_id':34567890}))
        self.assertIsNotNone(self.inbox.ingest({**raw,'message_type':'group','group_id':34567890,'message':[{'type':'at','data':{'qq':'12345678'}},*raw['message']]}))

    async def test_production_step_transcribes_actual_record_and_sends_once(self):
        item=self.ingest()
        loop=SessionLoop(self.cfg,self.root/'unused');loop.store=self.store;loop.speech=self.speech;loop.voice_inbox=self.inbox
        loop.brain=self.loop.brain;loop.guarded=self.loop.guarded;loop.check_stop=lambda:None
        result=await loop.step()
        self.assertEqual(result['outcome'],'sent_observed');self.assertEqual(len([p for p,_ in self.calls if p=='/send_private_msg']),1)
        self.assertEqual(self.model_data[0]['transcript']['text'],self.transcript['text'])
        self.assertEqual(self.model_data[0]['conversation']['messages'][0]['text'],'前一条同线程消息')
        self.assertTrue(self.speech.transcribe.call_args.args[0]['source_ref'].endswith(item['event_id']))
        self.assertIsNone(self.inbox.next());self.assertIsNone(self.inbox.ingest(self.event()))
        self.assertEqual(self.store.db.execute('SELECT status FROM social_outbox').fetchone()[0],'sent_observed')
        self.assertEqual(self.store.state['dispatched'],1)
        self.assertEqual((await QQVoiceAgent(self.loop,self.inbox).run(item))['outcome'],'voice_event_already_handled')

    def test_rejects_self_wrong_account_stale_or_non_record_and_expires_body(self):
        for change in ({'user_id':12345678},{'self_id':87654321},{'time':time.time()-4000},{'message':[]},{'message':[{'type':'record','data':{}}]}):
            self.assertIsNone(self.inbox.ingest(self.event(**change)))
        item=self.ingest();self.inbox.expire(time.time()+4000)
        self.assertEqual(self.store.db.execute('SELECT status,body FROM voice_inbound').fetchone(),('expired','{}'))

    async def test_actual_message_in_other_thread_never_reaches_asr_or_sender(self):
        item=self.ingest();self.wrong_thread=True
        with self.assertRaisesRegex(Blocked,'THREAD_MISMATCH'):await QQVoiceAgent(self.loop,self.inbox).run(item)
        self.speech.transcribe.assert_not_awaited();self.assertFalse(any(p.startswith('/send_') for p,_ in self.calls));self.assertIsNone(self.inbox.next())

    async def test_group_reply_keeps_group_route_and_never_sends_to_speaker_dm(self):
        self.channel='group';item=self.ingest(message_type='group',group_id=34567890)
        result=await QQVoiceAgent(self.loop,self.inbox).run(item)
        self.assertEqual(result['outcome'],'sent_observed')
        self.assertEqual([v['group_id'] for p,v in self.calls if p=='/send_group_msg'],[34567890])
        self.assertFalse(any(p=='/send_private_msg' for p,_ in self.calls))

    async def test_unavailable_audio_is_never_invented_from_event_label(self):
        item=self.ingest();self.no_audio=True
        with self.assertRaisesRegex(Blocked,'AUDIO_SOURCE_UNAVAILABLE'):await QQVoiceAgent(self.loop,self.inbox).run(item)
        self.speech.transcribe.assert_not_awaited();self.assertEqual(self.model_data,[])

    async def test_stop_during_tts_cancels_before_any_outbound(self):
        item=self.ingest();self.speech.synthesize.side_effect=asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):await QQVoiceAgent(self.loop,self.inbox).run(item)
        self.assertFalse(any(p.startswith('/send_') for p,_ in self.calls));self.assertIsNone(self.inbox.next())

    async def test_session_dispatch_budget_stops_native_send(self):
        item=self.ingest();self.cfg.max_actions=1;self.store.update(dispatched=1)
        with self.assertRaisesRegex(Blocked,'ACTION_BUDGET'):await QQVoiceAgent(self.loop,self.inbox).run(item)
        self.assertFalse(any(p.startswith('/send_') for p,_ in self.calls))
        self.assertEqual(self.store.db.execute('SELECT status FROM social_outbox').fetchone()[0],'cancelled_before_send')

    async def test_uncertain_transcript_can_be_deferred_without_synthesis(self):
        item=self.ingest();self.transcript['uncertain']=True;self.transcript['complete']=False;self.draft['decision']='defer'
        result=await QQVoiceAgent(self.loop,self.inbox).run(item)
        self.assertEqual(result['outcome'],'defer');self.assertTrue(self.model_data[0]['transcript']['uncertain'])
        self.speech.synthesize.assert_not_awaited();self.assertIsNone(self.inbox.next())

    async def test_spoken_opt_out_stops_before_model_or_outbound(self):
        item=self.ingest();self.transcript['text']='不要回复，停止回复'
        result=await QQVoiceAgent(self.loop,self.inbox).run(item)
        self.assertEqual(result['outcome'],'contact_opted_out');self.assertEqual(self.model_data,[]);self.speech.synthesize.assert_not_awaited()

    async def test_uncertain_dispatch_never_replays_after_new_session(self):
        item=self.ingest();self.drop_send=True
        with self.assertRaises(Blocked):await QQVoiceAgent(self.loop,self.inbox).run(item)
        self.assertEqual(self.store.db.execute('SELECT status FROM voice_inbound').fetchone()[0],'delivery_unknown')
        self.assertEqual(self.store.db.execute('SELECT status FROM social_outbox').fetchone()[0],'delivery_unknown')
        fresh=QQVoiceInbox(self.cfg,self.store);fresh.account='12345678'
        self.assertIsNone(fresh.ingest(self.event()));self.assertIsNone(fresh.next())
        self.assertEqual(len([p for p,_ in self.calls if p=='/send_private_msg']),1)

    async def test_sse_chunking_utf8_and_cancel_close_connection(self):
        disconnected=asyncio.Event()
        async def serve(reader,writer):
            await reader.readuntil(b'\r\n\r\n')
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n')
            raw=('data: '+json.dumps({'text':'实际语音事件'},ensure_ascii=False)+'\n\n').encode()
            for part in (raw[:12],raw[12:]):writer.write(f'{len(part):x}\r\n'.encode()+part+b'\r\n')
            await writer.drain();await reader.read();disconnected.set();writer.close();await writer.wait_closed()
        server=await asyncio.start_server(serve,'127.0.0.1',0)
        try:
            ready=asyncio.Event();stream=events('http://127.0.0.1:'+str(server.sockets[0].getsockname()[1]),'token',ready)
            self.assertEqual(await anext(stream),{'text':'实际语音事件'});self.assertTrue(ready.is_set())
            await stream.aclose();await asyncio.wait_for(disconnected.wait(),2)
        finally:server.close();await server.wait_closed()
