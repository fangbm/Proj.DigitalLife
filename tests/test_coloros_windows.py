"""offline_fixture: grammar from deidentified native metadata, faults synthetic."""
import unittest
import copy
import json
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch
from phonelife.device_profiles import parse_safety
from phonelife.native_windows import (WINDOW_TYPES, window_type, lock_state,
    interactive_state, NativeMetadataError, assess_native, readonly_capture_gate, assess_xml)


def window(index, identity, package, typ='BASE_APPLICATION', hidden=False, frame='[0,0][100,200]'):
    yes, no = ('false', 'true') if hidden else ('true', 'false')
    return f'''  Window #{index} Window{{{identity} u0 {package}/FixtureActivity}}:
    mDisplayId=0 mOwnerUid=1000 package={package}
    mAttrs={{ty={typ}}}
    mBaseLayer=21000 mSubLayer=0
    mViewVisibility=0x0
    Frames: frame={frame}
    mHasSurface=true
    isReadyForDisplay()={yes}
    isVisibleRequested()={yes}
    isOnScreen={yes}
    isVisible=true
    mLastHidden={no}
    mObscured={no}
      Surface: shown={yes}
'''


def input_row(index, identity, flags='TRUSTED_OVERLAY', frame='[0,0][100,200]', alpha='1'):
    return f'      {index}: name={identity} fixture/FixtureActivity, displayId=0, inputConfig={flags}, alpha={alpha}, frame={frame}, ownerUid=1000,\n'


def scene():
    """offline_fixture: fully synthetic complete two-window scene."""
    return {
        'power': 'POWER MANAGER (dumpsys power)\n mInteractive=true\n',
        'policy': 'WINDOW MANAGER POLICY STATE (dumpsys window policy)\n  KeyguardServiceDelegate\n    showing=false\n    KeyguardStateMonitor\n      mIsShowing=false\n',
        'windows': 'WINDOW MANAGER WINDOWS (dumpsys window windows)\n'+window(0,'a0','com.android.settings')+window(1,'a1','fixture.other',hidden=True)+'  mGlobalConfiguration={}\n  mSystemBooted=true\n',
        'displays': 'WINDOW MANAGER DISPLAY CONTENTS (dumpsys window displays)\nDisplay: mDisplayId=0\n cur=100x200\n mCurrentFocus=Window{a0 u0 com.android.settings/FixtureActivity}\n mObscuringWindow=Window{a0 u0 com.android.settings/FixtureActivity}\n isKeyguardShowing=false\n mKeyguardDrawComplete=true\n',
        'input': "Input Dispatcher State:\n  DispatchEnabled: true\n  DispatchFrozen: false\n  FocusedDisplayId: 0\n  FocusedWindows:\n    displayId=0, name='a0 com.android.settings/FixtureActivity'\n  FocusRequests:\n    Windows:\n"+input_row(0,'a0')+input_row(1,'a1','NO_INPUT_CHANNEL | NOT_VISIBLE | NOT_FOCUSABLE | NOT_TOUCHABLE')+'  mMaximumObscuringOpacityForTouch: 0.8\nViewport INTERNAL: displayId=0, orientation=0\n',
    }


class KeyguardBeforeTests(unittest.TestCase):
    def test_false_with_draw_complete_true_not_overlay(self):
        result=parse_safety('mInteractive=true','mShowingLockscreen=false',
            'mCurrentFocus=Window{a0 u0 com.android.settings/.Settings}\n isKeyguardShowing=false\n mKeyguardDrawComplete=true',
            'SurfaceOrientation: 0')
        self.assertFalse(result['locked'])
        self.assertFalse(result['permission_or_unknown_overlay'])
    def test_false_with_draw_complete_false_not_overlay(self):
        result=parse_safety('mInteractive=true','mShowingLockscreen=false',
            'mCurrentFocus=Window{a0 u0 com.android.settings/.Settings}\n isKeyguardShowing=false\n mKeyguardDrawComplete=false',
            'SurfaceOrientation: 0')
        self.assertFalse(result['permission_or_unknown_overlay'])
    def test_true_display_state_cannot_be_overridden_by_policy_false(self):
        result=parse_safety('mInteractive=true','mShowingLockscreen=false',
            'isKeyguardShowing=true','SurfaceOrientation: 0')
        self.assertTrue(result['locked'])
    def test_invalid_boolean_must_not_parse_as_false(self):
        self.assertIsNone(parse_safety('mInteractive=true','mShowingLockscreen=false_suffix','','')['locked'])


