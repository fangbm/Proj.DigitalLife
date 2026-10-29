import hashlib,tempfile,unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch
from dataclasses import replace
from PIL import Image
from digitallife_v2.bilibili import BilibiliUI
from digitallife_v2.media import BILIBILI
from digitallife_v2.safety import Blocked,UnknownDispatch
from test_m1 import observation


class DetailArrowNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_native_click_role_uses_actual_arrow_pixels_without_model_shift(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'fixture.png';Image.new('RGB',(720,1600),'black').save(path)
            obs=observation('actual-arrow',foreground=BILIBILI+'/.UnitedBizDetailsActivity')
            obs.png_path=str(path);obs.png_sha256=hashlib.sha256(path.read_bytes()).hexdigest()
            arrow=replace(obs.controls[0],package=BILIBILI,resource=BILIBILI+':id/arrow',bounds=(600,800,650,850),clickable=None)
            obs.controls=[arrow];obs.visual_controls={};ui=BilibiliUI(None)
            after=observation('after');ui.act=AsyncMock(side_effect=[Blocked('UNKNOWN_ACTION_ROLE'),after])
            with patch('digitallife_v2.visual.locate',new=AsyncMock()) as model:
                self.assertIs(await ui.select(obs,resource='arrow',visual='bili_video_details'),after);model.assert_not_awaited()
            rebound=next(c for c in obs.controls if c.control_id==ui.act.await_args_list[1].args[0]['control_id'])
            self.assertEqual(rebound.bounds,arrow.bounds);self.assertIsNone(rebound.clickable)
    async def test_other_pre_dispatch_errors_are_not_automatically_replayed(self):
        obs=observation();obs.controls=[replace(obs.controls[0],package=BILIBILI,resource=BILIBILI+':id/arrow')]
        ui=BilibiliUI(None);ui.act=AsyncMock(side_effect=Blocked('FINAL_SCENE_CHANGED_OR_EXPIRED'))
        with self.assertRaisesRegex(Blocked,'SCENE_CHANGED'):await ui.select(obs,resource='arrow',visual='bili_video_details')
        ui.act.assert_awaited_once()


class BackgroundEntryTests(unittest.IsolatedAsyncioTestCase):
    def bili_home(self):
        obs=observation('bili-home',foreground=BILIBILI+'/.MainActivityV2')
        obs.controls=[replace(obs.controls[0],package=BILIBILI,resource=BILIBILI+':id/tab_text',text='我的')]
        return obs
    async def test_public_thread_entry_can_start_from_the_launcher(self):
        launcher=observation('launcher',foreground='com.android.launcher/.Launcher');home=self.bili_home()
        ui=BilibiliUI(None);ui.observe=AsyncMock(return_value=launcher);ui.act=AsyncMock(return_value=home);ui.dismiss_empty_editor=AsyncMock(return_value=home)
        self.assertIs(await ui.home_page(),home)
        ui.act.assert_awaited_once_with({'type':'open_app','package_name':BILIBILI},launcher)
        ui.dismiss_empty_editor.assert_awaited_once_with(home)
    async def test_launch_returning_another_package_cannot_navigate_that_package(self):
        launcher=observation('launcher',foreground='com.android.launcher/.Launcher')
        ui=BilibiliUI(None);ui.observe=AsyncMock(return_value=launcher);ui.act=AsyncMock(return_value=observation('foreign',foreground='com.tencent.mm/.Selector'));ui.dismiss_empty_editor=AsyncMock()
        with self.assertRaisesRegex(Blocked,'APP_FOREGROUND_CHANGED'):await ui.home_page()
        ui.act.assert_awaited_once();ui.dismiss_empty_editor.assert_not_awaited()
    async def test_uncertain_launch_is_not_replayed(self):
        ui=BilibiliUI(None);ui.observe=AsyncMock(return_value=observation('launcher',foreground='com.android.launcher/.Launcher'))
        ui.act=AsyncMock(side_effect=UnknownDispatch('POST_OBSERVATION_FAILED'))
        with self.assertRaises(UnknownDispatch):await ui.home_page()
        ui.act.assert_awaited_once()
