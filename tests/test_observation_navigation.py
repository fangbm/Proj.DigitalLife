"""offline_fixture: directory vs form privacy, real tab geometry with synthetic XML."""
import unittest
import json
from dataclasses import replace
from xml.sax.saxutils import quoteattr
from phonelife import app_exploration as a
from phonelife.devices import mock_png
from phonelife.models import ScreenObservation
from phonelife.policy import PolicyDenied
from phonelife.semantic import UIAutomatorBackend,normalize
from phonelife.observation_privacy import assess_xml,sensitive_window_reason
import test_app_exploration as fx

APP='org.example.reader'
def node(cls,bounds,children='',**attrs):
    values={'class':cls,'bounds':bounds,'package':APP,'enabled':'true','clickable':'false','scrollable':'false',
        'checkable':'false','password':'false','long-clickable':'false'}|attrs
    return '<node '+' '.join(k+'='+quoteattr(v)for k,v in values.items())+'>'+children+'</node>'
def screen(children):return '<?xml version="1.0"?><hierarchy rotation="0">'+node('android.widget.FrameLayout','[0,0][1240,2772]',children)+'</hierarchy>'
def tab_xml():
    tabs=''
    for label,left,right,click in [('信息面板',0,280,False),('硬件',280,532,True),('系统',532,784,True),('电池',784,1036,True),('网络',1036,1240,True)]:
        text=node('android.widget.TextView',f'[{left+20},413][{right-20},481]',text=label)
        tabs+=node('android.widget.LinearLayout',f'[{left},363][{right},531]',text,clickable=str(click).lower(),**{'content-desc':label})
    return screen(node('android.widget.HorizontalScrollView','[0,363][1240,531]',node('android.widget.LinearLayout','[0,363][1240,531]',tabs),scrollable='true',**{'resource-id':APP+':id/tabs'}))
def directory_xml():
    rows=''.join(node('android.widget.TextView',f'[0,{400+i*120}][1240,{500+i*120}]',text=text,**{'resource-id':'android:id/title'})for i,text in enumerate(('设置','密码与安全','应用权限','Password and security')))
    return screen(node('androidx.recyclerview.widget.RecyclerView','[0,300][1240,2500]',rows,scrollable='true'))

class ObservationNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def semantic(self,xml):
        class Device:
            async def hierarchy(self):return xml
        obs=ScreenObservation.from_png(mock_png(1240,2772),'offline')
        raw=await UIAutomatorBackend().read(Device(),obs);s=normalize(raw,obs,APP+'/.Main',a.PROVIDER)
        catalog=a.AppCatalog();catalog.installed=frozenset({APP});catalog.components={APP:set()}
        return a.AllAppRegistry(catalog),s
    async def test_real_tab_geometry_exposes_clickable_battery_id(self):
        registry,s=await self.semantic(tab_xml());view=registry.view(s)
        target=next(c for c in s.controls if c.content_description=='电池')
        taps=[c['control_id']for c in view['controls']if c['intent']=='tap_control']
        self.assertIn(target.control_id,taps)
        action,binding=registry.ground({'type':'tap_control','reason':'Read battery tab','observation_id':s.observation_id,'control_id':target.control_id},s)
        self.assertEqual((action.x,action.y),(910,447));self.assertIsNone(target.visible);self.assertIsNone(target.editable)
    async def test_horizontal_tab_strip_is_not_vertical_scroll_or_click(self):
        registry,s=await self.semantic(tab_xml());strip=next(c for c in s.controls if c.scrollable)
        self.assertNotIn(strip.control_id,[v['control_id']for v in registry.view(s)['controls']])
        for candidate in ({'type':'tap_control','control_id':strip.control_id},{'type':'scroll_region','scrollable_region_id':strip.control_id,'direction':'toward_end'}):
            with self.assertRaises(PolicyDenied):registry.ground({'reason':'offline','observation_id':s.observation_id,**candidate},s)
    async def test_settings_directory_keywords_are_not_a_credential_form(self):
        self.assertFalse(a.sensitive_xml(directory_xml()))
    async def test_actual_password_and_non_search_editors_stay_redacted(self):
        for attrs in ({'password':'true','text':'synthetic-secret'},{'text':'synthetic-private-message'}):
            self.assertTrue(a.sensitive_xml(screen(node('android.widget.EditText','[0,400][1240,600]',**attrs))))
    async def test_risky_navigation_tab_stays_denied(self):
        registry,s=await self.semantic(tab_xml());target=next(c for c in s.controls if c.content_description=='电池')
        bad=replace(target,content_description='支付',text='Pay')
        changed=replace(s,controls=tuple(bad if c==target else c for c in s.controls))
        with self.assertRaises(PolicyDenied):registry.safe_target(changed,bad)
    async def test_tab_identity_flags_and_ambiguous_children_stay_required(self):
        registry,s=await self.semantic(tab_xml());target=next(c for c in s.controls if c.content_description=='电池')
        for bad in (replace(target,clickable=False),replace(target,enabled=False),replace(target,visible=False),replace(target,checkable=True)):
            current=replace(s,controls=tuple(bad if c==target else c for c in s.controls))
            with self.assertRaises(PolicyDenied):registry.safe_target(current,bad)
        child=next(c for c in s.controls if c.parent==target.control_id)
        bad=replace(child,text='A different destination')
        current=replace(s,controls=tuple(bad if c==child else c for c in s.controls))
        with self.assertRaises(PolicyDenied):registry.safe_target(current,target)
        selected=next(c for c in s.controls if c.content_description=='信息面板')
        with self.assertRaises(PolicyDenied):registry.safe_target(s,selected)