class KeyguardContractTests(unittest.TestCase):
    def denied(self,v):self.assertNotEqual(assess_native(v)['classification'],'SAFE_FOR_READONLY_CAPTURE')
    def test_false_is_not_overlay(self):self.assertFalse(lock_state('', 'isKeyguardShowing=false')['locked'])
    def test_true_is_locked(self):self.assertTrue(lock_state('', 'isKeyguardShowing=true')['locked'])
    def test_draw_complete_alone_grants_nothing(self):self.assertIsNone(lock_state('', 'mKeyguardDrawComplete=true')['locked'])
    def test_missing_fields(self):self.assertIsNone(lock_state('','')['locked'])
    def test_numeric_boolean(self):self.assertIsNone(lock_state('mShowingLockscreen=0','')['locked'])
    def test_string_boolean(self):self.assertIsNone(lock_state('mShowingLockscreen="false"','')['locked'])
    def test_conflict_is_not_unlocked(self):self.assertTrue(lock_state('mShowingLockscreen=false','isKeyguardShowing=true')['locked'])
    def test_unrelated_name(self):self.assertIsNone(lock_state('not_mShowingLockscreen=false', '')['locked'])
    def test_unrelated_generic_showing(self):self.assertIsNone(lock_state('OtherService\n showing=false', '')['locked'])
    def test_delegate_scope_ends_at_peer(self):self.assertIsNone(lock_state('KeyguardServiceDelegate\n  connected=true\nOtherService\n  showing=false', '')['locked'])
    def test_aosp_shape(self):self.assertFalse(lock_state('mShowingLockscreen=false','')['locked'])
    def test_coloros_shape(self):self.assertFalse(lock_state(scene()['policy'],scene()['displays'])['locked'])
    def test_native_lock_source_missing(self):v=scene();v['policy']=v['policy'].split('  KeyguardServiceDelegate')[0];self.denied(v)
    def test_locked_interactive_true(self):v=scene();v['displays']=v['displays'].replace('isKeyguardShowing=false','isKeyguardShowing=true');self.denied(v)
    def test_interactive_asleep_conflict(self):self.assertIsNone(interactive_state('mInteractive=true\nmWakefulness=Asleep'))
    def test_interactive_invalid_suffix(self):self.assertIsNone(interactive_state('mInteractive=true_suffix'))
    def test_policy_header_missing(self):v=scene();v['policy']=v['policy'].replace('WINDOW MANAGER POLICY STATE','Untrusted');self.denied(v)
    def test_truncated_input(self):v=scene();v['input']=v['input'].split('mMaximumObscuring')[0];self.denied(v)
    def test_native_header_missing(self):v=scene();v['windows']=v['windows'].replace('WINDOW MANAGER WINDOWS','UNTRUSTED');self.denied(v)


