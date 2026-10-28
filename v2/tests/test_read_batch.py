import asyncio,tempfile,unittest
from pathlib import Path
from digitallife_v2.phone import PhoneController
from digitallife_v2.config import IDENTITY
from digitallife_v2.safety import Blocked
from test_m1 import config


class FreshReadBatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.cfg=config(Path(self.tmp.name));self.phone=PhoneController(self.cfg,Path(self.tmp.name))

    async def test_identity_reads_all_original_properties_fresh_and_concurrently(self):
        active=maximum=0;calls=[]
        async def read(*command):
            nonlocal active,maximum
            calls.append(command)
            if command==('get-state',):return 'device'
            active+=1;maximum=max(maximum,active)
            await asyncio.sleep(.01);active-=1
            return self.cfg.identity[command[-1]]
        self.phone._text=read
        for _ in range(2):self.assertEqual(await self.phone.identity(),self.cfg.identity)
        self.assertEqual(maximum,len(IDENTITY));self.assertEqual(len(calls),2*(len(IDENTITY)+1))

    async def test_failure_cancels_and_drains_other_reads(self):
        stopped=[]
        async def read(*command):
            if command==('fail',):await asyncio.sleep(.01);raise Blocked('DEVICE_OFFLINE')
            try:await asyncio.sleep(60)
            finally:stopped.append(command)
        self.phone._text=read
        with self.assertRaisesRegex(Blocked,'DEVICE_OFFLINE'):
            await self.phone._read_batch(('slow',),('fail',),('also-slow',))
        self.assertEqual(set(stopped),{('slow',),('also-slow',)})
