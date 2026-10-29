import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from digitallife_v2.phone import PhoneController


class ReadDeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_tree_idle_wait_has_room_without_extending_other_adb_operations(self):
        phone=PhoneController(SimpleNamespace(adb='offline-adb',serial='fixture'),Path('unused'))
        timeouts=[]
        async def spawn(*args,**kwargs):
            return SimpleNamespace(stdout=SimpleNamespace(read=AsyncMock(side_effect=[b'offline fixture',b''])),
                stderr=SimpleNamespace(read=AsyncMock(return_value=b'')),wait=AsyncMock(return_value=0),returncode=0)
        async def wait(operation,timeout):timeouts.append(timeout);return await operation
        with patch('digitallife_v2.phone.asyncio.create_subprocess_exec',new=AsyncMock(side_effect=spawn)),patch('digitallife_v2.phone.asyncio.wait_for',new=AsyncMock(side_effect=wait)):
            await phone._adb('exec-out','uiautomator','dump','/proc/self/fd/1')
            await phone._adb('exec-out','screencap','-p')
            await phone._adb('exec-out','uiautomator','dump','/proc/self/fd/1',timeout=3)
        self.assertEqual(timeouts,[25,12,3])