class WindowTypeTests(unittest.TestCase):
    def test_all_known_numeric_named_equivalent(self):
        for name,number in WINDOW_TYPES.items():
            with self.subTest(name=name):self.assertEqual(window_type(name),window_type(str(number)));self.assertEqual(number,window_type('TYPE_'+name))
    def test_base_application(self):self.assertEqual(window_type('BASE_APPLICATION'),1)
    def test_application(self):self.assertEqual(window_type('APPLICATION'),2)
    def test_status_bar(self):self.assertEqual(window_type('STATUS_BAR'),2000)
    def test_navigation_bar(self):self.assertEqual(window_type('NAVIGATION_BAR'),2019)
    def test_wallpaper(self):self.assertEqual(window_type('WALLPAPER'),2013)
    def test_keyguard(self):self.assertEqual(window_type('KEYGUARD'),2004)
    def test_permission_dialog(self):self.assertEqual(window_type('SYSTEM_DIALOG'),2008)
    def test_input_method(self):self.assertEqual(window_type('INPUT_METHOD'),2011)
    def test_toast(self):self.assertEqual(window_type('TOAST'),2005)
    def test_overlay(self):self.assertEqual(window_type('APPLICATION_OVERLAY'),2038)
    def test_unknown_oem_numeric(self):self.assertRaises(NativeMetadataError,window_type,'2314')
    def test_unknown_oem_name(self):self.assertRaises(NativeMetadataError,window_type,'OEM_BASE_APPLICATION')
    def test_malformed_numeric(self):self.assertRaises(NativeMetadataError,window_type,'01')