class PrivacyEvidenceTests(unittest.TestCase):
    def test_headers_private_values_and_search_roles(self):
        for body in (node('android.widget.TextView','[0,100][1240,300]',text='密码',**{'resource-id':APP+':id/toolbar_title'}),
                     node('android.widget.TextView','[0,100][1240,300]',text='Register',**{'resource-id':APP+':id/page_title'}),
                     node('android.widget.TextView','[0,400][1240,600]',text='synthetic-only',**{'resource-id':APP+':id/password_value'}),
                     node('android.widget.FrameLayout','[0,400][1240,600]',**{'resource-id':APP+':id/account_form'})):
            self.assertTrue(assess_xml(screen(body))['redacted'])
        search=node('android.widget.EditText','[0,100][1240,300]',**{'resource-id':APP+':id/search_input','content-desc':'Search'})
        self.assertFalse(assess_xml(screen(search))['redacted'])
        for text in ('synthetic@example.invalid','password=synthetic-only'):
            value=assess_xml(screen(node('android.widget.EditText','[0,100][1240,300]',text=text,**{'resource-id':APP+':id/search_input'})))
            self.assertTrue(value['redacted']);self.assertNotIn(text,json.dumps(value))
        with self.assertRaisesRegex(PolicyDenied,'INVALID_ATTRIBUTE'):
            assess_xml(screen(node('android.widget.EditText','[0,100][1240,300]',password='unknown')))
    def test_visible_security_windows_still_redact_hidden_retained_surface_does_not(self):
        head='Window #0 Window{b u0 com.android.permissioncontroller/.Permission}\nty=1\nmViewVisibility=0x0 mHasSurface=true\n'
        self.assertIsNone(sensitive_window_reason({'windows':head+'isOnScreen=false isVisible=false'},APP+'/.Main'))
        self.assertEqual(sensitive_window_reason({'windows':head+'isOnScreen=true isVisible=true'},APP+'/.Main'),'VISIBLE_SECURITY_WINDOW')
        self.assertEqual(sensitive_window_reason({'windows':head.replace('ty=1','ty=2038')},APP+'/.Main'),'VISIBLE_SECURITY_WINDOW')
        self.assertEqual(sensitive_window_reason({'windows':head.replace('ty=1','ty=2009')},APP+'/.Main'),'VISIBLE_SECURITY_WINDOW')
        self.assertEqual(sensitive_window_reason({},'com.android.permissioncontroller/.Permission'),'FOREGROUND_SECURITY_SURFACE')

class ObservationPathTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.h=fx.AppsTests();self.h.setUp();self.xml=directory_xml()
        self.h.adb.package=APP;original=self.h.adb.run
        async def run(serial,args):
            if args==('exec-out','uiautomator','dump','/proc/self/fd/1'):
                self.h.adb.calls.append((serial,args));return self.xml.encode()
            if args==('exec-out','screencap','-p'):
                self.h.adb.calls.append((serial,args));return mock_png(1240,2772)
            return await original(serial,args)
        self.h.adb.run=run
        def cleanup():
            while self.h._cleanups:
                function,args,kwargs=self.h._cleanups.pop();function(*args,**kwargs)
        self.addCleanup(cleanup)
    async def test_directory_observation_saves_current_image_and_reason_audit(self):
        phone,obs,s=await self.h.semantic()
        self.assertFalse(phone._redacted);self.assertTrue(s.controls)
        self.assertTrue(list(phone.capture_dir.glob('*.png')));self.assertTrue(list(phone.capture_dir.glob('*.xml')))
        audit=phone.capabilities.view(s)['observation_assessment']
        self.assertFalse(audit['redacted']);self.assertEqual(audit['reason_codes'],[])
        self.assertEqual(phone.last_semantic,s);self.assertEqual(obs.width,1240);self.assertEqual(self.h.adb.inputs,[])
    async def test_private_editor_redacts_before_capture_and_retains_only_safe_reasons(self):
        secret='synthetic-private-value-never-captured'
        self.xml=screen(node('android.widget.EditText','[0,400][1240,600]',text=secret))
        phone,obs,s=await self.h.semantic();view=phone.capabilities.view(s)
        self.assertTrue(view['screen_redacted']);self.assertIn('NON_SEARCH_EDITOR',view['observation_assessment']['reason_codes'])
        self.assertNotIn(secret,json.dumps(view));self.assertEqual(s.controls,())
        self.assertFalse(any(args==('exec-out','screencap','-p')for _,args in self.h.adb.calls))
        self.assertFalse(list(phone.capture_dir.glob('*.xml')));self.assertFalse(list(phone.capture_dir.glob('*.png')))
        self.assertFalse(phone.capabilities.evidence_for_action(s,None));self.assertEqual(self.h.adb.inputs,[])
