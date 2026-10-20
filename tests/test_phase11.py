import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from phonelife.audit import EXCERPT_LIMIT, response_audit
from phonelife.devices import MockPhone, DeviceTransportError, mock_png
from phonelife.llm import APIError, ChatAPI, MockPlanner, ModelOutputError, parse_object
from phonelife.loop import PhoneLife
from phonelife.models import Action, ModelResponse, ScreenObservation, ValidationError
from phonelife.policy import ActionPolicy, validate_coordinates
from phonelife.replay import replay_task
from phonelife.storage import Store
from phonelife.verification import AboutPhoneVerifier, IndependentVisionVerifier

FINISH = '{"type":"finish","outcome":"uncertain","reason":"stop"}'
ACHIEVED = '{"type":"finish","outcome":"achieved","reason":"done"}'
TAP = '{"type":"tap","x":10,"y":20,"reason":"test"}'
HOME = '{"type":"home","reason":"test"}'
ABOUT_XML = '''<hierarchy><node package="com.android.settings">
  <node text="About phone" resource-id="com.android.settings:id/collapsing_toolbar"/>
  <node><node text="Android version" resource-id="android:id/title"/>
  <node text="15" resource-id="android:id/summary"/></node>
</node></hierarchy>'''
SEARCH_XML = '''<hierarchy><node package="com.google.android.settings.intelligence">
  <node text="About phone" resource-id="android:id/title"/>
  <node text="Android version"/><node text="15"/>
  <node class="android.widget.EditText" resource-id="search_src_text"/>
</node></hierarchy>'''


class SequenceGui:
    model = "fixture"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def propose(self, goal, observation, previous, feedback=None):
        self.calls.append((observation.sha256, len(previous), feedback))
        item = self.responses.pop(0) if self.responses else FINISH
        if isinstance(item, BaseException):
            raise item
        return ModelResponse(item, f"request-{len(self.calls)}", "fixture", self.model)