class NativeWindowSafetyTests(unittest.TestCase):
    def mutate(self,key,old,new):v=scene();v[key]=v[key].replace(old,new);return v
    def denied(self,v):self.assertNotEqual(assess_native(v)['classification'],'SAFE_FOR_READONLY_CAPTURE')
    def test_complete_synthetic_scene(self):self.assertEqual(assess_native(scene())['classification'],'SAFE_FOR_READONLY_CAPTURE')
    def test_numeric_type_scene(self):v=self.mutate('windows','ty=BASE_APPLICATION','ty=1');self.assertEqual(assess_native(v)['classification'],'SAFE_FOR_READONLY_CAPTURE')
    def test_foreign_base_hidden_not_isvisible_shortcut(self):self.assertEqual(assess_native(scene())['windows'][1]['classification'],'NONDISPLAYED_WITH_INPUT_CORROBORATION')
    def test_foreign_base_actually_shown(self):v=scene();v['windows']=v['windows'].replace(window(1,'a1','fixture.other',hidden=True),window(1,'a1','fixture.other'));self.assertEqual(assess_native(v)['classification'],'BLOCKED_BY_VISIBLE_WINDOW')
    def test_foreign_base_missing_input(self):v=scene();v['input']=v['input'].replace(input_row(1,'a1','NO_INPUT_CHANNEL | NOT_VISIBLE | NOT_FOCUSABLE | NOT_TOUCHABLE'),'');self.denied(v)
    def test_foreign_base_visibility_incomplete(self):self.denied(self.mutate('windows','    isOnScreen=false\n',''))
    def test_visible_keyguard_blocked(self):v=scene();v['windows']=v['windows'].replace(window(1,'a1','fixture.other',hidden=True),window(1,'a1','com.android.systemui',typ='KEYGUARD'));self.denied(v)
    def test_visible_ime_blocked(self):v=scene();v['windows']=v['windows'].replace(window(1,'a1','fixture.other',hidden=True),window(1,'a1','fixture.ime',typ='INPUT_METHOD'));self.denied(v)
    def test_visible_permission_blocked(self):v=scene();v['windows']=v['windows'].replace(window(1,'a1','fixture.other',hidden=True),window(1,'a1','fixture.permission',typ='SYSTEM_DIALOG'));self.denied(v)
    def test_systemui_name_does_not_grant_overlay(self):v=scene();v['windows']=v['windows'].replace(window(1,'a1','fixture.other',hidden=True),window(1,'a1','com.android.systemui',typ='APPLICATION_OVERLAY'));self.denied(v)
    def test_unknown_inactive_type_still_blocks(self):self.denied(self.mutate('windows','ty=BASE_APPLICATION','ty=2315'))
    def test_missing_type(self):self.denied(self.mutate('windows','ty=BASE_APPLICATION',''))
    def test_conflicting_types(self):self.denied(self.mutate('windows','ty=BASE_APPLICATION','ty=1 ty=2004'))
    def test_duplicate_same_type(self):self.denied(self.mutate('windows','ty=BASE_APPLICATION','ty=1 ty=1'))
    def test_missing_closing_attrs(self):self.denied(self.mutate('windows','ty=BASE_APPLICATION}','ty=BASE_APPLICATION'))
    def test_missing_last_footer(self):self.denied(self.mutate('windows','  mGlobalConfiguration={}',''))
    def test_reordered_blocks(self):self.denied(self.mutate('windows','Window #0','Window #2'))
    def test_duplicate_window_ids(self):self.denied(self.mutate('windows','Window{a1','Window{a0'))
    def test_unknown_boolean(self):self.denied(self.mutate('windows','isOnScreen=false','isOnScreen=no'))
    def test_duplicate_invalid_boolean(self):self.denied(self.mutate('windows','isOnScreen=false','isOnScreen=false isOnScreen=bad'))
    def test_not_settings_focused(self):self.denied(self.mutate('displays','com.android.settings','fixture.other'))
    def test_input_focus_conflicts(self):self.denied(self.mutate('input',"name='a0","name='a1"))
    def test_obscuring_window_conflicts(self):self.denied(self.mutate('displays','mObscuringWindow=Window{a0','mObscuringWindow=Window{a1'))
    def test_split_screen_settings_bounds(self):self.denied(self.mutate('windows','frame=[0,0][100,200]','frame=[0,0][50,200]'))
    def test_settings_obscured(self):self.denied(self.mutate('windows','mObscured=false','mObscured=true'))
    def test_input_owner_conflict(self):self.denied(self.mutate('input','ownerUid=1000','ownerUid=9999'))
    def test_extra_display(self):self.denied(self.mutate('windows','mDisplayId=0','mDisplayId=1'))
    def test_input_frozen(self):self.denied(self.mutate('input','DispatchFrozen: false','DispatchFrozen: true'))
    def test_duplicate_input_sections(self):v=scene();v['input']*=2;self.denied(v)
    def test_unattributed_input_layer(self):v=scene();v['input']=v['input'].replace('  mMaximumObscuring',input_row(2,'a9')+'  mMaximumObscuring');self.denied(v)
    def test_systemui_safe_bounded_status_bar(self):
        v=scene();v['windows']=v['windows'].replace(window(1,'a1','fixture.other',hidden=True),window(1,'a1','com.android.systemui','STATUS_BAR',frame='[0,0][100,10]'))
        v['input']=v['input'].replace(input_row(1,'a1','NO_INPUT_CHANNEL | NOT_VISIBLE | NOT_FOCUSABLE | NOT_TOUCHABLE'),input_row(1,'a1','NOT_FOCUSABLE | TRUSTED_OVERLAY',frame='[0,0][100,10]'))
        self.assertEqual(assess_native(v)['classification'],'SAFE_FOR_READONLY_CAPTURE')
    def test_systemui_fullscreen_status_bar_blocks(self):v=scene();v['windows']=v['windows'].replace(window(1,'a1','fixture.other',hidden=True),window(1,'a1','com.android.systemui','STATUS_BAR'));self.denied(v)
    def test_real_metadata_derivative_not_synthetic_origin(self):
        p=Path(__file__).parent/'fixtures/coloros16-window-metadata';v=json.loads((p/'deidentified-fields.json').read_text());provenance=json.loads((p/'PROVENANCE.json').read_text())
        r=assess_native(v);self.assertNotEqual(r['classification'],'SAFE_FOR_READONLY_CAPTURE');self.assertIn('UNKNOWN_WINDOW_TYPE',r['reason_codes']);self.assertEqual(provenance['origin'],'real_device_metadata_deidentified')
        self.assertEqual(r['windows'][17]['classification'],'NONDISPLAYED_WITH_INPUT_CORROBORATION')
    def test_unknown_input_config_blocks(self):self.denied(self.mutate('input','TRUSTED_OVERLAY','TRUSTED_OVERLAY | OEM_UNKNOWN'))
    def test_surface_invalid_boolean_blocks(self):self.denied(self.mutate('windows','Surface: shown=false','Surface: shown=false_suffix'))
    def test_power_off_blocks(self):self.denied(self.mutate('power','mInteractive=true','mInteractive=false'))
    def test_malformed_extra_window_header(self):self.denied(self.mutate('windows','  mGlobalConfiguration','  Window #x invalid\n  mGlobalConfiguration'))
    def test_visible_toast_blocks(self):v=scene();v['windows']=v['windows'].replace(window(1,'a1','fixture.other',hidden=True),window(1,'a1','fixture.other','TOAST'));self.denied(v)
    def test_missing_metadata_source(self):v=scene();del v['input'];self.denied(v)
    def test_alpha_out_of_range_blocks(self):self.denied(self.mutate('input','alpha=1,','alpha=2,'))
    def test_negative_settings_bounds(self):self.denied(self.mutate('windows','frame=[0,0][100,200]','frame=[-1,0][100,200]'))
    def test_appended_truncated_dispatcher(self):v=scene();v['input']+='Input Dispatcher State:\n';self.denied(v)
    def test_nontext_metadata_value(self):v=scene();v['windows']=None;self.denied(v)


