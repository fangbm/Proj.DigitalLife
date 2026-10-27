import unittest
from dataclasses import replace
from unittest.mock import AsyncMock
from digitallife_v2.phone import focused_window, PhoneController
from digitallife_v2.safety import Blocked
from test_m1 import config
from test_m1 import observation
from pathlib import Path


class WindowIdentityTests(unittest.TestCase):
    window = '''Window #8 Window{c105db1 u0 com.android.systemui}:
    mOwnerUid=10195 showForAllUsers=true package=com.android.systemui appop=NONE
    mAttrs={ty=STATUS_BAR_SUB_PANEL}
    Surface: shown=true
    isOnScreen=true
    isVisible=true
    '''
    display = 'mCurrentFocus=Window{c105db1 u0 com.android.systemui}'

    def test_package_only_dialog_requires_exact_visible_owned_window(self):
        self.assertEqual(focused_window(self.window, self.display, True),
            ('com.android.systemui/@window:c105db1', 'c105db1'))
        for window, display, full in [
            (self.window, self.display, False),
            (self.window.replace('isOnScreen=true', 'isOnScreen=false'), self.display, True),
            (self.window.replace('package=com.android.systemui', 'package=com.other'), self.display, True),
            (self.window, self.display.replace('c105db1', 'abc123'), True),
            (self.window, self.display+'\nmCurrentFocus=Window{abc123 u0 com.other/.Main}', True)]:
            with self.assertRaises(Blocked): focused_window(window, display, full)

    def test_activity_identity_kept_and_modern_overlay_visibility_checked(self):
        self.assertEqual(focused_window('', 'mCurrentFocus=Window{abc123 u0 com.reader/.Main}', False),
            ('com.reader/.Main', 'abc123'))
        phone=PhoneController(config(Path('.')), Path('unused'))
        phone.last_windows=self.window.replace('ty=STATUS_BAR_SUB_PANEL', 'ty=2038')
        with self.assertRaisesRegex(Blocked, 'VISIBLE_INPUT_OVERLAY'):
            phone.check_windows('com.reader/.Main')

    def test_popup_window_uses_verified_owner_and_exact_token(self):
        window=self.window.replace('com.android.systemui}', 'PopupWindow:abc123}').replace('package=com.android.systemui', 'package=com.tencent.mobileqq')
        display=self.display.replace('com.android.systemui}', 'PopupWindow:abc123}')
        self.assertEqual(focused_window(window,display,True),('com.tencent.mobileqq/@window:c105db1','c105db1'))
        with self.assertRaises(Blocked):focused_window(window.replace('mOwnerUid=10195','unknownUid'),display,True)


class TapOcclusionTests(unittest.IsolatedAsyncioTestCase):
    async def test_fullscreen_ime_uses_explicit_touchable_region_with_frame_fallback(self):
        phone=PhoneController(config(Path('.')),Path('unused'))
        phone.last_windows='Window #0 Window{abc u0 InputMethod}:\n ty=INPUT_METHOD frame=[0,139][1240,2772]\n isOnScreen=true\n isVisible=true\n touchable region=SkRegion((0,1662,1240,2772))\n'
        phone.check_windows('com.reader/.Main',(1000,1440,1001,1441))
        with self.assertRaisesRegex(Blocked,'TARGET_OCCLUDED'):
            phone.check_windows('com.reader/.Main',(1000,1800,1001,1801))
        phone.last_windows=phone.last_windows.replace('touchable region=SkRegion((0,1662,1240,2772))','touchable region=unknown')
        with self.assertRaisesRegex(Blocked,'TARGET_OCCLUDED'):
            phone.check_windows('com.reader/.Main',(1000,1440,1001,1441))

    async def test_transparent_gesture_bar_does_not_cover_app_bottom_tabs(self):
        phone=PhoneController(config(Path('.')),Path('unused'))
        phone.last_windows='Window #0 Window{abc u0 NavigationBar}:\n ty=NAVIGATION_BAR frame=[0,2633][1240,2772]\n isOnScreen=true\n isVisible=true\n touchable region=SkRegion((0,2746,1240,2772))\n'
        phone.check_windows('com.reader/.Main',(1080,2680,1081,2681))
        with self.assertRaisesRegex(Blocked,'TARGET_OCCLUDED'):
            phone.check_windows('com.reader/.Main',(1080,2760,1081,2761))
        phone.last_windows=phone.last_windows.replace('SkRegion((0,2746,1240,2772))','SkRegion()')
        phone.check_windows('com.reader/.Main',(1080,2760,1081,2761))

    async def test_control_crossing_status_bar_uses_actual_tap_point(self):
        cfg=config(Path('.'));cfg.full_access=True
        phone=PhoneController(cfg,Path('unused'))
        phone.identity=AsyncMock();phone.scene=AsyncMock(return_value='com.reader/.Main')
        phone.last_windows='Window #0 Window{abc u0 status}:\n ty=2000 frame=[0,0][720,139]\n isOnScreen=true\n isVisible=true\n'
        obs=observation();target=replace(obs.controls[0],bounds=(0,124,167,279))
        command=await phone.prepare({'type':'tap','control_id':target.control_id},target,obs)
        self.assertEqual(command,('shell','input','tap','83','201'))
        with self.assertRaisesRegex(Blocked,'TARGET_OCCLUDED_BY_SYSTEM_WINDOW'):
            await phone.prepare({'type':'tap','control_id':target.control_id},replace(target,bounds=(0,0,167,200)),obs)
