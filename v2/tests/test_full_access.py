from dataclasses import replace
from io import BytesIO
import json
import os
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from PIL import Image

from digitallife_v2.brain import LifeBrain
from digitallife_v2.config import Config
from digitallife_v2.perception import parse_xml
from digitallife_v2.phone import PhoneController, fixed_text_argument
from digitallife_v2.safety import Blocked, DeviceLock, guard, parse_decision, rebind
from digitallife_v2.session import SessionLoop
from digitallife_v2.store import LifeStore
from test_m1 import config, observation, decision, FixturePhone, FixtureBrain


class FullAccessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.config = config(self.root)
        self.config.full_access = True

    def test_full_access_is_default_and_profile_loader_honors_it(self):
        self.assertTrue(Config(self.config.serial, self.config.identity).full_access)
        p = self.root / 'profile.json'
        p.write_text(json.dumps({'serial': self.config.serial, 'identity': self.config.identity}), encoding='utf-8')
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(Config.load(str(p)).full_access)
        with patch.dict(os.environ, {'DIGITALLIFE_V2_FULL_ACCESS': 'false'}, clear=True):
            self.assertFalse(Config.load(str(p)).full_access)

    def test_full_access_accepts_named_side_effects_and_unknown_clickable_roles(self):
        a = {'type': 'tap', 'control_id': 'one:0'}
        for text in ('允许', '修改权限', '保存', '删除', '付款', '发送', '登录', '不明按钮'):
            before = observation()
            before.controls[0] = replace(before.controls[0], text=text, resource='unknown')
            fresh = replace(before, observation_id='two', controls=[replace(before.controls[0], control_id='two:0')])
            self.assertEqual(rebind(a, before, fresh, {}, full_access=True).control_id, 'two:0')
        for changes in ({'visible': False}, {'enabled': False}, {'covered': True}, {'package': 'com.other'}):
            obs = observation(); obs.controls[0] = replace(obs.controls[0], **changes)
            with self.assertRaises(Blocked):
                guard(a, obs, {}, full_access=True)
        with self.assertRaises(Blocked):
            rebind(a, observation(), observation('two', foreground='com.other/.Main'), {}, full_access=True)

    async def test_sensitive_and_overview_content_are_preserved_and_image_is_offered(self):
        self.config.vision = True
        phone = PhoneController(self.config, self.root / 'screens')
        phone.identity = AsyncMock()
        phone.scene = AsyncMock(return_value='com.android.launcher/.Launcher')
        phone._recent_apps_requested = True
        xml = ('<?xml version="1.0"?><hierarchy><node package="com.android.launcher" '
               'resource-id="overview_password_editor" class="android.widget.EditText" password="true" '
               'text="private preview" focused="true" enabled="true" clickable="true" bounds="[100,200][600,800]"/></hierarchy>')
        self.assertTrue(parse_xml(xml, 'strict')[2])
        phone._text = AsyncMock(return_value=xml)
        raw = BytesIO(); Image.new('RGB', (720,1600)).save(raw, format='PNG')
        phone._adb = AsyncMock(return_value=raw.getvalue())
        obs = await phone.observe()
        self.assertFalse(obs.redacted)
        self.assertTrue(obs.vision_allowed)
        self.assertIsNone(obs.image_withheld_reason)
        self.assertEqual(obs.controls[0].text, 'private preview')
        self.assertTrue(Path(obs.png_path).exists())
        directory = self.root / 'session'; directory.mkdir()
        store = LifeStore(self.config.data, str(directory), self.config.serial)
        self.addCleanup(store.close)
        payload = LifeBrain(self.config, store).payload(obs, {}, [])
        content = payload['messages'][1]['content']
        self.assertIsInstance(content, list)
        self.assertTrue(content[1]['image_url']['url'].startswith('data:image/png;base64,'))
        data = json.loads(content[0]['text'])
        self.assertEqual(data['operation_mode'], 'full_access')
        self.assertIn('type_text', data['observation']['controls'][0]['allowed_actions'])
        self.assertNotIn('不能支付', payload['messages'][0]['content'])

    def test_permission_window_is_operable_but_unrelated_overlay_still_blocks(self):
        phone = PhoneController(self.config, self.root)
        phone.last_windows = 'Window #1 Window{abc com.android.permissioncontroller/.GrantPermissionsActivity}\n isOnScreen()=true isVisible()=true ty=1'
        phone.check_windows('com.android.permissioncontroller/.GrantPermissionsActivity')
        phone.last_windows += '\nWindow #0 Window{def com.overlay/.Main}\n isOnScreen()=true isVisible()=true ty=2038'
        with self.assertRaises(Blocked):
            phone.check_windows('com.android.permissioncontroller/.GrantPermissionsActivity')

    def test_general_input_requires_focused_current_editor_and_literal_supported_text(self):
        action = {'type':'type_text', 'control_id':'one:0', 'text': 'name@example.com; $(literal)'}
        self.assertEqual(parse_decision(json.dumps(decision(action)))['action'], action)
        obs = observation()
        obs.controls[0] = replace(obs.controls[0], resource='message_input', kind='android.widget.EditText', focused=True)
        self.assertIsNotNone(guard(action, obs, {}, full_access=True))
        with self.assertRaises(Blocked): guard(action, obs, {}, full_access=False)
        obs.controls[0] = replace(obs.controls[0], focused=False)
        with self.assertRaises(Blocked): guard(action, obs, {}, full_access=True)
        for text in ('中文', 'line\nbreak', 'literal%s', 'x'*161):
            with self.assertRaises(Blocked): parse_decision(json.dumps(decision({**action,'text':text})))

    async def test_general_input_shell_quoting_cannot_break_into_another_command(self):
        phone = PhoneController(self.config, self.root)
        phone.recheck_search = AsyncMock()
        phone._adb = AsyncMock()
        obs = observation(); target = obs.controls[0]
        text = "a';reboot;$(id) `id` @example.com"
        command = await phone.prepare_input_text(obs, {'text':text}, target)
        self.assertEqual(shlex.split(command[-1]), [text.replace(' ','%s')])
        await phone.dispatch(command)
        phone._adb.assert_awaited_once_with(*command, dispatch=True)
        self.assertFalse(fixed_text_argument("'a';reboot;'"))
        with self.assertRaises(Blocked): await phone.dispatch(('shell','input','text',"'a';reboot;'"))

    async def test_type_text_records_two_inputs_and_never_implicitly_submits(self):
        class EditorPhone(FixturePhone):
            async def observe(self):
                self.count += 1
                obs=observation(str(self.count))
                obs.controls[0]=replace(obs.controls[0],resource='message_input',kind='android.widget.EditText',
                    focused=True,text='new' if len(self.calls)==2 else 'old')
                return obs
            async def prepare_input_text(self, *args): return ('fixture','typed')
        class EditorBrain(FixtureBrain):
            async def decide(self, obs, apps, recent):
                self.count += 1
                return decision({'type':'type_text','control_id':obs.controls[0].control_id,'text':'new'}) if self.count==1 else decision({'type':'rest','seconds':120})
        original=DeviceLock.__init__
        def init(lock, serial): original(lock, serial); lock.root=self.root/'locks'
        directory=self.root/'run'
        with patch.object(DeviceLock,'__init__',init):
            result=await SessionLoop(self.config,directory,minutes=.04,phone_factory=EditorPhone,brain_factory=EditorBrain).run()
        self.assertEqual(result['status'],'completed_duration')
        events=[json.loads(x) for x in (directory/'events.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual([e['action']['type'] for e in events if e['kind']=='action_dispatched'], ['text_select','type_text'])
        self.assertGreaterEqual(sum(e['kind']=='observed' for e in events),4)


if __name__ == '__main__': unittest.main()
