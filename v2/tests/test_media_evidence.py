"""Offline evidence boundaries, never presented as real platform delivery."""
import json,time,unittest
from types import SimpleNamespace
from dataclasses import replace
from digitallife_v2.bilibili import BilibiliUI
from digitallife_v2.media import BILIBILI
from digitallife_v2.perception import Control
from digitallife_v2.social import digest
from digitallife_v2.safety import Blocked
from test_m1 import observation
import test_media as fixture


class CommentEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.ui=BilibiliUI(SimpleNamespace());self.text='作为数字生命，我只看过采样片段。Agent 展示任务建议，并保留用户确认；实际使用感受尚不清楚。'

    def screen(self,oid,lines):
        o=observation(oid,foreground=BILIBILI+'/.UnitedBizDetailsActivity');o.controls=[];o.visual_controls={}
        def add(text,y,resource='',ocr=False):
            c=Control(oid+':'+str(len(o.controls)),BILIBILI,resource,text,'','visual.TextRegion' if ocr else 'android.widget.FrameLayout',(10,y,900,y+20),False,False,True,False,True,False)
            o.controls.append(c)
            if ocr:o.visual_controls[c.control_id]={'kind':'ocr','source_png_sha256':o.png_sha256,'crop_sha256':'fixture'}
        add('',100,BILIBILI+':id/comment_container')
        for i,line in enumerate(lines):add(line,150+i*30,ocr=True)
        return o

    def test_wrapped_full_text_and_author_from_local_OCR_are_observed(self):
        before=self.screen('before',[]);after=self.screen('after',['Test AI','LV2',self.text[:27],self.text[27:],'刚刚 浙江','回复'])
        proof=self.ui.comment_evidence(before,after,self.text,'Test AI')
        self.assertEqual(proof['method'],'author_bounded_full_text_local_OCR')
        self.assertFalse(proof['server_delivery_or_read_claimed'])

    def test_truncation_wrong_actor_or_existing_text_do_not_prove_new_comment(self):
        full=self.screen('full',['Test AI',self.text,'刚刚'])
        for after in [self.screen('cut',['Test AI',self.text[:30]+'…展开','刚刚']),self.screen('other',['Other AI',self.text,'刚刚'])]:
            self.assertIsNone(self.ui.comment_evidence(self.screen('empty',[]),after,self.text,'Test AI'))
        self.assertIsNone(self.ui.comment_evidence(full,full,self.text,'Test AI'))

    def test_visible_failure_or_open_editor_cannot_be_claimed_sent(self):
        after=self.screen('after',['Test AI',self.text,'审核中'])
        self.assertIsNone(self.ui.comment_evidence(self.screen('before',[]),after,self.text,'Test AI'))


class UnknownCommentReviewTests(unittest.TestCase):
    save=fixture.MediaTests.save
    def setUp(self):fixture.MediaTests.setUp(self)

    def attempted(self):
        oid=self.social.prepare_media(self.policy,self.binding,'post_video_comment',self.source,'One real intended draft','Observed source')
        self.social.transition(oid,'prepared','typed');self.social.transition(oid,'typed','dispatch_attempted')
        self.social.transition(oid,'dispatch_attempted','delivery_unknown',{'reason':'original observation missing'})
        return oid

    def proof(self):return {'text_sha256':digest('One real intended draft'),'actor_label_observed':True,'composer_closed':True,'new_comment_exact_normalized_OCR':True,'png_sha256':'offline-fixture','original_before_observation_id':'actual-before'}

    def test_review_keeps_unknown_history_and_can_never_replay_it(self):
        oid=self.attempted();self.social.reconcile_comment(oid,self.binding,self.source,self.proof())
        row=self.store.db.execute('SELECT status,evidence,attempted FROM social_outbox WHERE id=?',(oid,)).fetchone()
        self.assertEqual(row[0],'sent_observed');self.assertEqual(json.loads(row[1])['original_unknown_evidence']['reason'],'original observation missing')
        self.assertIsNotNone(row[2]);self.assertTrue(self.store.db.execute("SELECT 1 FROM events WHERE kind='social_delivery_unknown'").fetchone())
        with self.assertRaises(Blocked):self.social.reconcile_comment(oid,self.binding,self.source,self.proof())
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM social_outbox').fetchone()[0],1)

    def test_wrong_draft_or_target_does_not_clear_unknown(self):
        oid=self.attempted();proof=self.proof();proof['text_sha256']=digest('Different draft')
        with self.assertRaises(Blocked):self.social.reconcile_comment(oid,self.binding,self.source,proof)
        with self.assertRaises(Blocked):self.social.reconcile_comment(oid,replace(self.binding,account='98765432'),self.source,self.proof())
        self.assertEqual(self.store.db.execute('SELECT status FROM social_outbox WHERE id=?',(oid,)).fetchone()[0],'delivery_unknown')
