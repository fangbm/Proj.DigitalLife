"""offline_fixture: minimal geometries from first-run evidence, synthetic metadata.

No original PNG, UI tree, account, serial or private SQLite is a test dependency.
The historical WMS dumps were NOT saved; these demonstrate guard failure modes,
not a claim that a particular foreign window was present in the real session.
"""
import asyncio
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import test_app_exploration as fx
from phonelife import app_exploration as a,dedicated_device as d
from phonelife.models import Action
from phonelife.policy import PolicyDenied
from phonelife.request_context import compact_context,compact_gui,memory_view
from phonelife.life_models import LifeContext
from phonelife.storage import Store
from phonelife.memory import LifeMemory
from phonelife.life_storage import enable_life
from phonelife.verification_registry import GoalVerifierRegistry

APP='org.example.reader'
def windows(hidden=True,overlay=False,above=False):
    focus='Window #1 Window{a u0 '+APP+'/.Main}\npackage='+APP+' ty=1\nmViewVisibility=0x0 mHasSurface=true\nmFrame=[0,0][1240,2772]\n'
    other=('Window #0 Window{b u0 org.example.other/.Panel}\npackage=org.example.other ty='+('2038' if overlay else '1')+
        '\nmViewVisibility=0x0 mHasSurface=true\nmFrame=[0,0][1240,2772]\nisOnScreen='+('false' if hidden else 'true')+
        ' isVisible='+('false' if hidden else 'true')+' mObscured=false\n')
    if not above:focus=focus.replace('#1','#0');other=other.replace('#0','#1')
    return other+focus if above else focus+other

class OcclusionTests(unittest.TestCase):
    def test_real_swipe_geometry_retained_background_surface_is_not_occlusion(self):
        action=Action('swipe',x=620,y=1920,x2=620,y2=882,duration_ms=400)
        metadata={'windows':windows(),'input':''}
        old=object.__new__(d.DedicatedPhone);old._window_metadata=metadata;old._observed_package=APP
        with self.assertRaisesRegex(PolicyDenied,'TARGET_OCCLUSION_OBSERVED'):old._check_touch_blockers(action)
        self.assertEqual(a.browsing_occlusion(metadata,action,APP)['reason_code'],'NO_CONCRETE_TOUCH_BLOCKER')
    def test_real_tap_geometry_retained_background_surface_is_not_occlusion(self):
        action=Action('tap',x=988,y=333)
        self.assertEqual(a.browsing_occlusion({'windows':windows(),'input':''},action,APP)['reason_code'],'NO_CONCRETE_TOUCH_BLOCKER')
    def test_live_overlay_and_foreground_foreign_window_still_deny(self):
        for text in (windows(False,True,True),windows(False,False,True)):
            audit=a.browsing_occlusion({'windows':text,'input':''},Action('tap',x=988,y=251),APP)
            self.assertEqual(audit['reason_code'],'TARGET_OCCLUSION_OBSERVED');self.assertEqual(len(audit['windows_sha256']),64)
            self.assertNotIn('org.example.other',json.dumps(audit))
    def test_overlay_crossing_swipe_middle_and_drop_input_still_deny(self):
        text=windows(False,True,True).replace('mFrame=[0,0][1240,2772]','mFrame=[0,1200][1240,1400]')
        action=Action('swipe',x=620,y=1920,x2=620,y2=882,duration_ms=400)
        self.assertEqual(a.browsing_occlusion({'windows':text,'input':''},action,APP)['reason_code'],'TARGET_OCCLUSION_OBSERVED')
        self.assertEqual(a.browsing_occlusion({'windows':'','input':'DROP_INPUT touchableRegion=[0,0][1240,2772]'},action,APP)['reason_code'],'TARGET_TOUCH_BLOCKER_OBSERVED')

class ExecutionFixTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.h=fx.AppsTests();self.h.setUp()
        def close_helper():
            while self.h._cleanups:
                function,args,kwargs=self.h._cleanups.pop();function(*args,**kwargs)
        self.addCleanup(close_helper)
    async def test_real_search_toolbar_geometry_uses_centre_inside_control(self):
        self.h.adb.package=APP;p,o,s=await self.h.semantic();row=next(c for c in s.controls if c.clickable)
        row=replace(row,bounds=(904,167,1072,335),resource_id=APP+':id/search',text='',content_description='搜索',class_name='android.widget.Button')
        s=replace(s,screen_width=1240,screen_height=2772,controls=(row,))
        row=replace(row,parent=None,children=());s=replace(s,controls=(row,))
        action,b=p.capabilities.ground(self.h.candidate(s,'tap_control',control_id=row.control_id),s)
        self.assertEqual((action.x,action.y),(988,251))
    async def test_finish_uncertain_is_logical_end_not_input_or_success(self):
        result=await self.h.execute(lambda n,s:{'type':'finish','reason':'Target blocked','outcome':'uncertain'})
        self.assertEqual(result.reason_code,'EXECUTOR_UNCERTAIN');self.assertEqual(self.h.adb.inputs,[])
        s=Store.open_readonly(self.h.f.life);p=json.loads(s.conn.execute('SELECT payload FROM action_attempts').fetchone()[0]);s.close()
        self.assertTrue(p['logical_completion']);self.assertFalse(p['device_input_dispatched']);self.assertEqual(p['execution_status'],'not_executed')
    async def test_format_overlong_reason_repair_is_specific_and_bounded(self):
        def plan(n,s):
            if n==1:return {'type':'finish','reason':'x'*474,'outcome':'achieved'}
            self.assertIn('120 characters',self.h.used_gui.feedback[-1]['instruction'])
            self.assertEqual(self.h.used_gui.feedback[-1]['field_errors'][0]['actual_length'],474)
            return {'type':'finish','reason':'Read observed list','outcome':'uncertain'}
        await self.h.execute(plan);self.assertEqual(self.h.used_gui.calls,2);self.assertFalse(self.h.adb.inputs)
    async def test_pre_dispatch_rejection_feedback_allows_new_action_without_resend(self):
        def plan(n,s):
            if n==1:
                def blocked(action):
                    self.h.used_phone._check_touch_blockers=lambda action:None
                    raise a.SkippedAction('TARGET_OCCLUSION_OBSERVED')
                self.h.used_phone._check_touch_blockers=blocked
                # Match the real exception path's structured guard audit.
                self.h.used_phone.last_dispatch_guard={'reason_code':'TARGET_OCCLUSION_OBSERVED'}
                return self.h.candidate(s,'tap_control',control_id=next(c.control_id for c in s.controls if c.clickable))
            if n==2:
                self.assertEqual(self.h.used_gui.feedback[-1]['guard_reason_code'],'TARGET_OCCLUSION_OBSERVED')
                return self.h.candidate(s,package_name=fx.SECOND)
            return {'type':'finish','reason':'Observed another app','outcome':'uncertain'}
        result=await self.h.execute(plan)
        self.assertEqual(len(self.h.adb.inputs),1,result);self.assertEqual(self.h.adb.package,fx.SECOND)
        self.assertFalse(self.h.used_phone._unknown);self.assertEqual(result.status,'uncertain')
    async def test_redacted_placeholder_does_not_become_exploration_or_api_input(self):
        p,o,s=await self.h.semantic();p._redacted=True
        registry=GoalVerifierRegistry(p,api=None)
        self.assertEqual((await registry._observe({},o,[])).verdict,'uncertain')
        self.assertEqual((await registry._visual({},o,[])).verdict,'uncertain')
    async def test_overlay_arriving_on_last_window_refresh_denies_before_input(self):
        def plan(n,s):
            if n>1:return {'type':'finish','reason':'A new overlay blocks the target','outcome':'uncertain'}
            phone=self.h.used_phone;original=phone._scene
            async def late_overlay():
                scene=await original()
                if getattr(phone,'pre_action_semantic',None) is not None:
                    phone._window_metadata['windows']=windows(False,True,True)
                return scene
            phone._scene=late_overlay
            return self.h.candidate(s,'tap_control',control_id=next(c.control_id for c in s.controls if c.clickable))
        await self.h.execute(plan)
        self.assertFalse(self.h.adb.inputs);self.assertFalse(self.h.used_phone._unknown)
    async def test_native_negative_update_dialog_allowed_affirmative_denied(self):
        self.h.adb.package=APP;p,o,s=await self.h.semantic();root=s.controls[0];base=s.controls[-1]
        title=replace(base,control_id='title',text='Download update?',resource_id='android:id/alertTitle',clickable=False,parent=root.control_id,children=())
        no=replace(base,control_id='no',text='否',resource_id='android:id/button2',class_name='android.widget.Button',bounds=(662,1566,886,1755),parent=root.control_id,children=())
        yes=replace(no,control_id='yes',text='是',resource_id='android:id/button1',bounds=(886,1566,1110,1755))
        root=replace(root,bounds=(0,0,1240,2772),resource_id='android:id/parentPanel',children=('title','no','yes'))
        title=replace(title,bounds=(311,1213,1068,1306));s=replace(s,screen_width=1240,screen_height=2772,controls=(root,title,no,yes))
        p.capabilities.safe_target(s,no)
        with self.assertRaises(PolicyDenied):p.capabilities.safe_target(s,yes)
    async def test_ten_minute_session_uses_same_life_without_import_or_budget_reset(self):
        first=self.h.session()
        with patch.dict(os.environ,fx.ENV):
            second=d.create_session(self.h.f.root/'ten-minutes',profile_path=self.h.f.profile_path,life_db=self.h.f.life,lease_path=self.h.f.lease,seconds=600,app_scope='installed_launchable')
        self.assertEqual(second['duration_seconds'],600);self.assertEqual(second['config']['agent_db'],first['config']['agent_db'])
        with patch('phonelife.device_profiles.ReadOnlyAdb',side_effect=AssertionError('No device in Status')):
            stats=d.session_activity(self.h.f.root/'ten-minutes')
        self.assertEqual(stats['api_requests'],0);self.assertEqual(stats['cycles'],0)

