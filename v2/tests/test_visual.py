from dataclasses import replace
import hashlib
from pathlib import Path
import tempfile
import unittest
from PIL import Image, ImageDraw
from digitallife_v2.visual import add_control, rebind_region, verify_frame, grounded_target
from digitallife_v2.safety import Blocked, guard
from test_m1 import observation


class VisualTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def page(self,oid,outside=False,inside=False):
        p=self.root/(oid+'.png');picture=Image.new('RGB',(720,1600),'white')
        draw=ImageDraw.Draw(picture);draw.rectangle((100,200,250,260),fill='black')
        if outside:draw.rectangle((0,0,30,30),fill='red')
        if inside:draw.rectangle((130,210,150,230),fill='red')
        picture.save(p)
        return replace(observation(oid),controls=[],png_path=str(p),png_sha256=hashlib.sha256(p.read_bytes()).hexdigest())

    def test_ocr_is_explicitly_unknown_native_role_and_requires_current_pixels(self):
        obs=self.page('one');c=add_control(obs,'真实屏幕文字',[90,190,270,280],.7)
        self.assertIsNone(c.enabled);self.assertIsNone(c.clickable)
        self.assertEqual(guard({'type':'tap','control_id':c.control_id},obs,{},full_access=True),c)
        with self.assertRaises(Blocked):guard({'type':'tap','control_id':c.control_id},obs,{},full_access=False)
        obs.controls[0]=replace(c,bounds=(0,0,20,20))
        with self.assertRaises(Blocked):guard({'type':'tap','control_id':c.control_id},obs,{},full_access=True)

    def test_grounded_region_can_rebind_only_when_its_pixels_are_unchanged(self):
        before=self.page('before');c=add_control(before,'',[90,190,270,280],.95,'vision','chat_editor')
        fresh=self.page('fresh',outside=True)
        new=rebind_region(before,fresh,c)
        self.assertEqual(guard({'type':'tap','control_id':new.control_id},fresh,{},full_access=True),new)
        with self.assertRaisesRegex(Blocked,'PIXELS_CHANGED'):
            rebind_region(before,self.page('changed',inside=True),c)

    def test_tampered_source_image_is_rejected(self):
        obs=self.page('one');c=add_control(obs,'文字',[90,190,270,280],.7)
        Image.new('RGB',(720,1600),'black').save(obs.png_path)
        with self.assertRaisesRegex(Blocked,'SOURCE_UNVERIFIED'):
            guard({'type':'tap','control_id':c.control_id},obs,{},full_access=True)

    def test_inferred_group_scroll_does_not_invent_native_scrollability(self):
        obs=self.page('scroll')
        c=add_control(obs,'',[50,180,670,1400],.95,'vision','group_info_scroll')
        self.assertIsNone(c.scrollable)
        self.assertEqual(guard({'type':'swipe','control_id':c.control_id,'direction':'up'},obs,{},full_access=True),c)
        other=add_control(obs,'',[50,180,670,1400],.95,'vision','chat_editor')
        with self.assertRaises(Blocked):guard({'type':'swipe','control_id':other.control_id,'direction':'up'},obs,{},full_access=True)

    def test_editor_hit_patch_keeps_full_grounding_and_rechecks_actual_click_pixels(self):
        before=self.page('editor')
        target=grounded_target(before,[90,190,270,280],.95,'chat_editor')
        self.assertEqual(target.bounds,(119,244,151,276))
        self.assertEqual(before.visual_controls[target.control_id]['grounded_bounds'],[90,190,270,280])
        # Animated content away from the actual hit must not invalidate the hit.
        fresh=self.page('animated',inside=True)
        rebound=rebind_region(before,fresh,target)
        self.assertEqual(guard({'type':'tap','control_id':rebound.control_id},fresh,{},full_access=True),rebound)
        with Image.open(fresh.png_path) as picture:
            ImageDraw.Draw(picture).rectangle(target.bounds,fill='red');picture.save(fresh.png_path)
        with self.assertRaisesRegex(Blocked,'PIXELS_CHANGED'):rebind_region(before,fresh,target)

    def test_new_frame_retains_only_unchanged_recognized_regions(self):
        before=self.page('before');c=add_control(before,'文字',[90,190,270,280],.7)
        fresh=self.page('fresh',outside=True)
        verified=verify_frame(before,fresh.png_path)
        self.assertEqual(verified.png_sha256,fresh.png_sha256)
        self.assertEqual(guard({'type':'tap','control_id':c.control_id},verified,{},full_access=True),c)
        changed=self.page('changed',inside=True)
        verify_frame(verified,changed.png_path)
        self.assertFalse(verified.visual_controls)

    def test_removed_regions_cannot_make_new_targets_alias_surviving_ocr(self):
        obs=self.page('before')
        changed=add_control(obs,'old',[90,190,270,280],.7)
        surviving=add_control(obs,'kept',[300,400,400,450],.7)
        verify_frame(obs,self.page('fresh',inside=True).png_path)
        self.assertNotIn(changed.control_id,obs.visual_controls)
        target=grounded_target(obs,[90,190,270,280],.95,'chat_editor')
        self.assertNotEqual(target.control_id,surviving.control_id)
        self.assertEqual(len({c.control_id for c in obs.controls}),len(obs.controls))
        self.assertEqual(obs.visual_controls[surviving.control_id]['kind'],'ocr')

    def test_model_context_labels_ocr_and_keeps_region_proofs_local(self):
        import json
        from digitallife_v2.brain import LifeBrain
        from digitallife_v2.store import LifeStore
        from test_m1 import config
        cfg=config(self.root);cfg.full_access=True;cfg.vision=False
        directory=self.root/'session';directory.mkdir()
        store=LifeStore(cfg.data,str(directory),cfg.serial,'fixture');self.addCleanup(store.close)
        obs=self.page('ocr')
        for n in range(40):add_control(obs,'Screen text '+str(n),[90,190,270,280],.7)
        data=json.loads(LifeBrain(cfg,store).payload(obs,{},[])['messages'][1]['content'])
        self.assertTrue(data['page_text_from_ocr']);self.assertNotIn('page_text_from_xml',data)
        self.assertNotIn('visual_controls',data['observation'])
        self.assertEqual(data['observation']['controls'][0]['native_role'],'unknown')
