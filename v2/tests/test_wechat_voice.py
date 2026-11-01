import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,Mock,patch

from PIL import Image,ImageDraw
from digitallife_v2.safety import Blocked
from digitallife_v2.social import Binding
from digitallife_v2.speech import SpeechEngine,inspect_wav
from digitallife_v2.store import LifeStore
from digitallife_v2.session import SessionLoop
from digitallife_v2.wechat_voice import WechatVoiceAgent,DesktopWechatTransport,WechatVoicePolicy
from digitallife_v2.wechat_desktop_worker import WechatDesktopUI,direction_from_image,normalized_title
from test_m1 import config,observation
from test_speech import audio


class WechatVoiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.cfg=config(self.root)
        directory=self.root/'session';directory.mkdir()
        self.store=LifeStore(self.cfg.data,str(directory),self.cfg.serial,'fixture');self.addCleanup(self.store.close)
        self.binding=Binding('com.tencent.mm','wx_fixture@proof','ui-chat:fixture','dm',True,('fixture-phone-observation',),time.time(),'fixture','测试对象')
        self.settings={'version':1,'serial':self.cfg.serial,'enabled':True,'provider':'windows_uia',
            'account':'wx_fixture','reply_formats':['text','voice'],'handle_text':False}
        self.grant={'version':1,'serial':self.cfg.serial,'enabled':True,'accounts':{'com.tencent.mm':self.binding.account},
            'contacts':{'mode':'all_existing_contacts','auto_reply':True,'proactive_dm':True},
            'groups':{'mode':'all_existing_groups','group_post':True},'quiet_hours':['00:00','00:00']}
        self.save()
        (self.cfg.data/'speech.private.json').write_text(json.dumps({'version':1,'serial':self.cfg.serial,'enabled':True,
            'tts':{'enabled':True,'provider':'windows_sapi','voice_id':'fixture'}}),encoding='utf8')
        self.speech=SpeechEngine(self.cfg,self.store);path=self.speech.cache/'test.wav';audio(path)
        self.asset=self.speech.asset('tts',{'path':str(path),**inspect_wav(path)[0],'text':'先喝杯水休息一下吧。'},600)
        self.speech.synthesize=AsyncMock(return_value=self.asset);self.speech.transcribe=AsyncMock()
        self.selected='voice';self.decision='send';self.model_data=[];self.before_go=[];self.drop=False
        self.incoming={'message_key':'a'*64,'direction':'incoming','kind':'voice','text':'刚散步回来，有什么建议？','time_label':'12:00'}
        self.actual={'outcome':'incoming_observed','account':'wx_fixture','title':'测试对象','incoming':self.incoming,
                     'messages':[self.incoming], 'engine_id':'wechat_platform_transcription'}
        async def call(payload,before_dispatch=None):
            if payload['operation']=='inspect':return self.actual
            self.before_go.append(self.store.db.execute('SELECT status FROM social_outbox').fetchone()[0])
            await before_dispatch()
            self.assertEqual(self.store.db.execute('SELECT status FROM social_outbox').fetchone()[0],'dispatch_attempted')
            if self.drop:raise Blocked('fixture_receipt_unknown')
            return {'outcome':'sent_observed','accepted':True,'native_voice_seconds':2}
        self.transport=SimpleNamespace(call=AsyncMock(side_effect=call))
        async def structured(system,data,images,validate):
            self.model_data.append(data)
            draft={'decision':self.decision,'format':self.selected,'text':'先喝杯水休息一下吧。','motive':'回应当前实际来信','sources':data['offered_sources']}
            validate(draft);return draft
        async def guarded(awaitable):return await awaitable
        self.loop=SimpleNamespace(config=self.cfg,store=self.store,speech=self.speech,brain=SimpleNamespace(structured=structured),
            guarded=guarded,check_stop=lambda:None)
        self.ui=SimpleNamespace(resolve=AsyncMock(return_value=(self.binding,{'mentions_self':False},SimpleNamespace(observation_id='phone'))))
        self.agent=WechatVoiceAgent(self.loop,self.ui,transport=self.transport)

    def save(self):
        (self.cfg.data/'social.private.json').write_text(json.dumps(self.grant),encoding='utf8')
        (self.cfg.data/'wechat-voice.private.json').write_text(json.dumps(self.settings),encoding='utf8')

    async def test_platform_transcript_path_does_not_claim_raw_asr(self):
        result=await self.agent.run('auto_reply')
        self.assertEqual(result['outcome'],'sent_observed');self.assertEqual(self.before_go,['voice_ready'])
        self.speech.transcribe.assert_not_awaited();self.speech.synthesize.assert_awaited_once()
        transcript=self.model_data[0]['transcript'];self.assertEqual(transcript['text'],self.incoming['text'])
        self.assertIsNone(transcript['confidence']);self.assertTrue(transcript['uncertain'])
        self.assertEqual(transcript['engine_id'],'wechat_platform_transcription')

    async def test_voice_incoming_can_choose_text_without_tts(self):
        self.selected='text';result=await self.agent.run('auto_reply')
        self.assertEqual(result['reply_format'],'text');self.speech.synthesize.assert_not_awaited()
        self.assertEqual(self.before_go,['text_ready'])

    async def test_repeat_source_is_not_sent_again_after_restart(self):
        await self.agent.run('auto_reply')
        # Bypass only the per-thread cooldown to reach durable source deduplication.
        self.agent.social.check_thread=lambda *args:None
        self.assertEqual((await self.agent.run('auto_reply'))['outcome'],'incoming_already_recorded')
        self.assertEqual(len(self.before_go),1);self.assertEqual(len(self.model_data),1)

    async def test_unknown_send_never_switches_format_or_retries(self):
        self.drop=True
        with self.assertRaises(Blocked):await self.agent.run('auto_reply')
        self.assertEqual(self.store.db.execute('SELECT status FROM social_outbox').fetchone()[0],'delivery_unknown')
        self.selected='text'
        with self.assertRaises(Blocked):await self.agent.run('auto_reply')
        self.assertEqual(len(self.before_go),1)

    async def test_verified_late_readback_resolves_unknown_without_resending(self):
        self.drop=True
        with self.assertRaises(Blocked):await self.agent.run('auto_reply')
        oid=self.store.db.execute('SELECT id FROM social_outbox').fetchone()[0]
        evidence={'outbound_id':oid,'outcome':'outgoing_observed','engine_id':'wechat_platform_transcription',
            'account':'wx_fixture','title':'测试对象','additional_messages_sent':0,'reconciled_read_only':True,
            'outgoing':{'message_key':'b'*64,'direction':'outgoing','kind':'voice','seconds':2,'text':'先喝杯水休息一下吧！'}}
        for invalid in ({**evidence,'title':'另一对象'}, {**evidence,'outgoing':{**evidence['outgoing'],'text':'不同内容'}},
                        {**evidence,'reconciled_read_only':False}):
            with self.assertRaisesRegex(Blocked,'LATE_WECHAT_RECORD_UNVERIFIED'):
                self.agent.social.reconcile_wechat_record(oid,invalid)
        self.agent.social.reconcile_wechat_record(oid,evidence)
        self.assertEqual(self.store.db.execute('SELECT status FROM social_outbox').fetchone()[0],'sent_observed')
        self.assertEqual(len(self.before_go),1)
        # The original trigger remains deduplicated; reconciliation creates no second attempt.
        self.agent.social.check_thread=lambda *args:None
        self.assertEqual((await self.agent.run('auto_reply'))['outcome'],'incoming_already_recorded')
        self.assertEqual(len(self.before_go),1)

    async def test_missing_voice_body_does_not_guess_notification_content(self):
        self.incoming['text']=''
        with self.assertRaisesRegex(Blocked,'AUDIO_SOURCE_UNAVAILABLE'):await self.agent.run('auto_reply',event={'preview':'[语音]'})
        self.assertFalse(self.model_data);self.assertFalse(self.before_go)

    async def test_wrong_account_or_conversation_never_reaches_model(self):
        for field in ('account','title'):
            previous=self.actual[field];self.actual[field]='wrong'
            with self.assertRaises(Blocked):await self.agent.run('auto_reply')
            self.actual[field]=previous
        self.assertFalse(self.model_data)

    async def test_voice_notification_cannot_be_replaced_with_an_unrelated_text(self):
        self.settings['handle_text']=True;self.save();self.incoming['kind']='text'
        with self.assertRaisesRegex(Blocked,'SOURCE_CHANGED'):await self.agent.run('auto_reply',event={'preview':'[语音]'})
        self.assertFalse(self.model_data)

    async def test_spoken_opt_out_is_applied_before_model_or_sending(self):
        self.incoming['text']='停止回复'
        self.assertEqual((await self.agent.run('auto_reply'))['outcome'],'contact_opted_out')
        self.assertFalse(self.model_data);self.assertFalse(self.before_go)

    async def test_group_reply_remains_in_the_group(self):
        self.binding=replace(self.binding,kind='group',peer='ui-chat:testgroup')
        self.ui.resolve.return_value=(self.binding,{'mentions_self':True},SimpleNamespace(observation_id='group'))
        result=await self.agent.run('auto_reply')
        row=self.store.db.execute('SELECT b.kind,o.kind FROM social_bindings b JOIN social_outbox o ON b.id=o.binding_id').fetchone()
        self.assertEqual(row,('group','group_post'));self.assertEqual(result['outcome'],'sent_observed')
        self.assertTrue(self.model_data[0]['conversation']['mentions_self'])

    async def test_defer_or_ignore_never_creates_an_outbox(self):
        for decision in ('defer','ignore'):
            self.decision=decision;self.assertEqual((await self.agent.run('auto_reply'))['outcome'],decision)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM social_outbox').fetchone()[0],0)
        self.speech.synthesize.assert_not_awaited()

    async def test_policy_revocation_after_model_stops_before_dispatch(self):
        original=self.loop.brain.structured
        async def revoke(*args):
            result=await original(*args);self.settings['enabled']=False;self.save();return result
        self.loop.brain.structured=revoke
        with self.assertRaises(Blocked):await self.agent.run('auto_reply')
        self.assertFalse(self.before_go)

    def production_loop(self,preview):
        loop=SessionLoop(self.cfg,self.root/'unused-runtime');loop.store=self.store;loop.speech=self.speech
        loop.guarded=self.loop.guarded;loop.check_stop=lambda:None;loop.observed=lambda obs:None
        loop.phone=SimpleNamespace(observe=AsyncMock(return_value=observation('wx-runtime')),catalog=AsyncMock(return_value={}))
        event={'event_id':'native-notification','source_app':'com.tencent.mm','preview':preview}
        loop.hub=SimpleNamespace(next=lambda:event,resolve=Mock())
        loop.brain=SimpleNamespace(structured=self.loop.brain.structured,decide=AsyncMock(return_value={
            'intent':'rest','action':{'type':'rest','seconds':1},
            'event_response':{'event_id':event['event_id'],'decision':'reply_now','note':'respond to actual notification'}}))
        return loop

    async def test_production_step_routes_voice_notification_through_wechat_agent(self):
        loop=self.production_loop('[语音]');self.agent.loop=loop
        with patch('digitallife_v2.wechat_voice.WechatVoiceAgent',return_value=self.agent) as factory:
            result=await loop.step()
        factory.assert_called_once();self.assertEqual(result['outcome'],'sent_observed')
        self.assertEqual(self.before_go,['voice_ready'])

    async def test_normal_text_path_is_preserved_when_voice_text_handling_is_off(self):
        loop=self.production_loop('普通文字消息')
        with patch('digitallife_v2.social_agent.SocialAgent.run',new=AsyncMock(return_value={'outcome':'legacy_text'})) as text_path:
            result=await loop.step()
        self.assertEqual(result['outcome'],'legacy_text');text_path.assert_awaited_once()
        self.assertFalse(self.before_go)

    async def test_disabling_voice_does_not_disable_existing_wechat_text_replies(self):
        self.settings['enabled']=False;self.save();loop=self.production_loop('普通文字消息')
        with patch('digitallife_v2.social_agent.SocialAgent.run',new=AsyncMock(return_value={'outcome':'legacy_text'})):
            self.assertEqual((await loop.step())['outcome'],'legacy_text')
        self.assertFalse(self.before_go)

    def test_disabled_or_different_device_account_is_not_enabled(self):
        for key,value in (('enabled',False),('serial','OTHER_DEVICE'),('account','personal_account')):
            previous=self.settings[key];self.settings[key]=value;self.save()
            with self.assertRaises(Blocked):WechatVoicePolicy(self.cfg).read()
            self.settings[key]=previous

    def test_bubble_direction_requires_positive_visual_evidence(self):
        rect=(0,0,600,90);image=Image.new('RGB',(600,90),(245,245,245))
        self.assertEqual(direction_from_image(image,rect,rect),'unknown')
        draw=ImageDraw.Draw(image);draw.rectangle((400,20,540,70),fill=(149,236,105))
        self.assertEqual(direction_from_image(image,rect,rect),'outgoing')
        image=Image.new('RGB',(600,90),(245,245,245));ImageDraw.Draw(image).rectangle((80,30,98,50),fill=(30,30,30))
        self.assertEqual(direction_from_image(image,rect,rect),'incoming')
        self.assertEqual(normalized_title('测试群(3)'),'测试群')

    def test_scrolled_old_voice_identity_is_not_counted_as_an_extra_send(self):
        ui=WechatDesktopUI.__new__(WechatDesktopUI)
        ui.voices=lambda:{'moved-old-key':{'seconds':11},'new-key':{'seconds':9}}
        self.assertEqual(ui.observe_new_voice({'old-key':{'seconds':11}}),{'seconds':9})

    def test_changed_identity_without_an_added_voice_stays_unknown(self):
        ui=WechatDesktopUI.__new__(WechatDesktopUI);ui.voices=lambda:{'moved-key':{'seconds':11}}
        with patch('digitallife_v2.wechat_desktop_worker.time.monotonic',side_effect=[0,1,5]), \
                patch('digitallife_v2.wechat_desktop_worker.time.sleep'):
            self.assertIsNone(ui.observe_new_voice({'old-key':{'seconds':11}}))

    async def test_transport_cancel_before_go_does_not_send_go(self):
        script=self.root/'worker.py'
        script.write_text('import sys,json\nv=json.loads(sys.stdin.buffer.readline())\nprint(json.dumps({"phase":"ready","target_verified":True,"account":v["account"],"title":v["title"]}),flush=True)\ncommand=sys.stdin.buffer.readline().decode().strip()\nopen(v["data"]+"/command.txt","w").write(command)\n',encoding='utf8')
        transport=DesktopWechatTransport(self.cfg,self.store,command=[sys.executable,'-u',str(script)])
        async def stopped():raise Blocked('stopped_before_GO')
        with self.assertRaisesRegex(Blocked,'stopped_before_GO'):
            await transport.call({'operation':'send','account':'wx_fixture','title':'测试对象','format':'voice'},stopped)
        self.assertEqual((self.cfg.data/'command.txt').read_text(),'CANCEL')

    async def test_transport_cancel_after_go_notifies_the_worker_and_waits_for_cleanup(self):
        script=self.root/'worker.py'
        script.write_text('import sys,json\nv=json.loads(sys.stdin.buffer.readline())\nprint(json.dumps({"phase":"ready","target_verified":True,"account":v["account"],"title":v["title"]}),flush=True)\nassert sys.stdin.buffer.readline().strip()==b"GO"\nopen(v["data"]+"/started","w").write("yes")\ncommand=sys.stdin.buffer.readline().decode().strip()\nopen(v["data"]+"/cleaned","w").write(command)\n',encoding='utf8')
        transport=DesktopWechatTransport(self.cfg,self.store,command=[sys.executable,'-u',str(script)])
        async def ready():pass
        task=asyncio.create_task(transport.call({'operation':'send','account':'wx_fixture','title':'测试对象','format':'voice'},ready))
        for _ in range(100):
            if (self.cfg.data/'started').exists():break
            await asyncio.sleep(.02)
        self.assertTrue((self.cfg.data/'started').exists());task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual((self.cfg.data/'cleaned').read_text(),'CANCEL')
