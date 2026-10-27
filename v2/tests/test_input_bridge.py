import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
from digitallife_v2.input_bridge import InputBridge, IME
from digitallife_v2.safety import Blocked


class InputBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_after_switch_restores_only_owned_keyboard_and_releases_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); original='com.example.keyboard/.Keyboard'
            selected=[original]
            async def read(*args):return selected[0]
            async def adb(*args):
                if args[:3]==('shell','ime','set'):selected[0]=args[3]
                return b''
            async def guarded(task):return await task
            events=[]
            loop=SimpleNamespace(config=SimpleNamespace(data=root,serial='fixture'),directory=root,
                bridge=object(),phone=SimpleNamespace(_text=read,_adb=adb),guarded=guarded,
                check_stop=lambda:None,store=SimpleNamespace(event=lambda *args:events.append(args),update=lambda **kwargs:None))
            controller=InputBridge(loop)
            with self.assertRaisesRegex(Blocked,'TEST_STOP'):
                async with controller.temporary():
                    self.assertEqual(selected[0],IME)
                    self.assertTrue(controller.recovery.exists())
                    raise Blocked('TEST_STOP')
            self.assertEqual(selected[0],original)
            self.assertFalse(controller.recovery.exists())
            async with controller.temporary():selected[0]='com.user.selected/.Keyboard'
            self.assertEqual(selected[0],'com.user.selected/.Keyboard')

    async def test_existing_draft_rejected_without_bridge_request_or_input(self):
        from dataclasses import replace
        from test_m1 import observation
        obs=observation();obs.controls[0]=replace(obs.controls[0],kind='android.widget.EditText',focused=True,text='user draft')
        request=AsyncMock();send=AsyncMock()
        loop=SimpleNamespace(config=SimpleNamespace(data=Path('.')),bridge=SimpleNamespace(request=request),send_operation=send)
        with self.assertRaisesRegex(Blocked,'EXISTING_DRAFT'):
            await InputBridge(loop).insert(obs,'outbound','你好')
        request.assert_not_awaited();send.assert_not_awaited()
