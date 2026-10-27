import asyncio
import base64
import hashlib
from io import BytesIO
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,patch
import wave
from digitallife_v2.audio import validate_wav,start,finish
from digitallife_v2.store import LifeStore
from test_m1 import config,observation


def wav(samples,rate=16000):
    target=BytesIO()
    with wave.open(target,'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(rate);w.writeframes(struct.pack('<'+'h'*len(samples),*samples))
    return target.getvalue()


class AudioTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.config=config(self.root)

    def test_audio_validation_rejects_silence_wrong_rate_and_overlong_capture(self):
        duration,rms=validate_wav(wav([500,-500]*8000));self.assertEqual(duration,1);self.assertEqual(rms,500)
        for raw in [wav([0]*16000),wav([500]*16000,8000),wav([500]*336000)]:
            with self.assertRaises(ValueError):validate_wav(raw)

    async def test_disabled_audio_never_contacts_bridge(self):
        bridge=SimpleNamespace(request=AsyncMock())
        value=await start(SimpleNamespace(config=self.config,bridge=bridge),observation(),20)
        self.assertFalse(value['available']);bridge.request.assert_not_awaited()

    async def test_missing_projection_is_unavailable_without_microphone_fallback(self):
        self.config.audio_enabled=True
        async def guarded(task):return await task
        bridge=SimpleNamespace(request=AsyncMock(return_value={'available':False,'reason':'projection_not_authorized'}))
        value=await start(SimpleNamespace(config=self.config,bridge=bridge,guarded=guarded),observation(),20)
        self.assertEqual(value['reason'],'projection_not_authorized');self.assertEqual(bridge.request.await_count,1)

    async def test_capture_source_and_bytes_are_bound_before_local_transcription(self):
        directory=self.root/'session';directory.mkdir();store=LifeStore(self.config.data,str(directory),self.config.serial,'fixture');self.addCleanup(store.close)
        raw=wav([500,-500]*8000);capture={'available':True,'capture_id':'00112233-4455-6677-8899-aabbccddeeff','source_package':'com.reader'}
        response={'state':'ready','capture_id':capture['capture_id'],'source_package':'com.reader','wav_base64':base64.b64encode(raw).decode()}
        async def guarded(task):return await task
        loop=SimpleNamespace(config=self.config,bridge=SimpleNamespace(request=AsyncMock(return_value=response)),guarded=guarded,directory=directory,store=store)
        with patch('digitallife_v2.audio.transcribe',AsyncMock(return_value={'available':True,'engine':'offline-test','language':'en','segments':[{'text':'test speech'}]})) as asr:
            value=await finish(loop,capture)
            self.assertTrue(value['available']);self.assertEqual(value['wav_sha256'],hashlib.sha256(raw).hexdigest());asr.assert_awaited_once()
        response['source_package']='com.foreign'
        with patch('digitallife_v2.audio.transcribe',AsyncMock()) as asr:
            value=await finish(loop,capture)
            self.assertFalse(value['available']);asr.assert_not_awaited()


if __name__=='__main__':unittest.main()
