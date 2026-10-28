import hashlib,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from PIL import Image,ImageDraw
from digitallife_v2.native_share import NativeShareUI
from digitallife_v2.safety import Blocked
from digitallife_v2.social_ui import WX
from digitallife_v2.perception import Control
from test_m1 import observation
from unittest.mock import AsyncMock


class NativeAvatarWitnessTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
    def frame(self,name,size=128,other=False,plain=False):
        image=Image.new('RGB',(size,size),(80,80,80));d=ImageDraw.Draw(image)
        if not plain:
            colors=[(240,30,20),(30,230,40),(20,40,240),(230,220,60)]
            if other:colors=colors[::-1]
            for (a,b,c,e),color in zip([(0,0,.5,.5),(.5,0,1,.5),(0,.5,.5,1),(.5,.5,1,1)],colors):d.rectangle((int(a*size),int(b*size),int(c*size),int(e*size)),fill=color)
        p=self.root/(name+'.png');image.save(p)
        return SimpleNamespace(png_path=str(p),png_sha256=hashlib.sha256(p.read_bytes()).hexdigest()),(0,0,size,size)
    def test_fresh_group_avatar_can_match_across_picker_sizes(self):
        a,x=self.frame('info',256);b,y=self.frame('picker',128)
        self.assertTrue(NativeShareUI.avatar_matches(a,x,b,y))
    def test_different_avatar_or_generic_blank_icon_cannot_identify_group(self):
        a,x=self.frame('group');b,y=self.frame('wrong',other=True);c,z=self.frame('generic',plain=True)
        self.assertFalse(NativeShareUI.avatar_matches(a,x,b,y));self.assertFalse(NativeShareUI.avatar_matches(c,z,c,z))
    def test_tampered_screenshot_is_not_a_target_witness(self):
        a,x=self.frame('group');Path(a.png_path).write_bytes(b'changed')
        with self.assertRaises(Blocked):NativeShareUI.avatar(a,x)
    def test_non_square_image_region_is_not_an_avatar(self):
        a,_=self.frame('group')
        with self.assertRaises(Blocked):NativeShareUI.avatar(a,(0,0,120,40))


class OfficialConfirmationTests(unittest.TestCase):
    def screen(self,recipient='Fixture group(12人)',caption='发消息',title='Fixture video'):
        obs=observation('preview',foreground=WX+'/com.tencent.mm.ui.halfscreen.HalfScreenTransparentActivity');obs.controls=[]
        for i,text in enumerate(['Fixture group','Fixture video','发送给：',recipient,title,caption,'取消','发送']):
            obs.controls.append(Control(str(i),WX,'',text,'','visual.TextRegion',(20,100+i*100,300,140+i*100),None,None,None,None,True,None))
        return obs
    def check(self,obs):return NativeShareUI(None).confirmation(obs,{'title':'Fixture video'},SimpleNamespace(app=WX,route_hint='Fixture group(12)'))
    def test_actual_modal_recipient_and_empty_caption_are_required(self):
        self.assertEqual(self.check(self.screen()).text,'发送')
        for obs in [self.screen(recipient='Wrong group(12人)'),self.screen(caption='unexpected draft'),self.screen(title='Wrong video')]:
            with self.assertRaises(Blocked):self.check(obs)
    def test_underlying_page_or_changed_activity_cannot_substitute_for_modal(self):
        obs=self.screen();obs.foreground=WX+'/.LauncherUI'
        with self.assertRaises(Blocked):self.check(obs)


class NativeWindowNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_sent_callback_uses_stay_in_wechat_without_another_send(self):
        obs=observation('callback',foreground=WX+'/.SendAppMessageWrapperUI')
        obs.controls=[SimpleNamespace(text=t,covered=False) for t in ('选择聊天','已发送','留在微信')]
        ui=SimpleNamespace(tap=AsyncMock(return_value='actual-chat'),visual_tap=AsyncMock())
        self.assertEqual(await NativeShareUI(None).cancel_wechat_picker(ui,obs),'actual-chat')
        ui.tap.assert_awaited_once_with(obs,text='留在微信');ui.visual_tap.assert_not_awaited()
    async def test_fullscreen_does_not_open_system_menu_and_unknown_menu_cannot_change_mode(self):
        obs=observation('wechat',foreground=WX+'/.LauncherUI')
        phone=SimpleNamespace(focus_window='abc',last_windows='Window #0 Window{abc u0 WeChat}:\n frame=[0,0][%d,%d]'%(obs.width,obs.height))
        native=NativeShareUI(SimpleNamespace(phone=phone));ui=SimpleNamespace(visual_tap=AsyncMock(),tap=AsyncMock())
        self.assertIs(await native.ensure_wechat_fullscreen(ui,obs),obs);ui.visual_tap.assert_not_awaited()
        phone.last_windows='Window #0 Window{abc u0 WeChat}:\n frame=[100,300][800,1500]'
        menu=observation('unrelated',foreground=WX+'/.UnrelatedMenu');menu.controls=[];ui.visual_tap.return_value=menu
        with self.assertRaisesRegex(Blocked,'SYSTEM_MENU_UNVERIFIED'):await native.ensure_wechat_fullscreen(ui,obs)
        ui.tap.assert_not_awaited()
