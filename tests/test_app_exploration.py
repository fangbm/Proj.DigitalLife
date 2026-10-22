"""offline_fixture: cross-app execution against synthetic ADB and temporary SQLite."""
import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from xml.sax.saxutils import quoteattr
from phonelife import app_exploration as a
from phonelife import dedicated_device as d
from phonelife.devices import mock_png,DeviceTransportError
from phonelife.models import Action,ModelResponse,ScreenObservation
from phonelife.storage import Store
from phonelife.longrun_state import SessionLedger
from phonelife.policy import ActionPolicy,PolicyDenied
from phonelife.physical_execution import LeaseNamespace
from phonelife.semantic import normalize,UIAutomatorBackend
from phonelife.loop import PhoneLife
from phonelife.llm import MockPlanner
from phonelife.verification_registry import GoalVerifierRegistry
from test_dedicated_device import Fixture,DedicatedAdb

ENV={'PHONE_LIFE_API_BASE':'https://example.invalid','PHONE_LIFE_API_KEY':'offline-placeholder',
     'PHONE_LIFE_BRAIN_MODEL':'offline','PHONE_LIFE_GUI_MODEL':'offline'}
APP='org.example.reader';SECOND='org.example.video';HOME='com.example.launcher'

def xml(package=APP,role='article_card',label='A public article',search=False,focused=True,extra='',password=False,text=''):
    def node(cls,bounds,children='',**attrs):
        props={'class':cls,'bounds':bounds,'package':package,'clickable':'false','scrollable':'false','enabled':'true',
            'checkable':'false','password':'false','long-clickable':'false'}|attrs
        return '<node '+' '.join(k+'='+quoteattr(v) for k,v in props.items())+'>'+children+'</node>'
    item=node('android.widget.EditText' if search else 'android.widget.LinearLayout','[0,400][720,560]',
        text=text if search else label,clickable='true',focused='true' if focused else 'false',password='true' if password else 'false',
        **{'resource-id':package+':id/'+('search_input' if search else role),'content-desc':'Search' if search else ''})
    body=node('androidx.recyclerview.widget.RecyclerView','[0,200][720,1500]',item+extra,scrollable='true',**{'resource-id':package+':id/feed'})
    return '<?xml version="1.0"?><hierarchy rotation="0">'+node('android.widget.FrameLayout','[0,0][720,1600]',body)+'</hierarchy>'

class AppsAdb(DedicatedAdb):
    def __init__(self):
        super().__init__();self.packages={APP,SECOND,HOME,'org.example.no_launcher','com.android.settings'}
        self.launchable={APP,SECOND,'com.android.settings'};self.package=HOME;self.inputs=[];self.fail=False
        self.page={};self.search_value='';self.version=0;self.resolve_override=None
    @property
    def windows(self):return 'mCurrentFocus=Window{abc u0 '+self.package+'/.Main}'
    @windows.setter
    def windows(self,v):pass
    async def run(self,serial,args):
        if args in {a.INSTALLED,a.LAUNCHERS}:
            self.calls.append((serial,args))
            return ('\n'.join('package:'+p for p in sorted(self.packages)) if args==a.INSTALLED else '\n'.join(p+'/.Main' for p in sorted(self.launchable))).encode()
        if args==('exec-out','uiautomator','dump','/proc/self/fd/1'):
            self.calls.append((serial,args));return xml(self.package,**self.page).encode()
        if args==('exec-out','screencap','-p'):
            self.calls.append((serial,args));return mock_png(720,1600,self.version)
        # DedicatedAdb replaces screenshots, so route remaining metadata directly.
        return await super().run(serial,args)
    async def resolve(self,serial,package):return self.resolve_override or package+'/.Main\n'
    async def dispatch(self,serial,args):
        self.inputs.append(args)
        if self.fail:raise OSError('synthetic transport interruption')
        if args[:3]==('shell','am','start'):self.package=args[-1].strip("'").split('/')[0];self.page={}
        if args[:3]==('shell','input','text'):
            self.search_value=args[3].strip("'").replace('%s',' ');self.page['text']=self.search_value
        if args==('shell','input','keyevent','66'):self.page={'role':'search_result','label':'Results for '+self.search_value}
        if args[:3]==('shell','input','tap'):self.page={'role':'article_card','label':'Article details'}
        self.version+=20
        return b''

class AppsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.f=Fixture(self.tmp.name);self.adb=AppsAdb()
    def session(self,directory=None,first=True):
        with patch.dict(os.environ,ENV):
            self.value=d.create_session(directory or self.f.session,profile_path=self.f.profile_path,life_db=self.f.life,
                source_db=self.f.source if first else None,lease_path=self.f.lease,seconds=3600,app_scope='installed_launchable')
        return self.value
    async def phone(self):
        value=self.session();l=SessionLedger(self.f.session/'soak-control.sqlite3');l.start();l.update(active_pid=os.getpid());l.close()
        return a.AllAppPhone(d.DedicatedContract(value['config'],value),self.adb)
    async def semantic(self,phone=None):
        phone=phone or await self.phone();obs=await phone.observe();s=await a.AllAppSemanticProvider().collect(phone,obs)
        return phone,obs,s
    def candidate(self,s,kind='open_app',**kwargs):return {'type':kind,'reason':'offline test','observation_id':s.observation_id,**kwargs}
    async def test_live_inventory_launchable_intersection_no_settings_start(self):
        p,o,s=await self.semantic()
        self.assertEqual(s.foreground_package,HOME);self.assertIn(APP,p.catalog.packages)
        self.assertNotIn('org.example.no_launcher',p.catalog.packages)
        action,b=p.capabilities.ground(self.candidate(s,package_name=APP),s)
        self.assertEqual(action.package,APP);self.assertEqual(b['capability'],'launch_allowed_app')
        self.assertEqual(self.value['config']['allowed_apps'],[])
        self.assertIsNone(self.value['config']['test_controller'])
    async def test_inventory_resolver_injection_removed_and_unlaunchable_denied(self):
        p,o,s=await self.semantic()
        for package in ('org.example.no_launcher','org.not.installed','org.example.reader;reboot','$(reboot)'):
            error=PolicyDenied if a.PACKAGE.fullmatch(package) else a.GroundingError
            with self.assertRaises(error):p.capabilities.ground(self.candidate(s,package_name=package),s)
        self.adb.resolve_override='org.other/.Main'
        with self.assertRaises(PolicyDenied):await p.catalog.resolve(self.adb,p.serial,APP)
        self.adb.packages.remove(APP)
        with self.assertRaises(PolicyDenied):await p.catalog.resolve(self.adb,p.serial,APP)
        self.assertEqual(self.adb.inputs,[])
    async def test_unknown_attributes_preserved_generic_navigation_and_scroll(self):
        self.adb.package=APP;p,o,s=await self.semantic()
        row=next(c for c in s.controls if c.clickable)
        self.assertIsNone(row.visible);self.assertIsNone(row.editable)
        action,b=p.capabilities.ground(self.candidate(s,'tap_control',control_id=row.control_id),s)
        self.assertEqual(action.type,'tap')
        region=next(c for c in s.controls if c.scrollable)
        action,b=p.capabilities.ground(self.candidate(s,'scroll_region',scrollable_region_id=region.control_id,direction='toward_end'),s)
        self.assertEqual(action.type,'swipe');self.assertGreater(action.y,action.y2)
    async def test_high_risk_effects_and_ambiguous_controls_denied(self):
        self.adb.package=APP;p=await self.phone()
        for role,label in [('send_button','Continue'),('pay_button','OK'),('follow','Profile'),('article_card','点赞'),('delete','Continue'),('permission','Continue'),('button','OK'),('item','OK')]:
            with self.subTest(role=role):
                self.adb.page={'role':role,'label':label};_,o,s=await self.semantic(p)
                if not s.controls:continue # explicitly redacted sensitive page
                row=next(c for c in s.controls if c.clickable)
                with self.assertRaises(a.SkippedAction):p.capabilities.ground(self.candidate(s,'tap_control',control_id=row.control_id),s)
        self.assertFalse(self.adb.inputs)
    async def test_write_effects_and_camel_case_resource_cannot_hide_as_navigation(self):
        self.adb.package=APP;p=await self.phone()
        for role in ('navigation_save','navigation_submit','navigation_confirm','btnSendMessage','navigation_enable','navigation_remove'):
            with self.subTest(role=role):
                self.adb.page={'role':role,'label':'Continue'};_,o,s=await self.semantic(p)
                row=next(c for c in s.controls if c.clickable)
                with self.assertRaises(a.SkippedAction):p.capabilities.ground(self.candidate(s,'tap_control',control_id=row.control_id),s)
        self.assertFalse(self.adb.inputs)
    async def test_state_changing_class_cannot_hide_behind_read_label(self):
        self.adb.package=APP;p,o,s=await self.semantic();row=next(c for c in s.controls if c.clickable)
        for altered in (replace(row,class_name='android.widget.Switch'),replace(row,checkable=True),replace(row,enabled=False),replace(row,password=True)):
            modified=replace(s,controls=tuple(altered if c==row else c for c in s.controls))
            with self.assertRaises(PolicyDenied):p.capabilities.safe_target(modified,altered)
    async def test_playback_control_allowed_and_account_state_control_denied(self):
        self.adb.package=SECOND;self.adb.page={'role':'player_play','label':'Play'};p,o,s=await self.semantic()
        row=next(c for c in s.controls if c.clickable);p.capabilities.safe_target(s,row)
        bad=replace(row,resource_id=SECOND+':id/favorite',text='Save')
        with self.assertRaises(PolicyDenied):p.capabilities.safe_target(replace(s,controls=tuple(bad if c==row else c for c in s.controls)),bad)
    async def test_search_requires_positive_role_focus_and_safe_query(self):
        self.adb.package=APP;self.adb.page={'search':True};p,o,s=await self.semantic();row=next(c for c in s.controls if c.clickable)
        self.assertTrue(p.capabilities.focused(s,row))
        action,b=p.capabilities.ground(self.candidate(s,'search_text',control_id=row.control_id,text='space exploration',submit=True),s)
        self.assertEqual(action.type,'text');self.assertEqual(b['dispatch_cost'],2)
        for query in ('中文','x;reboot','$(id)','https://example.org','password 123','13800138000','a@b.com'):
            with self.assertRaises(a.SkippedAction):p.capabilities.ground(self.candidate(s,'search_text',control_id=row.control_id,text=query,submit=True),s)
        unfocused=replace(s,raw={**s.raw,'_focused_paths':[]})
        with self.assertRaises(PolicyDenied):p.capabilities.ground(self.candidate(s,'search_text',control_id=row.control_id,text='space',submit=True),unfocused)
    async def test_standard_search_editor_long_click_capability_is_not_a_long_press(self):
        self.adb.package=APP;self.adb.page={'search':True};p,o,s=await self.semantic();row=next(c for c in s.controls if c.clickable)
        native=replace(row,class_name='android.widget.AutoCompleteTextView',long_clickable=True)
        current=replace(s,controls=tuple(native if c==row else c for c in s.controls))
        action,b=p.capabilities.ground(self.candidate(current,'search_text',control_id=native.control_id,text='space',submit=True),current)
        self.assertEqual(action.type,'text');self.assertEqual(b['dispatch_cost'],2)
        self.assertTrue(native.long_clickable);self.assertIsNone(native.editable)
    async def test_sensitive_form_redacts_before_screenshot_and_can_switch_away(self):
        self.adb.package=APP;self.adb.page={'password':True,'label':'secret never sent'};p,o,s=await self.semantic()
        self.assertEqual(s.failure_reason,'SENSITIVE_PAGE_REDACTED');self.assertEqual(s.controls,())
        self.assertFalse(any(args==('exec-out','screencap','-p') for _,args in self.adb.calls))
        self.assertFalse(p.capabilities.evidence_for_action(s,None));self.assertTrue(p.capabilities.view(s)['screen_redacted'])
        action,b=p.capabilities.ground(self.candidate(s,package_name=SECOND),s);self.assertEqual(action.package,SECOND)
        self.assertFalse(list(p.capture_dir.glob('*.xml')))
    async def execute(self,plan,*,search=False,fail=False):
        self.adb.package=APP;self.adb.page={'search':True} if search else {};self.adb.fail=fail
        phone=await self.phone();self.used_phone=phone
        store=Store(self.f.life);self.addCleanup(store.close)
        from phonelife.life_subject import attach_subject
        attach_subject(store,phone.contract.binding)
        leases=Store(self.f.lease);self.addCleanup(leases.close);owner='offline-owner'
        for key in ('real','autonomous-api-writer','subject:'+phone.contract.binding.life_subject_id):leases.acquire(key,owner,120)
        phone.preheld_lease_owner=owner
        test=self
        class Gui:
            model='offline';calls=0;feedback=[]
            async def propose(self,goal,observation,history,feedback=None):
                self.feedback.append(feedback);self.calls+=1
                s=phone.last_semantic
                obj=plan(self.calls,s)
                return ModelResponse(json.dumps(obj),provider='offline',model='offline')
        gui=Gui();self.used_gui=gui
        life=PhoneLife(store,phone.contract.binding.execution_device_id,phone,MockPlanner(),gui,a.AllAppActionPolicy(phone.contract,phone.catalog),
            step_limit=5,action_delay=0,semantic_provider=a.AllAppSemanticProvider(),lease_store=LeaseNamespace(leases,phone.contract.binding.execution_device_id))
        self.used_life=life
        try:return await life.execute_goal('offline cross-app navigation',run_kind='autonomous')
        finally:
            for key in ('real','autonomous-api-writer','subject:'+phone.contract.binding.life_subject_id):leases.release(key,owner)
    async def test_full_leased_executor_switches_two_apps_and_observes_actual_foreground(self):
        def plan(n,s):return self.candidate(s,package_name=SECOND if n==1 else APP) if n<=2 else {'type':'finish','reason':'offline','outcome':'uncertain'}
        result=await self.execute(plan)
        self.assertEqual(result.status,'uncertain');self.assertEqual(len(self.adb.inputs),2)
        self.assertEqual(self.adb.package,APP)
        s=Store.open_readonly(self.f.life)
        attempts=[json.loads(r[0]) for r in s.conn.execute('SELECT payload FROM action_attempts')];s.close()
        executed=[v for v in attempts if v.get('execution_status')=='executed'];self.assertEqual(len(executed),2)
        self.assertTrue(all(v['app_post_observation']['foreground_matches_requested_app'] for v in executed))
    async def test_full_search_types_and_submits_only_in_still_focused_search(self):
        def plan(n,s):return self.candidate(s,'search_text',control_id=next(c.control_id for c in s.controls if c.clickable),text='space exploration',submit=True) if n==1 else {'type':'finish','reason':'offline','outcome':'uncertain'}
        await self.execute(plan,search=True)
        self.assertEqual(len(self.adb.inputs),2);self.assertEqual(self.adb.inputs[0][2],'text');self.assertEqual(self.adb.inputs[1][-1],'66')
        self.assertEqual(self.used_phone._dispatched,2);self.assertEqual(self.adb.page['role'],'search_result')
    async def test_ambiguous_candidate_skipped_then_different_safe_action_runs(self):
        def plan(n,s):
            if n==1:return self.candidate(s,'text',text='not a supported free form editor')
            if n==2:return self.candidate(s,package_name=SECOND)
            return {'type':'finish','reason':'offline','outcome':'uncertain'}
        result=await self.execute(plan)
        self.assertEqual(len(self.adb.inputs),1);self.assertEqual(self.adb.package,SECOND)
        self.assertEqual(self.used_gui.feedback[1]['code'],'ACTION_RISK_SKIPPED');self.assertEqual(result.status,'uncertain')
    async def test_unknown_dispatch_never_resends_and_retains_prewrite(self):
        result=await self.execute(lambda n,s:self.candidate(s,package_name=SECOND),fail=True)
        self.assertEqual(len(self.adb.inputs),1);self.assertTrue(self.used_phone._unknown)
        self.assertEqual(result.reason_code,'ADB_OUTCOME_UNKNOWN')
        with self.assertRaises(PolicyDenied):await self.used_phone.act(Action('launch',package=SECOND))
        s=Store.open_readonly(self.f.life);rows=[json.loads(r[0]) for r in s.conn.execute('SELECT payload FROM action_attempts')];s.close()
        self.assertEqual(rows[-1]['execution_status'],'uncertain_after_transport_error')
    async def test_goal_scope_cannot_turn_browsing_into_foreground_success(self):
        self.assertTrue(a.AllAppPhone.goal_consistency('Open '+APP,{'kind':'foreground','package':APP})[0])
        self.assertFalse(a.AllAppPhone.goal_consistency('Open '+APP+' and read ten articles',{'kind':'foreground','package':APP})[0])
        self.assertFalse(a.AllAppPhone.goal_consistency('Read public information',{'kind':'foreground','package':APP})[0])
        self.assertFalse(a.AllAppPhone.goal_consistency('Read public information',{'kind':'all','checks':[{'kind':'foreground','package':APP},{'kind':'foreground','package':APP}]})[0])
        self.assertFalse(a.AllAppPhone.goal_consistency('Explore videos',{'kind':'observe'})[0])
    async def test_session_and_subject_memory_budget_continuity_without_smoke(self):
        first=self.session();s=Store.open_readonly(self.f.life)
        count=s.conn.execute('SELECT COUNT(*) FROM life_requests').fetchone()[0];s.close()
        second=self.session(self.f.root/'second-hour',first=False)
        self.assertEqual(first['config']['agent_db'],second['config']['agent_db'])
        self.assertEqual(first['config']['physical_execution']['life_binding']['life_subject_id'],second['config']['physical_execution']['life_binding']['life_subject_id'])
        s=Store.open_readonly(self.f.life);self.assertEqual(s.conn.execute('SELECT COUNT(*) FROM life_requests').fetchone()[0],count)
        self.assertTrue(s.conn.execute("SELECT 1 FROM life_interests WHERE topic='historical curiosity'").fetchone());s.close()
    async def test_strict_physical_policy_and_settings_registry_unchanged(self):
        with self.assertRaises(PolicyDenied):ActionPolicy('real',frozenset({APP})).check(Action('tap',x=100,y=100))
        self.assertEqual(d.DedicatedRegistry.packages,frozenset({'com.android.settings'}))
    async def test_dynamic_prompt_schema_and_brain_context_use_live_inventory(self):
        p,o,s=await self.semantic();g=a.AllAppGui(None,'offline',p.catalog)
        schema=g.schema_provider();rule=next(r for r in schema['oneOf'] if r['properties']['type']['const']=='open_app')
        self.assertIn(APP,rule['properties']['package_name']['enum']);self.assertNotIn('read-only Android Settings navigation',a.AllAppBrain.system_prompt)
        self.assertEqual(p.goal_gate.packages,p.catalog.packages)
        for candidate in ({'type':[]},self.candidate(s,package_name=[]),self.candidate(s,'scroll_region',scrollable_region_id='invalid',direction=[])):
            with self.assertRaises(ValueError):p.capabilities.ground(candidate,s)
    async def test_progress_excludes_status_bar_and_allows_real_content_scroll(self):
        self.adb.package=APP;p,o,s=await self.semantic()
        row=next(c for c in s.controls if c.clickable)
        changed=replace(s,controls=tuple(replace(c,text='Different article') if c==row else c for c in s.controls))
        self.assertEqual(p.capabilities.content_progress(s,changed)['changed'],True)
        top=replace(row,bounds=(0,0,100,70),resource_id=APP+':id/status_bar_clock',text='12:00')
        left=replace(s,controls=s.controls+(top,));right=replace(s,controls=s.controls+(replace(top,text='12:01'),))
        self.assertFalse(p.capabilities.content_progress(left,right)['changed'])
    async def test_search_submit_not_sent_if_typing_changes_focus_to_message_form(self):
        original=self.adb.dispatch
        async def changed(serial,args):
            await original(serial,args)
            if args[:3]==('shell','input','text'):self.adb.page={'role':'chat_input','label':'Send a message'}
        self.adb.dispatch=changed
        def plan(n,s):return self.candidate(s,'search_text',control_id=next(c.control_id for c in s.controls if c.clickable),text='space',submit=True)
        result=await self.execute(plan,search=True)
        self.assertEqual(len(self.adb.inputs),1);self.assertTrue(self.used_phone._unknown)
        self.assertEqual(result.reason_code,'ADB_OUTCOME_UNKNOWN')
    async def test_empty_xml_has_explicit_redacted_observation_but_app_switch_available(self):
        original=self.adb.run
        async def no_xml(serial,args):
            if args==('exec-out','uiautomator','dump','/proc/self/fd/1'):return b'no accessible nodes'
            return await original(serial,args)
        self.adb.run=no_xml;p,o,s=await self.semantic()
        self.assertEqual(s.failure_reason,'GUI_XML_UNAVAILABLE_REDACTED')
        action,b=p.capabilities.ground(self.candidate(s,package_name=APP),s);self.assertEqual(action.package,APP)
    async def test_stale_cached_semantics_cannot_bind_new_image(self):
        p,o,s=await self.semantic()
        with self.assertRaises(PolicyDenied):await a.AllAppSemanticProvider().collect(p,ScreenObservation.from_png(o.png,p.serial))
    async def test_ambiguous_risk_repair_is_bounded_without_any_input(self):
        result=await self.execute(lambda n,s:self.candidate(s,'text',text='unsupported arbitrary editor'))
        self.assertEqual(self.used_gui.calls,3);self.assertEqual(self.adb.inputs,[])
        self.assertEqual(result.reason_code,'ACTION_RISK_SKIPPED');self.assertFalse(self.used_phone._unknown)
    async def test_search_two_commands_cannot_exceed_original_action_budget(self):
        def plan(n,s):
            self.used_phone._dispatched=4 # One command remains out of the original five.
            return self.candidate(s,'search_text',control_id=next(c.control_id for c in s.controls if c.clickable),text='space',submit=True)
        result=await self.execute(plan,search=True)
        self.assertEqual(self.adb.inputs,[]);self.assertEqual(result.status,'blocked')
    async def test_emergency_stop_denies_all_app_dispatch_before_input(self):
        def plan(n,s):
            l=SessionLedger(self.f.session/'soak-control.sqlite3');l.operator('emergency_stopped');l.close()
            return self.candidate(s,package_name=SECOND)
        result=await self.execute(plan)
        self.assertEqual(self.adb.inputs,[]);self.assertEqual(result.status,'blocked')
    async def test_high_risk_modal_context_blocks_benign_navigation_label(self):
        self.adb.package=APP;p,o,s=await self.semantic();root=s.controls[0];row=next(c for c in s.controls if c.clickable)
        modal=replace(root,resource_id='android:id/parentPanel',text='Delete content?')
        nav=replace(row,resource_id=APP+':id/navigation_next',text='Next')
        changed=replace(s,controls=tuple(modal if c==root else nav if c==row else c for c in s.controls))
        with self.assertRaises(a.SkippedAction):p.capabilities.safe_target(changed,nav)
    async def test_worker_uses_real_brain_gui_classes_and_cross_app_episode_memory(self):
        value=self.session();l=SessionLedger(self.f.session/'soak-control.sqlite3');l.start();l.update(active_pid=os.getpid());l.close()
        phone_type=a.AllAppPhone;created=[];contexts=[];requests=[];target=APP
        original_dispatch=self.adb.dispatch
        async def with_reader_search(serial,args):
            await original_dispatch(serial,args)
            if args[:3]==('shell','am','start') and self.adb.package==APP:self.adb.page={'search':True}
        self.adb.dispatch=with_reader_search
        def phone_factory(contract):
            p=phone_type(contract,self.adb);created.append(p);return p
        class OfflineAPI:
            provider='offline'
            def __init__(self,*args,**kwargs):pass
            async def generate(self,model,system,user,image=None,temperature=.2):
                nonlocal target
                request=json.loads(user);requests.append(request)
                if 'life_context' in request:
                    ctx=request['life_context'];contexts.append(ctx)
                    selected={'goal_id':'model-choice','description':'Browse public articles' if target==APP else 'Open '+target,'motivation':'offline model choice','expected_value':.6,'novelty':.8,
                        'estimated_cost':3,'risk_class':'low','continuation_of':None,'created_at':ctx['now'],'verification':{'kind':'observe'} if target==APP else {'kind':'foreground','package':target}}
                    result={'kind':'explore','candidates':[selected],'selected_goal_id':'model-choice','reason':'offline free choice','wake_after_seconds':60}
                elif 'semantic_context' in request:
                    sem=request['semantic_context']
                    if sem['foreground_package']!=target:result={'type':'open_app','reason':'offline choose launch','observation_id':sem['observation_id'],'package_name':target}
                    elif search:=next((c for c in sem['controls'] if c.get('search_text_allowed')),None):result={'type':'search_text','reason':'offline search choice','observation_id':sem['observation_id'],'control_id':search['control_id'],'text':'space exploration','submit':True}
                    else:result={'type':'finish','reason':'offline request independent verification','outcome':'achieved'}
                else:result={'attempted':'Opened app','subjective_outcome':'Observed foreground','discoveries':['Public entry screen observed'],'topics':['reading'],'worth_continuing':target==APP,'next_adjustment':'Consider another app'}
                return ModelResponse(json.dumps(result),provider='offline',model='offline')
        from phonelife.longrun import run_worker
        def due():
            s=Store(self.f.life);s.conn.execute('UPDATE schedule SET next_wake_at=0');s.close()
        with patch.dict(os.environ,ENV),patch.object(a,'AllAppPhone',side_effect=phone_factory),patch('phonelife.longrun_api.SoakAPI',OfflineAPI):
            due();await run_worker(self.f.session,os.getppid())
            target=SECOND;due();await run_worker(self.f.session,os.getppid())
        self.assertEqual(len(contexts),2);self.assertEqual(len(self.adb.inputs),4)
        self.assertIn(APP,contexts[0]['safety_constraints']['autonomous_apps'])
        self.assertIn(SECOND,contexts[1]['safety_constraints']['autonomous_apps'])
        self.assertTrue(contexts[1]['recent_episodes'])
        self.assertEqual(contexts[1]['unfinished_goals'][0]['remaining_action_budget'],12) # launch + text + Enter, persisted across workers
        self.assertTrue(any(e['body']['objective']['confirmed_actions']==3 for e in contexts[1]['recent_episodes']))
        self.assertEqual(len(created),2)
        s=Store.open_readonly(self.f.life)
        tasks=[dict(r) for r in s.conn.execute("SELECT task_run_id,status FROM task_runs WHERE device=?",(value['config']['device'],))]
        self.assertEqual([r['status'] for r in tasks],['exploration_observed','succeeded'])
        self.assertGreater(s.conn.execute('SELECT COUNT(*) FROM life_requests').fetchone()[0],1)
        s.close();leases=Store.open_readonly(self.f.lease);self.assertFalse(leases.conn.execute('SELECT 1 FROM lease').fetchone());leases.close()

class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_fixed_launcher_argv_quotes_inner_class_and_rejects_shell_intent(self):
        t=a.AppTransport();sent=[]
        async def run(argv,**kwargs):sent.append(argv);return b''
        t._run=run
        await t.dispatch('offline-usb-001',('shell','am','start','-W',*a.LAUNCH_FLAGS,'-n',"'org.example.reader/.Main$Inner'"))
        self.assertEqual(sent[0][-1],"'org.example.reader/.Main$Inner'")
        for args in [('shell','reboot'),('shell','am','start','-d','https://bad'),('shell','input','text','$(reboot)')]:
            with self.assertRaises(PolicyDenied):await t.dispatch('offline-usb-001',args)
        self.assertEqual(len(sent),1)
