import asyncio
import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from digitallife_v2.local_http import endpoint,request
from digitallife_v2.qq_voice import QQVoiceSender
from digitallife_v2.safety import Blocked
from digitallife_v2.speech import SpeechEngine,inspect_wav
from digitallife_v2.store import LifeStore
from test_m1 import config
from test_speech import audio


class VoiceBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.cfg=config(self.root);session=self.root/'session';session.mkdir()
        self.store=LifeStore(self.cfg.data,str(session),self.cfg.serial,'fixture');self.addCleanup(self.store.close)
        self.speech=SpeechEngine(self.cfg,self.store);self.sender=QQVoiceSender(self.cfg,self.store,self.speech)
        self.social={'version':1,'serial':self.cfg.serial,'enabled':True,'accounts':{'com.tencent.mobileqq':'12345678'},
            'contacts':{'mode':'all_existing_contacts','auto_reply':True,'proactive_dm':True},
            'groups':{'mode':'all_existing_groups','group_post':True},'quiet_hours':['00:00','00:00']}
        self.sender.policy.path.write_text(json.dumps(self.social),encoding='utf8')
        self.calls=[];self.send_mode='ok';self.receipt_record=True;self.login='12345678';self.active=set()
        self.server=await asyncio.start_server(self.handle,'127.0.0.1',0)
        self.addAsyncCleanup(self.close_server)
        port=self.server.sockets[0].getsockname()[1];self.url=f'http://127.0.0.1:{port}'
        self.settings={'version':1,'serial':self.cfg.serial,'enabled':True,'provider':'napcat','endpoint':self.url,'access_token':'fixture-only-token-1234'}
        (self.cfg.data/'voice.private.json').write_text(json.dumps(self.settings),encoding='utf8')
        self.wav=self.speech.cache/'source.wav';audio(self.wav)
        self.asset=self.speech.asset('tts',{'path':str(self.wav),**inspect_wav(self.wav)[0],'text':'静默语音测试','text_sha256':'fixture'},600)

    async def close_server(self):
        self.server.close();await self.server.wait_closed()
        for task in tuple(self.active):task.cancel()
        await asyncio.gather(*tuple(self.active),return_exceptions=True)

    async def handle(self,reader,writer):
        task=asyncio.current_task();self.active.add(task)
        try:
            head=await reader.readuntil(b'\r\n\r\n');lines=head.decode().split('\r\n')
            fields={k.lower():v.strip() for line in lines[1:] if ':' in line for k,v in [line.split(':',1)]}
            body=json.loads(await reader.readexactly(int(fields['content-length'])))
            path=lines[0].split()[1];self.calls.append((path,body,fields))
            if path=='/get_login_info':data={'user_id':int(self.login),'nickname':'fixture'}
            elif path=='/get_friend_list':data=[{'user_id':23456789}]
            elif path=='/get_group_list':data=[{'group_id':34567890}]
            elif path in {'/send_private_msg','/send_group_msg'}:
                self.last_send=body
                if self.send_mode=='drop':return
                if self.send_mode=='hang':await reader.read();return
                data={'message_id':41}
            elif path=='/get_msg':data={'message_id':41,'sender':{'user_id':12345678},
                'user_id':12345678,'group_id':self.last_send.get('group_id'),
                'message':[{'type':'record' if self.receipt_record else 'file','data':{'file':'converted.silk'}}]}
            elif path=='/tts':
                if self.send_mode=='json_error':raw=b'{"error":"model unavailable"}'
                else:raw=self.wav.read_bytes()
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: audio/wav\r\nTransfer-Encoding: chunked\r\n\r\n')
                writer.write(f'{len(raw):x}\r\n'.encode()+raw+b'\r\n0\r\n\r\n');await writer.drain();return
            else:data={}
            raw=json.dumps({'status':'ok','retcode':0,'data':data}).encode()
            writer.write(f'HTTP/1.1 200 OK\r\nContent-Length: {len(raw)}\r\nContent-Type: application/json\r\n\r\n'.encode()+raw);await writer.drain()
        finally:
            writer.close();await writer.wait_closed();self.active.discard(task)

    def outbound_state(self):return self.store.db.execute('SELECT status FROM social_outbox').fetchone()[0]
    def sends(self):return [c for c in self.calls if c[0].startswith('/send_')]

    async def test_file_becomes_record_without_speaker_mic_or_ui_and_same_intent_never_repeats(self):
        result=await self.sender.send(self.asset['speech_ref'],target='23456789',intent='one-source')
        self.assertEqual(result['outcome'],'sent_observed');self.assertFalse(result['speaker_used']);self.assertFalse(result['microphone_used'])
        message=self.sends()[0][1]['message'];self.assertEqual([m['type'] for m in message],['record'])
        self.assertEqual(base64.b64decode(message[0]['data']['file'][9:]),self.wav.read_bytes())
        self.assertEqual(self.sends()[0][2]['authorization'],'Bearer fixture-only-token-1234')
        with self.assertRaisesRegex(Blocked,'INTENT_ALREADY'):await self.sender.send(self.asset['speech_ref'],target='23456789',intent='one-source')
        self.assertEqual(len(self.sends()),1)

    async def test_self_test_never_loads_contacts_and_group_uses_group_endpoint(self):
        await self.sender.send(self.asset['speech_ref'],intent='self-test')
        self.assertFalse(any(c[0]=='/get_friend_list' for c in self.calls))
        self.assertEqual(self.sends()[0][1]['user_id'],12345678)
        self.wav.unlink();audio(self.wav,seconds=1.1)
        asset=self.speech.asset('tts',{'path':str(self.wav),**inspect_wav(self.wav)[0],'text':'群内语音测试'},600)
        result=await self.sender.send(asset['speech_ref'],target_kind='group',target='34567890',intent='group-source')
        self.assertEqual(result['outcome'],'sent_observed');self.assertEqual(self.sends()[-1][0],'/send_group_msg')

    async def test_wrong_login_unknown_target_or_revoked_scope_never_send(self):
        self.login='99999999'
        with self.assertRaisesRegex(Blocked,'ACCOUNT_MISMATCH'):await self.sender.send(self.asset['speech_ref'],intent='wrong-login')
        self.login='12345678'
        with self.assertRaisesRegex(Blocked,'EXISTING_TARGET'):await self.sender.send(self.asset['speech_ref'],target='99999999',intent='unknown-target')
        self.social['enabled']=False;self.sender.policy.path.write_text(json.dumps(self.social),encoding='utf8')
        with self.assertRaisesRegex(Blocked,'SOCIAL_DISABLED'):await self.sender.send(self.asset['speech_ref'],intent='revoked')
        self.assertEqual(self.sends(),[])

    async def test_dropped_response_retains_unknown_state_and_no_second_attempt(self):
        self.send_mode='drop'
        with self.assertRaises(Blocked):await self.sender.send(self.asset['speech_ref'],intent='dropped')
        self.assertEqual(self.outbound_state(),'delivery_unknown')
        with self.assertRaisesRegex(Blocked,'INTENT_ALREADY'):await self.sender.send(self.asset['speech_ref'],intent='dropped')
        self.assertEqual(len(self.sends()),1)

    async def test_cancellation_after_dispatch_is_unknown_and_closes_socket(self):
        self.send_mode='hang';task=asyncio.create_task(self.sender.send(self.asset['speech_ref'],intent='cancelled'))
        for _ in range(100):
            if self.sends():break
            await asyncio.sleep(.005)
        self.assertEqual(len(self.sends()),1);task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(self.outbound_state(),'delivery_unknown')

    async def test_file_attachment_receipt_is_not_voice_acceptance(self):
        self.receipt_record=False
        result=await self.sender.send(self.asset['speech_ref'],intent='wrong-kind')
        self.assertEqual(result['outcome'],'delivery_unknown');self.assertFalse(result['client_record_observed'])

    async def test_local_character_tts_decodes_chunked_wav_and_preserves_voice_metadata(self):
        policy={'version':1,'serial':self.cfg.serial,'enabled':True,'tts':{'enabled':True,'provider':'gpt_sovits_local',
            'endpoint':self.url,'voice_id':'winefox-character','reference_audio':str(self.wav),
            'prompt_text':'参考语音','prompt_language':'ja','text_language':'auto','voice_source':'creator-model-package'}}
        self.speech.policy.path.write_text(json.dumps(policy),encoding='utf8')
        with patch('digitallife_v2.speech.run_worker',side_effect=AssertionError('must not fall back to SAPI')):
            result=await self.speech.synthesize('你好，这是新的角色音色。')
        self.assertEqual(result['engine_id'],'gpt_sovits_local');self.assertTrue(result['cloned_voice'])
        self.assertEqual(result['voice_source'],'creator-model-package');self.assertEqual(result['seconds'],1)
        sent=[c for c in self.calls if c[0]=='/tts'][0][1]
        self.assertFalse(sent['streaming_mode']);self.assertEqual(sent['prompt_lang'],'ja')
        self.send_mode='json_error'
        with self.assertRaisesRegex(Blocked,'WAV_RESPONSE'):await self.speech.synthesize('模型错误不切回旧声音')
        states=self.store.db.execute('SELECT state FROM speech_usage ORDER BY rowid').fetchall()
        self.assertEqual([r[0] for r in states],['completed','failed_or_cancelled'])

    async def test_local_http_rejects_remote_redirect_payload_limit_and_times_out(self):
        for value in ('http://example.com:3000','http://127.0.0.1:3000@evil.test:80','https://127.0.0.1:3000','http://127.0.0.1:3000/path'):
            with self.assertRaisesRegex(Blocked,'ENDPOINT'):endpoint(value)
        with self.assertRaisesRegex(Blocked,'RESPONSE_LIMIT'):await request(self.url,'/get_login_info',{},max_bytes=5)
        self.send_mode='hang'
        with self.assertRaises(asyncio.TimeoutError):await request(self.url,'/send_private_msg',{},timeout=.02)


if __name__=='__main__':unittest.main()