class ReadonlyAdmissionTests(unittest.TestCase):
    def gate(self,**changes):
        from test_device_profiles import IDENTITY,SERIAL
        from phonelife.device_profiles import PhysicalDeviceProfile
        p=PhysicalDeviceProfile(SERIAL,IDENTITY,True)
        control=dict(safety_evidence_readable=True,requires_manual_review=False,emergency_stopped=False,device_lease_free=True)
        control.update(changes)
        return readonly_capture_gate(p,IDENTITY,scene(),control,connected=True)
    def test_recommendation_is_not_permission(self):
        r=self.gate();self.assertEqual(r['state'],'READY_FOR_READONLY_CAPTURE_APPROVAL');self.assertFalse(r['raw_capture_authorized']);self.assertFalse(r['physical_input_allowed']);self.assertFalse(r['worker_launch_allowed'])
    def test_emergency_stop(self):self.assertNotEqual(self.gate(emergency_stopped=True)['state'],'READY_FOR_READONLY_CAPTURE_APPROVAL')
    def test_review_barrier(self):self.assertNotEqual(self.gate(requires_manual_review=True)['state'],'READY_FOR_READONLY_CAPTURE_APPROVAL')
    def test_unreadable_storage(self):self.assertNotEqual(self.gate(safety_evidence_readable=False)['state'],'READY_FOR_READONLY_CAPTURE_APPROVAL')
    def test_active_lease(self):self.assertNotEqual(self.gate(device_lease_free=False)['state'],'READY_FOR_READONLY_CAPTURE_APPROVAL')
    def test_unknown_review(self):self.assertNotEqual(self.gate(requires_manual_review=None)['state'],'READY_FOR_READONLY_CAPTURE_APPROVAL')
    def test_identity_mismatch(self):
        from test_device_profiles import IDENTITY,SERIAL
        from phonelife.device_profiles import PhysicalDeviceProfile
        r=readonly_capture_gate(PhysicalDeviceProfile(SERIAL,IDENTITY,True),{},scene(),{},connected=True);self.assertNotEqual(r['state'],'READY_FOR_READONLY_CAPTURE_APPROVAL')
    def test_disconnected(self):
        from test_device_profiles import IDENTITY,SERIAL
        from phonelife.device_profiles import PhysicalDeviceProfile
        r=readonly_capture_gate(PhysicalDeviceProfile(SERIAL,IDENTITY,True),IDENTITY,scene(),{},connected=False);self.assertNotEqual(r['state'],'READY_FOR_READONLY_CAPTURE_APPROVAL')


class NativeAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_unknown_blocks_before_capture(self):
        from test_device_profiles import FakeAdb,IDENTITY,SERIAL
        from phonelife.device_profiles import PhysicalDeviceProfile,ProfileBoundReadOnlyPhone,DeviceProfileError
        fake=FakeAdb();fake.windows=scene()['windows'].replace('ty=BASE_APPLICATION','ty=2315')
        with self.assertRaises(DeviceProfileError):await ProfileBoundReadOnlyPhone(PhysicalDeviceProfile(SERIAL,IDENTITY,True),fake).observe()
        self.assertFalse(any(c[1][0]=='exec-out' for c in fake.calls))
    async def test_cli_capture_option_does_not_capture(self):
        import argparse,tempfile
        from phonelife.device_profiles import diagnose
        from test_device_profiles import FakeAdb,SERIAL
        fake=FakeAdb();fake.journal=[]
        with tempfile.TemporaryDirectory() as d,patch('phonelife.device_profiles.ReadOnlyAdb',return_value=fake),patch('builtins.print'):
            await diagnose(argparse.Namespace(serial=SERIAL,output=Path(d)/'out',consent_read_only=True,capture_settings=True))
            r=json.loads((Path(d)/'out/summary.json').read_text());self.assertEqual(r['capture_reason'],'RAW_CAPTURE_REQUIRES_C0_APPROVAL')
        self.assertFalse(any(c[1][0]=='exec-out' for c in fake.calls))


class NativeXmlPreparationTests(unittest.TestCase):
    def check(self,xml=None,**kw):
        v=dict(width=100,height=200,rotation=0,observed_ns=1,now_ns=2,before_scene='sha',after_scene='sha');v.update(kw)
        if xml is None:xml='<hierarchy rotation="0"><node package="com.android.settings" bounds="[0,0][100,200]" clickable="false" enabled="true" focusable="false" password="false" checkable="false" visible-to-user="true" editable="false"/></hierarchy>'
        return assess_xml(xml,**v)
    def test_structural_success_is_partial_only(self):self.assertEqual(self.check()['compatibility'],'PARTIAL');self.assertFalse(self.check()['trusted_for_physical_input'])
    def test_missing_safety_fields(self):self.assertIn('XML_SAFETY_ATTRIBUTE_MISSING_OR_INVALID',self.check('<hierarchy rotation="0"><node package="com.android.settings" bounds="[0,0][100,200]"/></hierarchy>')['reason_codes'])
    def test_bad_bounds(self):self.assertIn('XML_MALFORMED_OR_UNTRUSTED',self.check('<hierarchy><node bounds="bad"/></hierarchy>')['reason_codes'])
    def test_rotation_changed(self):self.assertIn('XML_ROTATION_UNVERIFIED',self.check(rotation=1)['reason_codes'])
    def test_resolution_changed(self):self.assertIn('XML_BOUNDS_UNVERIFIED',self.check(width=50)['reason_codes'])
    def test_stale_observation(self):self.assertIn('XML_STALE_OR_UNTIMED',self.check(now_ns=3_000_000_000)['reason_codes'])
    def test_action_precheck_scene_changed(self):self.assertIn('XML_SCENE_CHANGED',self.check(after_scene='changed')['reason_codes'])
    def test_mult_window_wrong_owner(self):self.assertIn('XML_WINDOW_OWNER_UNVERIFIED',self.check('<hierarchy><node package="other" bounds="[0,0][1,1]"/></hierarchy>')['reason_codes'])
    def test_dynamic_text_never_ignored(self):self.assertIn('XML_DYNAMIC_TEXT_UNVERIFIED',self.check('<hierarchy><node text="12:34" bounds="[0,0][1,1]"/></hierarchy>')['reason_codes'])
    def test_entity_forbidden(self):self.assertIn('XML_MALFORMED_OR_UNTRUSTED',self.check('<!DOCTYPE hierarchy><hierarchy/>')['reason_codes'])