class ContextFixTests(unittest.TestCase):
    def test_compaction_preserves_error_fields_source_refs_and_original_body(self):
        error={'reason_code':'MISSING_CONTINUATION_REFERENCE','field':'continuation_of','expected':'eligible ID',
            'source_task_run_id':'source','candidate_sha256':'a'*64,'executed':False,'confirmed_actions':0}
        memory={'memory_id':'m1','source_runs':['source'],'body':{'objective':{'status':'failed','decision_rejections':[error],'control_failures':['x'*20000]},'model_reflection':{'subjective_outcome':'x'*20000}}}
        before=json.dumps(memory);compact=memory_view(memory)
        self.assertEqual(compact['body']['objective']['decision_rejections'],[error]);self.assertEqual(compact['source_runs'],['source'])
        self.assertEqual(json.dumps(memory),before);self.assertLess(len(json.dumps(compact)),len(before)//10)
    def test_gui_inventory_single_schema_copy_and_bounded_action_history(self):
        request={'launch_allowlist':['org.a'],'action_schema':{'enum':['org.a']},'semantic_context':{'capabilities':{'installed_launchable_apps':['org.a']}},'previous_executed_actions':[{'reason':'x'*400}for _ in range(15)]}
        view=compact_gui(request);self.assertNotIn('launch_allowlist',view);self.assertEqual(view['action_schema'],request['action_schema'])
        self.assertEqual(len(view['previous_executed_actions']),6);self.assertEqual(len(request['previous_executed_actions']),15)
    def test_current_device_memory_outranks_emulator_without_rewriting_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(Path(tmp)/'memory.sqlite3');enable_life(s)
            for device in ('emulator','physical-current'):
                s.start_task(device,device,'offline',{},run_kind='autonomous')
                LifeMemory(s,device).add('episodic','same topic',{'objective':{'status':'uncertain'}},device,.8)
            memory=LifeMemory(s,'physical-current');memory.devices=('emulator','physical-current');memory.scope='device IN (?,?)'
            original=[tuple(r) for r in s.conn.execute('SELECT * FROM life_memories')]
            memory.environment_device='physical-current';items=memory.retrieve('same topic')
            self.assertEqual(items[0]['source_runs'],['physical-current']);self.assertEqual(len(items),2)
            self.assertEqual(original,[tuple(r) for r in s.conn.execute('SELECT * FROM life_memories')]);s.close()
