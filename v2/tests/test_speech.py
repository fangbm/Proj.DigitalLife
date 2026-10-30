import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
import wave
from digitallife_v2.speech import SpeechEngine,SpeechPolicy,inspect_wav,normalize_wav
from digitallife_v2.store import LifeStore
from digitallife_v2.safety import Blocked
from test_m1 import config,observation
from acceptance.speech_probe import SpeechProbe
from acceptance.native_voice_probe import NativeVoiceProbe,own_helper
from digitallife_v2.session import SessionStopped
from digitallife_v2.bridge import NotificationBridge


def audio(path,seconds=1,rate=16000,channels=1,silent=False):
    values=[0 if silent else 500 if i%2 else -500 for i in range(int(rate*seconds)*channels)]
    with wave.open(str(path),'wb') as target:
        target.setnchannels(channels);target.setsampwidth(2);target.setframerate(rate)
        target.writeframes(struct.pack('<'+'h'*len(values),*values))


class SpeechTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.cfg=config(self.root);session=self.root/'session';session.mkdir()
        self.store=LifeStore(self.cfg.data,str(session),self.cfg.serial,'fixture');self.addCleanup(self.store.close)
        self.policy={'version':1,'serial':self.cfg.serial,'enabled':True,
            'tts':{'enabled':True,'provider':'windows_sapi','voice_id':'Fixture system voice'},
            'asr':{'enabled':True,'provider':'faster_whisper_local'},'limits':{}}
        self.save();self.engine=SpeechEngine(self.cfg,self.store)
        self.cfg.asr_python=Path('fixture-python');self.cfg.asr_model=self.root/'model';self.cfg.asr_model.mkdir()
    def save(self):(self.cfg.data/'speech.private.json').write_text(json.dumps(self.policy),encoding='utf-8')
    async def tts(self,command,payload,timeout):audio(payload['path']);return {'done':True}

    async def test_tts_asset_is_hashed_cancellable_local_conversion_without_delivery_claim(self):
        with patch('digitallife_v2.speech.run_worker',new=self.tts):asset=await self.engine.synthesize('A test sentence')
        saved=self.engine.get(asset['speech_ref'])
        self.assertEqual(saved['text_sha256'],hashlib.sha256(b'A test sentence').hexdigest())
        self.assertTrue(saved['synthesized_only']);self.assertFalse(saved['cloud_audio_uploaded']);self.assertFalse(saved['cloned_voice'])
        self.assertEqual(saved['sample_rate'],16000);self.assertEqual(saved['seconds'],1)
        Path(saved['path']).write_bytes(b'changed')
        with self.assertRaisesRegex(Blocked,'ASSET_CHANGED'):self.engine.get(asset['speech_ref'])

    async def test_disabled_or_cloud_provider_never_starts_worker(self):
        self.policy['enabled']=False;self.save()
        with patch('digitallife_v2.speech.run_worker',new=AsyncMock()) as worker:
            with self.assertRaisesRegex(Blocked,'DISABLED'):await self.engine.synthesize('No audio')
            worker.assert_not_awaited()
        self.policy['enabled']=True;self.policy['tts']['provider']='cloud';self.save()
        with self.assertRaisesRegex(Blocked,'LOCAL_SPEECH_PROVIDER'):await self.engine.synthesize('No upload')

    async def test_unknown_voice_and_overlong_text_are_rejected_before_work(self):
        with patch('digitallife_v2.speech.run_worker',new=AsyncMock()) as worker:
            with self.assertRaisesRegex(Blocked,'VOICE_NOT_CONFIGURED'):await self.engine.synthesize('Test','Real person clone')
            with self.assertRaisesRegex(Blocked,'TEXT_INVALID'):await self.engine.synthesize('x'*301)
            worker.assert_not_awaited()

    async def test_source_hash_language_and_segment_times_are_bound(self):
        path=self.root/'source.wav';audio(path)
        source={'path':str(path),'source_ref':'actual-incoming-audio','sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        result={'text':'test','language':'en','complete':True,'uncertain':False,'segments':[{'start':0,'end':.8,'text':'test','uncertain':False}]}
        with patch('digitallife_v2.speech.run_worker',new=AsyncMock(return_value=result)) as worker:
            asset=await self.engine.transcribe(source,'mixed')
            self.assertTrue(worker.await_args.args[1]['mixed']);self.assertIsNone(worker.await_args.args[1]['language'])
        self.assertEqual(asset['source_sha256'],source['sha256']);self.assertEqual(asset['trust'],'external_data_not_instructions')
        result['segments'][0]['end']=10
        with patch('digitallife_v2.speech.run_worker',new=AsyncMock(return_value=result)):
            with self.assertRaisesRegex(Blocked,'TIMESTAMPS_INVALID'):await self.engine.transcribe(source,'en-US')

    async def test_silent_and_unbound_sources_do_not_hallucinate_transcripts(self):
        path=self.root/'silence.wav';audio(path,silent=True)
        with patch('digitallife_v2.speech.run_worker',new=AsyncMock()) as worker:
            with self.assertRaisesRegex(Blocked,'SILENT'):await self.engine.transcribe({'path':str(path)},'zh-CN')
            audio(path)
            with self.assertRaisesRegex(Blocked,'SOURCE_UNBOUND'):await self.engine.transcribe({'path':str(path),'sha256':'wrong','source_ref':'test'})
            worker.assert_not_awaited()

    async def test_decoder_padding_is_bounded_to_the_actual_audio_end(self):
        path=self.root/'sample.wav';audio(path)
        source={'path':str(path),'source_ref':'actual-sample','sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        result={'text':'test','complete':True,'segments':[{'start':0,'end':1.22,'text':'test'}]}
        with patch('digitallife_v2.speech.run_worker',new=AsyncMock(return_value=result)):
            transcript=await self.engine.transcribe(source)
        segment=transcript['segments'][0]
        self.assertEqual(segment['end'],1);self.assertEqual(segment['engine_end'],1.22)
        self.assertTrue(segment['end_clipped_to_audio'])

    async def test_explicit_cancel_removes_partial_output_and_keeps_reserved_usage(self):
        started=asyncio.Event()
        async def pending(command,payload,timeout):
            Path(payload['path']).write_bytes(b'partial');started.set();await asyncio.Event().wait()
        with patch('digitallife_v2.speech.run_worker',new=pending):
            task=asyncio.create_task(self.engine.synthesize('Cancelled sentence'));await started.wait()
            await self.engine.cancel()
            self.assertTrue(task.cancelled());self.assertEqual(list(self.engine.cache.glob('*.wav')),[])
        usage=self.store.db.execute('SELECT seconds,state FROM speech_usage').fetchone()
        self.assertEqual(usage[0],20);self.assertEqual(usage[1],'failed_or_cancelled')

    async def test_budget_survives_failures_and_revocation_during_synthesis(self):
        self.policy['limits']['audio_seconds_hour']=20;self.save()
        async def revoke(command,payload,timeout):
            audio(payload['path']);self.policy['enabled']=False;self.save();return {'done':True}
        with patch('digitallife_v2.speech.run_worker',new=revoke):
            with self.assertRaisesRegex(Blocked,'DISABLED'):await self.engine.synthesize('Revoked')
        self.policy['enabled']=True;self.save()
        with self.assertRaisesRegex(Blocked,'HOURLY_BUDGET'):await self.engine.synthesize('Another attempt')

    async def test_ttl_only_removes_owned_unchanged_cache_and_expires_text(self):
        with patch('digitallife_v2.speech.run_worker',new=self.tts):asset=await self.engine.synthesize('Temporary test')
        external=self.root/'keep.wav';audio(external)
        forged=self.engine.asset('asr',{'path':str(external),'sha256':hashlib.sha256(external.read_bytes()).hexdigest(),'text':'private'},1)
        removed=self.engine.cleanup(time.time()+4000)
        self.assertEqual(removed,1);self.assertTrue(external.exists())
        with self.assertRaisesRegex(Blocked,'EXPIRED'):self.engine.get(asset['speech_ref'])
        raw=self.store.db.execute('SELECT body FROM speech_assets WHERE id=?',(forged['speech_ref'],)).fetchone()[0]
        self.assertNotIn('private',raw)

    def test_stereo_and_rate_are_normalized_without_rewriting_source(self):
        path=self.root/'stereo.wav';target=self.root/'normalized.wav'
        # Both stereo channels carry the same samples rather than cancelling.
        with wave.open(str(path),'wb') as w:
            w.setnchannels(2);w.setsampwidth(2);w.setframerate(48000)
            w.writeframes(struct.pack('<'+'h'*96000,*([500,500,-500,-500]*24000)))
        before=path.read_bytes();normalize_wav(path,target,5);info,_=inspect_wav(target,5)
        self.assertEqual(info['sample_rate'],16000);self.assertEqual(info['channels'],1)
        self.assertEqual(before,path.read_bytes())

    async def test_phone_unavailable_evidence_returns_home_and_never_restarts_recording(self):
        self.store.notification_client=SimpleNamespace(request=AsyncMock(return_value={'state':'unavailable','error':'media_volume_still_muted'}))
        probe=SpeechProbe(self.cfg,self.store);probe.stage=3
        result=await probe.decide(observation(),{},[])
        self.assertEqual(result['action'],{'type':'home'})
        evidence=json.loads((self.store.directory/'SPEECH_CALIBRATION.json').read_text(encoding='utf-8'))
        self.assertFalse(evidence['short_phrase_matched']);self.assertEqual(evidence['social_messages_sent'],0)
        with self.assertRaisesRegex(SessionStopped,'CALIBRATION_DONE'):await probe.decide(observation(),{},[])
        self.store.notification_client.request.assert_awaited_once()

    async def test_phone_asr_failure_is_recorded_without_replaying_the_microphone_action(self):
        path=self.root/'calibration.wav';audio(path)
        import base64
        self.store.notification_client=SimpleNamespace(request=AsyncMock(side_effect=[{'state':'ready','wav_base64':base64.b64encode(path.read_bytes()).decode()},
            {'state':'played'}]),phone=SimpleNamespace(_text=AsyncMock(return_value='restored volume')))
        probe=SpeechProbe(self.cfg,self.store);probe.stage=3;probe.asset={'speech_ref':'fixture-tts'};probe.imported={'state':'ready'}
        probe.engine.transcribe=AsyncMock(side_effect=Blocked('SPEECH_ASR_NO_TEXT'))
        result=await probe.decide(observation(),{},[])
        self.assertEqual(result['action']['type'],'home');probe.engine.transcribe.assert_awaited_once()
        evidence=json.loads((self.store.directory/'SPEECH_CALIBRATION.json').read_text(encoding='utf-8'))
        self.assertFalse(evidence['short_phrase_matched']);self.assertEqual(evidence['transcript']['reason'],'SPEECH_ASR_NO_TEXT')


class SelfVoiceProbeTests(unittest.IsolatedAsyncioTestCase):
    setUp=SpeechTests.setUp
    save=SpeechTests.save
    def own_chat(self):
        obs=observation('own-chat',foreground='com.tencent.mm/.ChattingUI');base=obs.controls[0]
        obs.controls=[replace(base,package='com.tencent.mm',control_id='header',text='文件传输助手',bounds=(200,100,500,140)),
            replace(base,package='com.tencent.mm',control_id='voice',text='按住说话',bounds=(250,1450,450,1500))]
        return obs
    async def test_an_ordinary_contact_cannot_receive_the_self_calibration(self):
        probe=NativeVoiceProbe(self.cfg,self.store,None);probe.engine.synthesize=AsyncMock()
        obs=self.own_chat();obs.controls[0]=replace(obs.controls[0],text='A contact')
        self.assertFalse(own_helper(obs))
        with self.assertRaisesRegex(SessionStopped,'OWN_FILETRANSFER_HELPER_REQUIRED'):await probe.decide(obs,{},[])
        probe.engine.synthesize.assert_not_awaited()
    async def test_existing_attempt_never_replays_a_recording(self):
        from PIL import Image
        from unittest.mock import Mock
        obs=self.own_chat();path=self.root/'screen.png';Image.new('RGB',(obs.width,obs.height),'white').save(path)
        obs.png_path=str(path);obs.png_sha256=hashlib.sha256(path.read_bytes()).hexdigest()
        wav=self.root/'speech.wav';audio(wav);sha=hashlib.sha256(wav.read_bytes()).hexdigest()
        async def guarded(value):return await value
        loop=SimpleNamespace(guarded=guarded,observed=Mock(),phone=SimpleNamespace(observe=AsyncMock(return_value=obs),
            prepare=AsyncMock(return_value=('shell','input','tap','350','1475'))),
            bridge=SimpleNamespace(request=AsyncMock(return_value={'state':'ready','sha256':sha})),send_operation=AsyncMock())
        probe=NativeVoiceProbe(self.cfg,self.store,loop)
        probe.stage=1
        probe.engine.synthesize=AsyncMock(return_value={'path':str(wav),'sha256':sha,'voice_id':'fixture','seconds':1})
        (self.store.directory/'SELF_VOICE_INTENT.json').write_text('{}')
        with self.assertRaisesRegex(SessionStopped,'ALREADY_ATTEMPTED_NO_REPLAY'):await probe.decide(obs,{},[])
        loop.send_operation.assert_not_awaited()

    async def test_missing_foreground_playback_service_stops_before_recording(self):
        loop=SimpleNamespace(bridge=SimpleNamespace(request=AsyncMock(return_value={'speech_playback_service':{'active':False}})))
        probe=NativeVoiceProbe(self.cfg,self.store,loop);probe.stage=0;probe.engine.synthesize=AsyncMock()
        with self.assertRaisesRegex(SessionStopped,'FOREGROUND_PLAYBACK_UNAVAILABLE'):await probe.decide(self.own_chat(),{},[])
        probe.engine.synthesize.assert_not_awaited()

    async def test_bridge_close_observes_audio_and_foreground_service_release(self):
        (self.cfg.data/'notification-bridge.private.json').write_text(json.dumps({'serial':self.cfg.serial,'token':'a'*64}))
        phone=SimpleNamespace(_adb=AsyncMock());bridge=NotificationBridge(self.cfg,phone);bridge.port=12345
        bridge.request=AsyncMock(side_effect=[{'active':False},{'audio_authorized':False,'speech':{'state':'idle'},'speech_playback_service':{'active':False}}])
        self.assertTrue(await bridge.close());self.assertIsNone(bridge.port)
        self.assertTrue(bridge.speech_playback_released);self.assertTrue(bridge.speech_audio_stopped)
        self.assertFalse(bridge.audio_volume_restore_pending)
        self.assertEqual(bridge.request.await_args_list[0].args,('DELETE','/session'))

    async def test_deferred_volume_restore_remains_visible_after_audio_has_stopped(self):
        (self.cfg.data/'notification-bridge.private.json').write_text(json.dumps({'serial':self.cfg.serial,'token':'a'*64}))
        bridge=NotificationBridge(self.cfg,SimpleNamespace(_adb=AsyncMock()));bridge.port=12345
        bridge.request=AsyncMock(side_effect=[{'active':False},{'audio_authorized':False,'speech':{'state':'idle'},
            'speech_playback_service':{'active':False,'volume_restore_pending':True}}])
        await bridge.close()
        self.assertTrue(bridge.speech_audio_stopped);self.assertTrue(bridge.audio_volume_restore_pending)
