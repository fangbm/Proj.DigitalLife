import unittest
from digitallife_v2.phone import focused_window, PhoneController
from digitallife_v2.safety import Blocked
from test_m1 import config
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
