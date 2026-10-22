"""offline_fixture, including explicit assumptions; no phone or model calls."""
import copy
import json
import unittest
from acceptance.native_target_observer import (ROW_PATH,TITLE_PATH,SELECTOR_SHA256,
    visibility_evidence,validate_packet,touch_point_assessment)


def packet():
    node={'bounds':[0,2206,1240,2395],'visible':True,'editable':False,'clickable':True,
        'enabled':True,'checkable':False,'password':False,'long_clickable':False}
    f={'sample_started_ns':20,'sample_finished_ns':30,'window_id':1,'window_layer':3,
       'window_bounds':[0,0,1240,2772],'window_active':True,'window_focused':True,'package':'com.android.settings',
       'width':1240,'height':2772,'rotation':0,'ancestry_visible':True,'same_window':True,'refreshed':True,
       'row':node,'title':dict(node,bounds=[280,2224,1030,2370],clickable=False)}
    return {'schema':'find-x6-target-observer/1','nonce':'a'*32,'selector_sha256':SELECTOR_SHA256,
        'row_path':ROW_PATH,'title_path':TITLE_PATH,'frames':[f,dict(copy.deepcopy(f),sample_started_ns=31,sample_finished_ns=40)],'disconnected':True}


class ObserverPrototypeTests(unittest.TestCase):
    def check(self,p=None,**kw):
        args={'nonce':'a'*32,'host_started_ns':10,'host_received_ns':50,'now_ns':60};args.update(kw)
        return validate_packet(json.dumps(p or packet()).encode(),**args)
    def denied(self,p,**kw):
        with self.assertRaises((ValueError,TypeError)):self.check(p,**kw)
    def test_root_not_covered_by_child_filter(self):
        v=visibility_evidence(None,path=(),actual_dumper_sha256='a'*64,reviewed_dumper_sha256='a'*64,path_mapping_verified=True)
        self.assertFalse(v['condition_satisfied']);self.assertIsNone(v['native_value'])
    def test_descendant_filter_requires_device_implementation_and_path_review(self):
        args=dict(path=(0,2),actual_dumper_sha256='a'*64,reviewed_dumper_sha256='a'*64,path_mapping_verified=True)
        self.assertEqual(visibility_evidence(None,**args)['level'],'REVIEWED_DUMPER_FILTER_CONDITIONAL')
        for key,value in [('actual_dumper_sha256',None),('actual_dumper_sha256','b'*64),('path_mapping_verified',False)]:
            self.assertFalse(visibility_evidence(None,**(args|{key:value}))['condition_satisfied'])
    def test_missing_or_false_visibility_never_coerced(self):
        self.assertFalse(visibility_evidence(None)['condition_satisfied'])
        self.assertFalse(visibility_evidence(False,path=(0,))['condition_satisfied'])
        self.assertTrue(visibility_evidence(True)['condition_satisfied'])
    def test_native_packet_consistency_is_not_input_permission(self):
        result=self.check();self.assertTrue(result['native_target_sample_consistent']);self.assertFalse(result['execution_allowed'])
    def test_hidden_target_or_hidden_ancestor_rejects(self):
        for key in ('row','ancestry_visible'):
            p=packet()
            if key=='row':p['frames'][0]['row']['visible']=False
            else:p['frames'][0][key]=False
            self.denied(p)
    def test_old_xml_and_future_or_inconsistent_host_clock_rejects(self):
        for times in ({'now_ns':3_000_000_010},{'now_ns':49},{'host_started_ns':51},{'host_started_ns':True}):self.denied(packet(),**times)
    def test_fast_wms_cannot_replace_refreshed_target(self):
        p=packet();p['frames'][1]['refreshed']=False;self.denied(p)
        p=packet();p['frames'][1]['row']['enabled']=False;self.denied(p)
    def test_focus_orientation_window_and_bounds_change_rejects(self):
        for key,value in [('window_focused',False),('rotation',1),('window_id',2),('width',1080)]:
            p=packet();p['frames'][1][key]=value;self.denied(p)
        p=packet();p['frames'][1]['row']['bounds']=[0,2207,1240,2395];self.denied(p)
    def test_owner_mismatch_password_or_unapproved_selector_rejects(self):
        for key,value in [('package','foreign.app'),('same_window',False)]:
            p=packet();p['frames'][1][key]=value;self.denied(p)
        p=packet();p['frames'][1]['title']['password']=True;self.denied(p)
        p=packet();p['selector_sha256']='b'*64;self.denied(p)
    def test_remote_timestamp_not_a_host_freshness_reset(self):
        p=packet();p['frames'][1]['sample_finished_ns']=1000;self.denied(p)
        p=packet();p['frames'][1]['sample_started_ns']=1;self.denied(p)
    def test_unknown_output_fields_wrong_nonce_and_cleanup_failure_rejects(self):
        for key,value in [('text','private'),('nonce','b'*32),('disconnected',False)]:
            p=packet();p[key]=value;self.denied(p)
        raw=json.dumps(packet()).encode().replace(b'"nonce":',b'"nonce":"duplicate","nonce":')
        with self.assertRaises(ValueError):validate_packet(raw,nonce='a'*32,host_started_ns=10,host_received_ns=50,now_ns=60)


def handle(i,flags=(),region=(0,0,1240,2772),**kw):
    return dict(id=i,flags=flags,region=region,channel_present=True,owner_verified=True,**kw)


class TargetTouchModelTests(unittest.TestCase):
    def assess(self,handles,**kw):return touch_point_assessment(handles,(620,2300),'settings',**kw)
    def test_spy_observes_without_becoming_normal_receiver_but_unknown_policy_denies(self):
        r=self.assess([handle('spy',['SPY']),handle('settings')],compositor_verified=True)
        self.assertEqual(r['receiver'],'settings');self.assertEqual(r['observers'],['spy'])
        self.assertEqual(r['classification'],'INSUFFICIENT_TARGET_EVIDENCE')
    def test_hidden_with_channel_does_not_mean_aosp_normal_receiver(self):
        r=self.assess([handle('hidden',['NOT_VISIBLE']),handle('settings')],compositor_verified=True)
        self.assertEqual(r['classification'],'TARGET_INPUT_SAFE');self.assertFalse(r['execution_allowed'])
    def test_visible_touch_interceptor_above_settings_blocks(self):
        r=self.assess([handle('interceptor'),handle('settings')],compositor_verified=True)
        self.assertEqual(r['classification'],'TARGET_INPUT_BLOCKED')
    def test_unknown_input_owner_keeps_target_insufficient(self):
        h=handle('unowned');h['owner_verified']=False
        self.assertEqual(self.assess([h,handle('settings')],compositor_verified=True)['classification'],'INSUFFICIENT_TARGET_EVIDENCE')
    def test_local_region_outside_target_not_global_window_exception(self):
        h=handle('other',region=(0,0,500,500))
        self.assertEqual(self.assess([h,handle('settings')],compositor_verified=True)['classification'],'TARGET_INPUT_SAFE')
        self.assertEqual(touch_point_assessment([h,handle('settings')],(250,250),'settings',compositor_verified=True)['classification'],'TARGET_INPUT_BLOCKED')
    def test_multilayer_order_and_visual_evidence_are_independent(self):
        hs=[handle('hidden',['NOT_VISIBLE']),handle('outside',region=(0,0,1,1)),handle('settings'),handle('lower')]
        self.assertEqual(self.assess(hs)['classification'],'INSUFFICIENT_TARGET_EVIDENCE')
        self.assertEqual(self.assess(hs,compositor_verified=True)['classification'],'TARGET_INPUT_SAFE')