class FixturePhone(MockPhone):
    async def hierarchy(self):
        return ABOUT_XML

    async def foreground(self):
        return 'com.android.settings/.Settings$MyDeviceInfoActivity'


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'state.db'
        self.store = Store(self.path)
        self.phone = FixturePhone()
        self.gui = None

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def life(self, responses, phone=None, *, verifier=None, kind='mock', steps=6):
        self.phone = phone or self.phone
        self.gui = SequenceGui(responses)
        return PhoneLife(self.store, kind, self.phone, MockPlanner(), self.gui,
            ActionPolicy(kind, frozenset({'com.android.chrome'})), step_limit=steps,
            action_delay=0, verifier=verifier)

    def run_target(self, responses, **kwargs):
        life = self.life(responses, **kwargs)
        result = asyncio.run(life.execute_goal('打开关于手机'))
        return result, self.store.task(result.task_run_id)

    def test_p11_01_single_action(self):
        action = Action.from_dict(parse_object(TAP, allow_fences=False))
        self.assertEqual((action.x, action.y), (10, 20))

    def test_p11_02_trailing_prose_never_executes(self):
        result, task = self.run_target([TAP+' explanation'] * 3)
        self.assertEqual(result.reason_code, 'MODEL_OUTPUT_INVALID')
        self.assertEqual(self.phone.actions, [])
        self.assertEqual(len(task['attempts']), 3)

    def test_p11_03_multiple_objects_bounded(self):
        result, task = self.run_target([TAP+HOME] * 3)
        self.assertEqual(result.status, 'failed')
        self.assertEqual(len(self.gui.calls), 3)
        self.assertEqual(self.phone.actions, [])

    def test_p11_04_arrays_and_wrong_types(self):
        for raw in ('[]', 'null', '42', '"tap"', '{"x":NaN}', '{"x":1e999}', '{"x":1,"x":2}', '```json\n'+TAP+'\n```'):
            with self.subTest(raw=raw), self.assertRaises(ModelOutputError):
                parse_object(raw, allow_fences=False)

    def test_p11_05_missing_swipe_field(self):
        raw = '{"type":"swipe","x":100,"y":200,"x2":100,"duration_ms":400,"reason":"scroll"}'
        result, task = self.run_target([raw, raw])
        self.assertEqual(result.reason_code, 'INVALID_COORDINATE')
        self.assertEqual(len(self.gui.calls), 2)
        self.assertEqual(self.phone.actions, [])
        self.assertEqual(task['attempts'][0]['payload']['field_errors'][0]['field'], 'y2')

    def test_p11_06_actual_pixel_bounds_not_clamped(self):
        screen = ScreenObservation.from_png(mock_png(), 'fixture')
        for name, value in [('x', 720), ('y', 1600), ('x', -1), ('y', '10'), ('x', True), ('y', None)]:
            wire = {'type':'tap', 'x':10, 'y':20, 'reason':'test'}; wire[name]=value
            with self.subTest(name=name, value=value), self.assertRaises(ValidationError) as caught:
                action = Action.from_dict(wire)
                validate_coordinates(action, screen)
            self.assertEqual(caught.exception.errors[0]['field'], name)
        raw = '{"type":"tap","x":720,"y":20,"reason":"test"}'
        result, task = self.run_target([raw, raw])
        self.assertEqual(result.status, 'failed'); self.assertEqual(self.phone.actions, [])
        self.assertEqual(task['attempts'][0]['payload']['candidate_action']['x'], 720)

    def test_p11_07_rotation_invalidates_pre_rotation_action(self):
        class RotatingPhone(FixturePhone):
            observations = 0
            async def observe(self):
                self.observations += 1
                size = (720,1600) if self.observations == 1 else (1600,720)
                return ScreenObservation.from_png(mock_png(*size), 'rotating')
        phone = RotatingPhone()
        result, task = self.run_target([TAP, HOME, FINISH], phone=phone)
        self.assertEqual([a.type for a in phone.actions], ['home'])
        self.assertEqual(task['attempts'][0]['payload']['field_errors'][0]['code'], 'STALE_OBSERVATION')
        self.assertEqual(result.status, 'uncertain')

    def test_p11_08_format_repair_executes_once(self):
        result, task = self.run_target([TAP+HOME, TAP, FINISH])
        self.assertEqual(result.steps, 1)
        self.assertEqual(len(self.phone.actions), 1)
        self.assertEqual(self.gui.calls[0][0], self.gui.calls[1][0])
        self.assertEqual(self.gui.calls[1][1], 0)
        self.assertEqual([a['generation_attempt'] for a in task['attempts']], [1,2,1])

    def test_p11_09_three_invalid_generations_fail_and_back_off(self):
        before=time.time()
        result, task = self.run_target(['bad']*4)
        self.assertEqual(result.status, 'failed'); self.assertEqual(len(self.gui.calls), 3)
        self.assertGreaterEqual(self.store.next_wake('mock', run_kind='manual_targeted'), before+600)
        self.assertEqual(task['status'], 'failed')
        self.assertEqual(self.store.conn.execute('SELECT COUNT(*) FROM lease').fetchone()[0], 0)

    def test_p11_10_uncertain_finish_is_never_success(self):
        result, task = self.run_target([FINISH], verifier=AboutPhoneVerifier(self.phone))
        self.assertEqual(result.status, 'uncertain')
        self.assertEqual(result.verification.verdict, 'uncertain')

    def test_p11_11_search_page_is_not_about_phone(self):
        class SearchPhone(FixturePhone):
            async def hierarchy(self): return SEARCH_XML
            async def foreground(self): return 'com.google.android.settings.intelligence/.SearchActivity'
        phone=SearchPhone()
        result, task = self.run_target([ACHIEVED], phone=phone, verifier=AboutPhoneVerifier(phone))
        self.assertEqual(result.status, 'uncertain')
        self.assertEqual(result.verification.verdict, 'not_met')

    def test_p11_12_independent_about_phone_evidence(self):
        result, task = self.run_target([ACHIEVED], verifier=AboutPhoneVerifier(self.phone))
        self.assertEqual(result.status, 'succeeded')
        self.assertIn('field=Android version; value=15', result.verification.evidence)
        self.assertEqual(result.final_observation_sha256, result.verification.observation_sha256)
        self.assertEqual(task['result']['verification']['verdict'], 'verified')

    def test_p11_13_policy_denial_no_regeneration(self):
        raw='{"type":"text","text":"password123","reason":"test"}'
        result, task=self.run_target([raw, TAP])
        self.assertEqual(result.status,'blocked'); self.assertEqual(self.phone.actions,[])
        self.assertEqual(len(self.gui.calls),1)
        result, task=self.run_target([TAP],kind='real')
        self.assertEqual(result.status,'blocked'); self.assertEqual(len(self.gui.calls),1)

    def test_p11_14_transport_unknown_reobserves_without_resend(self):
        class TimeoutPhone(FixturePhone):
            dispatches=0; observed=0
            async def observe(self):
                self.observed+=1
                return await super().observe()
            async def act(self,action):
                self.dispatches+=1
                raise DeviceTransportError('timeout')
        phone=TimeoutPhone()
        result, task=self.run_target([TAP,TAP],phone=phone)
        self.assertEqual(result.reason_code,'ADB_OUTCOME_UNKNOWN')
        self.assertEqual(phone.dispatches,1); self.assertGreaterEqual(phone.observed,3)
        self.assertEqual(task['attempts'][0]['payload']['execution_status'],'uncertain_after_transport_error')

    def test_p11_15_targeted_exception_persists_and_releases(self):
        result, task=self.run_target([TAP, RuntimeError('fixture failure')])
        self.assertEqual(result.status,'failed'); self.assertEqual(result.steps,1)
        self.assertEqual(task['status'],'failed'); self.assertIsNotNone(task['result'])
        self.assertEqual(self.store.conn.execute('SELECT COUNT(*) FROM lease').fetchone()[0],0)

    def test_p11_16_legacy_database_rows_unchanged(self):
        path=Path(self.tmp.name)/'legacy.db'
        conn=sqlite3.connect(path)
        conn.executescript('''
          CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT, device TEXT NOT NULL,event_type TEXT NOT NULL,payload TEXT NOT NULL,created_at REAL NOT NULL);
          CREATE TABLE memories(id INTEGER PRIMARY KEY AUTOINCREMENT,device TEXT NOT NULL,kind TEXT NOT NULL,summary TEXT NOT NULL,created_at REAL NOT NULL);
          CREATE TABLE schedule(device TEXT PRIMARY KEY,next_wake_at REAL NOT NULL DEFAULT 0,last_result TEXT NOT NULL DEFAULT '');
          CREATE TABLE lease(device TEXT PRIMARY KEY,owner TEXT NOT NULL,expires_at REAL NOT NULL);
          INSERT INTO events VALUES(18,'emulator','outcome','{"status":"completed"}',1234.5);
          INSERT INTO memories VALUES(1,'emulator','episode','legacy memory',1234.5);
          INSERT INTO schedule VALUES('emulator',1234.5,'completed');
        ''')
        old=conn.execute('SELECT * FROM events').fetchall();conn.close()
        migrated=Store(path)
        try:
            self.assertEqual([tuple(r) for r in migrated.conn.execute('SELECT * FROM events')],old)
            self.assertEqual(migrated.conn.execute('SELECT last_result FROM schedule').fetchone()[0],'completed')
            self.assertEqual(migrated.interpret_legacy_status('completed'),'legacy_unverified')
            self.assertEqual(migrated.conn.execute('SELECT COUNT(*) FROM task_runs').fetchone()[0],0)
        finally: migrated.close()

    def test_p11_17_api_failure_categories_and_backoff(self):
        fixtures=[(urllib.error.HTTPError('https://provider',429,'rate',{},None),'API_RATE_LIMIT'),
                  (urllib.error.HTTPError('https://provider',503,'server',{},None),'API_SERVER_ERROR'),
                  (urllib.error.URLError(TimeoutError()),'API_TIMEOUT')]
        for fault,code in fixtures:
            with self.subTest(code=code),patch('urllib.request.urlopen',side_effect=fault):
                with self.assertRaises(APIError) as caught:
                    asyncio.run(ChatAPI('https://provider','fixture').generate('m','JSON','JSON'))
                self.assertEqual(caught.exception.code,code)
            result,task=self.run_target([APIError(code,'test')])
            self.assertEqual(result.reason_code,code)
            self.assertGreater(self.store.next_wake('mock', run_kind='manual_targeted'),time.time()+590)
            self.assertEqual(len(self.gui.calls),1)

    def test_p11_18_audit_redacts_and_limits(self):
        secret='sk-fixture-secret-token-1234567890'
        raw=json.dumps({'type':'text','text':secret,'reason':'Cookie=session-private; user@example.com 13812345678'})
        with patch.dict(os.environ,{'PHONE_LIFE_API_KEY':secret}):
            result,task=self.run_target([raw,FINISH])
            stored=self.path.read_bytes()
            self.assertNotIn(secret.encode(),stored)
            audit=json.dumps(task,ensure_ascii=False)
            for value in (secret,'user@example.com','13812345678','session-private'):
                self.assertNotIn(value,audit)
            excerpt=response_audit('{bad '+secret+' '+('x'*10000))['model_response_excerpt']
            self.assertNotIn(secret,excerpt); self.assertLessEqual(len(excerpt),EXCERPT_LIMIT)

    def test_p11_19_separate_process_and_force_cannot_take_lease(self):
        self.assertTrue(self.store.acquire('mock','parent'))
        command='from pathlib import Path; from phonelife.storage import Store; import sys; s=Store(Path(sys.argv[1])); print(s.acquire("mock","child")); s.close()'
        env=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parents[1]/'src'))
        child=subprocess.run([sys.executable,'-c',command,str(self.path)],capture_output=True,text=True,env=env,timeout=20)
        self.assertEqual(child.returncode,0,child.stderr); self.assertEqual(child.stdout.strip(),'False')
        life=self.life([TAP])
        self.assertEqual(asyncio.run(life.run_once(force=True))['status'],'busy')
        self.assertEqual(self.gui.calls,[])
        self.store.release('mock','parent')

    def test_p11_20_replay_is_always_offline(self):
        result,task=self.run_target([TAP,FINISH])
        count=len(self.phone.actions)
        with patch('phonelife.devices.AdbPhone._adb',side_effect=AssertionError('No ADB in replay')):
            replay=replay_task(self.store,result.task_run_id)
        self.assertEqual(replay['mode'],'dry-run'); self.assertEqual(replay['adb_actions_sent'],0)
        self.assertEqual(len(self.phone.actions),count)
        self.assertTrue(all(a['device_execution']=='disabled' for a in replay['attempts']))

    def test_unknown_fields_and_coercions_rejected(self):
        for change in ({'x':'10'},{'x':True},{'x':None},{'extra':1},{'reason':42}):
            wire=json.loads(TAP);wire.update(change)
            with self.subTest(change=change),self.assertRaises(ValidationError):Action.from_dict(wire)

    def test_png_header_and_bounds(self):
        good=mock_png(720,1600)
        screen=ScreenObservation.from_png(good,'fixture')
        self.assertEqual((screen.width,screen.height),(720,1600))
        for bad in (b'not png',good[:29]+b'\0\0\0\0'+good[33:]):
            with self.assertRaises(ValueError):ScreenObservation.from_png(bad,'fixture')

    def test_rest_has_distinct_status_and_no_gui_calls(self):
        self.store.remember('mock','episode','already explored')
        life=self.life([TAP])
        result=asyncio.run(life.run_once(force=True))
        self.assertEqual(result['status'],'rested');self.assertEqual(self.gui.calls,[])

    def test_step_budget_does_not_become_success(self):
        result,task=self.run_target([TAP,HOME],steps=2)
        self.assertEqual(result.status,'step_limit');self.assertEqual(result.steps,2)

    def test_no_fake_success_if_about_title_is_only_search_result(self):
        result=AboutPhoneVerifier.inspect(SEARCH_XML,'com.android.settings/.SubSettings','hash')
        self.assertNotEqual(result.verdict,'verified')

    def test_independent_visual_fallback_separate_request(self):
        class ReadOnlyAPI:
            calls=0
            async def generate(self,model,system,user,image,temperature):
                self.calls+=1
                return ModelResponse(json.dumps({'verdict':'verified','page_title':'About emulated device',
                    'field_name':'Android version','field_value':'15','reason':'visible'}),'verify-id','fixture','fixture')
        class NoTreePhone(FixturePhone):
            async def hierarchy(self):raise RuntimeError('could not get idle state')
        api=ReadOnlyAPI();phone=NoTreePhone()
        verifier=AboutPhoneVerifier(phone,IndependentVisionVerifier(api,'fixture'))
        result,task=self.run_target([ACHIEVED],phone=phone,verifier=verifier)
        self.assertEqual(result.status,'succeeded');self.assertEqual(api.calls,1)
        self.assertEqual(result.verification.evidence_type,'independent_vision')

    def test_visual_claim_on_search_title_cannot_succeed(self):
        class API:
            async def generate(self,*args,**kwargs):
                return ModelResponse(json.dumps({'verdict':'verified','page_title':'Search settings',
                    'field_name':'Android version','field_value':'15','reason':'looks done'}))
        verifier=IndependentVisionVerifier(API(),'fixture')
        screen=ScreenObservation.from_png(mock_png(),'fixture')
        result=asyncio.run(verifier.verify('关于手机',screen,[]))
        self.assertEqual(result.verdict,'uncertain')

    def test_visual_invalid_output_has_own_bounded_budget(self):
        class API:
            calls=0
            async def generate(self,*args,**kwargs):
                self.calls+=1;return ModelResponse('bad')
        api=API();screen=ScreenObservation.from_png(mock_png(),'fixture')
        result=asyncio.run(IndependentVisionVerifier(api,'fixture').verify('关于手机',screen,[]))
        self.assertEqual(api.calls,2);self.assertEqual(result.api_calls,2)
        self.assertEqual(result.verdict,'uncertain')

    def test_json_mode_only_enabled_for_known_provider_or_explicit_selection(self):
        for url,enabled in [('https://api.deepseek.com',True),('https://other.example/v1',False)]:
            api=ChatAPI(url,'fixture');payloads=[]
            def request(payload):
                payloads.append(payload)
                return {'choices':[{'message':{'content':'{"ok":true}'},'finish_reason':'stop'}]}
            api._request=request
            asyncio.run(api.generate('fixture','return JSON','JSON'))
            self.assertEqual('response_format' in payloads[0],enabled)

    def test_identifier_redaction_preserves_sha256(self):
        from phonelife.audit import redact
        digest='abcdef12345678901234'+('a'*44)
        self.assertEqual(len(digest),64)
        self.assertEqual(redact(digest),digest)
        self.assertNotIn('867400022047199',redact('IMEI 867400022047199'))

    def test_emulator_label_cannot_expand_physical_phone_permissions(self):
        from phonelife.devices import AdbPhone
        phone=AdbPhone('physical-fixture','emulator')
        async def adb(*args,**kwargs):return '0'
        phone._adb=adb
        with self.assertRaises(ValueError):asyncio.run(phone.observe())


if __name__=='__main__': unittest.main()
