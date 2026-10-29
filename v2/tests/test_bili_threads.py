from dataclasses import replace
from types import SimpleNamespace
from pathlib import Path
import hashlib,json,unittest,xml.etree.ElementTree as ET
from digitallife_v2.bili_threads import ThreadSources
from digitallife_v2.media import BILIBILI
from digitallife_v2.safety import Blocked
from digitallife_v2.bilibili import BilibiliUI
from digitallife_v2.perception import Control
from test_m1 import observation
import test_media as fixture


class PublicThreadTests(unittest.TestCase):
    save=fixture.MediaTests.save
    def setUp(self):
        fixture.MediaTests.setUp(self)
        self.root_text='I am an AI reviewing an offline fixture with original public evidence.'
        self.root_id=self.social.prepare_media(self.policy,self.binding,'post_video_comment',self.source,self.root_text,'Offline fixture')
        self.social.transition(self.root_id,'prepared','typed');self.social.transition(self.root_id,'typed','dispatch_attempted');self.social.transition(self.root_id,'dispatch_attempted','sent_observed',{'offline_fixture':True})
        self.actor={'id':self.binding.account,'name':'Fixture AI'};self.sender='87654321';self.question='Why preserve a user confirmation step?'
        self.detail=self.capture('detail',[('primary_message',self.root_text),('primary_message',self.question)])
        self.profile=self.capture('profile',[('',self.sender)])
        self.threads=ThreadSources(self.store)
        self.thread_id=self.bind()

    def capture(self,name,fields):
        root=ET.Element('hierarchy')
        for rid,text in fields:
            parent=root
            if name=='profile' and not rid:
                parent=ET.SubElement(root,'node',{'resource-id':BILIBILI+':id/bottom_tags_layout','package':BILIBILI})
            ET.SubElement(parent,'node',{'resource-id':BILIBILI+':id/'+rid if rid else '', 'text':text,'package':BILIBILI,'clickable':'true' if name=='profile' else 'false'})
        path=self.root/(name+'.xml');path.write_bytes(ET.tostring(root))
        image=Path(self.captures[0]['png_path'])
        return SimpleNamespace(observation_id=name,xml_path=str(path),xml_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),png_path=str(image),png_sha256=hashlib.sha256(image.read_bytes()).hexdigest())

    def bind(self):return self.threads.bind(self.source,self.root_id,self.sender,'Public sender',self.question,self.actor,(self.detail,self.detail),self.profile)

    def test_UI_thread_fingerprint_is_stable_across_reobservation(self):
        a=self.threads.get(self.thread_id);b=self.threads.get(self.bind())
        self.assertEqual(a['ui_thread_key'],b['ui_thread_key']);self.assertNotEqual(a['thread_id'],b['thread_id'])
        self.assertIn('UI_fingerprint',a['identity_kind'])

    def test_actual_thread_sources_are_required_for_reply_preparation(self):
        t=self.threads.get(self.thread_id);binding=replace(self.binding,kind='comment_thread',peer=self.video+':'+t['ui_thread_key'])
        oid=self.social.prepare_media(self.policy,binding,'reply_video_comment',self.source,'A source-bound answer','Actual public question',thread_sources=[self.thread_id])
        self.social.transition(oid,'prepared','typed');self.social.before_media_send(self.policy,binding,oid)
        with self.assertRaises(Blocked):self.social.prepare_media(self.policy,replace(binding,peer=self.video+':42'),'reply_video_comment',self.source,'Wrong thread','Fixture',thread_sources=[self.thread_id])

    def test_tampered_thread_metadata_or_native_XML_cannot_be_used(self):
        self.store.db.execute('UPDATE content_segments SET body=replace(body,?,?) WHERE id=?',(self.question,'Forged question',self.thread_id[7:]));self.store.db.commit()
        with self.assertRaises(Blocked):self.threads.get(self.thread_id)
        fresh=self.bind();Path(self.profile.xml_path).write_text('<hierarchy/>')
        with self.assertRaises(Blocked):self.threads.get(fresh)

    def test_self_reply_or_missing_real_parent_is_rejected(self):
        with self.assertRaises(Blocked):self.threads.bind(self.source,self.root_id,self.actor['id'],'Self',self.question,self.actor,(self.detail,self.detail),self.profile)
        with self.assertRaises(Blocked):self.threads.bind(self.source,'0'*32,self.sender,'Sender',self.question,self.actor,(self.detail,self.detail),self.profile)

    def split_captures(self,wrong_entry=False):
        preview='Public sender: '+self.question
        parent=self.capture('parent',[('user_name','Fixture AI'),('primary_message',self.root_text+'\u200b 收起'),('secondary_message',preview)])
        entry=self.capture('entry',[('title','评论详情'),('user_name','Public sender'),('primary_message','Different branch' if wrong_entry else self.question)])
        incoming=self.capture('incoming',[('title','评论详情'),('user_name','Public sender'),('primary_message',self.question)])
        after=self.capture('after-profile',[('title','评论详情'),('user_name','Public sender'),('primary_message',self.question)])
        return parent,entry,incoming,after,preview

    def test_offscreen_parent_requires_separate_native_parent_entry_and_question_proofs(self):
        parent,entry,incoming,after,preview=self.split_captures()
        tid=self.threads.bind(self.source,self.root_id,self.sender,'Public sender',self.question,self.actor,(incoming,after),self.profile,parent=parent,entry=entry,preview_text=preview)
        self.assertEqual(self.threads.get(tid)['root_text'],self.root_text)
        self.assertEqual(len(self.threads.get(tid)['captures']),5)
        Path(parent.xml_path).write_text('<hierarchy/>')
        with self.assertRaises(Blocked):self.threads.get(tid)

    def test_other_branch_cannot_be_substituted_for_the_actual_parent_preview_entry(self):
        parent,entry,incoming,after,preview=self.split_captures(wrong_entry=True)
        with self.assertRaises(Blocked):self.threads.bind(self.source,self.root_id,self.sender,'Public sender',self.question,self.actor,(incoming,after),self.profile,parent=parent,entry=entry,preview_text=preview)

    def observed_old_reply(self):
        old_question='An earlier different public question'
        detail=self.capture('old-question',[('primary_message',self.root_text),('primary_message',old_question)])
        old_id=self.threads.bind(self.source,self.root_id,self.sender,'Public sender',old_question,self.actor,(detail,detail),self.profile)
        old=self.threads.get(old_id);binding=replace(self.binding,kind='comment_thread',peer=self.video+':'+old['ui_thread_key'])
        text='Earlier answer actually observed in this original parent thread.'
        oid=self.social.prepare_media(self.policy,binding,'reply_video_comment',self.source,text,'Fixture prior question',thread_sources=[old_id])
        self.social.transition(oid,'prepared','typed');self.social.transition(oid,'typed','dispatch_attempted');self.social.transition(oid,'dispatch_attempted','sent_observed',{'offline_fixture':True})
        return '回复 Public sender: '+text

    def reply_frame(self,oid,prior=None,answer=None):
        o=observation(oid,foreground=BILIBILI+'/.UnitedBizDetailsActivity');o.controls=[]
        fields=[('title','评论详情')]
        if prior:fields.append(('primary_message',prior))
        fields.extend([('user_name','Public sender'),('primary_message',self.question),('reply_button','回复')])
        if answer:fields.extend([('user_name',self.actor['name']),('primary_message','回复 Public sender: '+answer),('reply_button','回复')])
        for i,(rid,text) in enumerate(fields):
            o.controls.append(Control(oid+':'+str(i),BILIBILI,BILIBILI+':id/'+rid,text,'','android.widget.TextView',(20,100+i*70,700,140+i*70),False,False,True,False,True,False))
        return o

    def reply_ui(self):
        t=self.threads.get(self.thread_id);ui=BilibiliUI(SimpleNamespace(store=self.store));ui.actor=self.actor
        ui.reply_route={**t,'root_text':self.root_text};ui.comment_route={'source_id':self.source,'video_id':self.video,'thread_key':t['ui_thread_key']}
        return ui

    def test_clipped_prior_own_reply_uses_actual_original_thread_and_outbox_proofs(self):
        prior=self.observed_old_reply();ui=self.reply_ui();answer='A new answer to the actual current question.'
        proof=ui.reply_evidence(self.reply_frame('before',prior),self.reply_frame('after',prior,answer),answer,self.actor['name'])
        self.assertTrue(proof['new_own_reply_exact_text']);self.assertFalse(proof['parent_text_currently_present'])
        self.assertEqual(proof['bound_thread_id'],self.thread_id)
        self.assertEqual(proof['parent_identity_proof'],'bound_original_thread_and_known_prior_own_replies')

    def test_unrecorded_prior_message_or_tampered_original_thread_cannot_confirm(self):
        prior=self.observed_old_reply();ui=self.reply_ui();answer='A new answer to the actual current question.'
        self.assertIsNone(ui.reply_evidence(self.reply_frame('before',prior),self.reply_frame('after','Unverified different parent',answer),answer,self.actor['name']))
        Path(self.profile.xml_path).write_bytes(b'<hierarchy/>')
        self.assertIsNone(ui.reply_evidence(self.reply_frame('before',prior),self.reply_frame('after',prior,answer),answer,self.actor['name']))

    def test_correcting_an_invalid_legacy_sender_uid_does_not_reoffer_an_answered_question(self):
        thread=self.threads.get(self.thread_id);binding=replace(self.binding,kind='comment_thread',peer=self.video+':'+thread['ui_thread_key'])
        oid=self.social.prepare_media(self.policy,binding,'reply_video_comment',self.source,'Previously attempted answer','Fixture',thread_sources=[self.thread_id])
        old={**thread,'sender_uid':'567890','ui_thread_key':'123456789012345678'};old.pop('thread_id')
        raw=json.dumps(old,ensure_ascii=False)
        self.store.db.execute('UPDATE content_segments SET body=?,summary=? WHERE id=?',(raw,hashlib.sha256(raw.encode()).hexdigest(),self.thread_id[7:]))
        self.store.db.execute('UPDATE social_outbox SET trigger_id=? WHERE id=?',(self.video+':'+old['ui_thread_key'],oid));self.store.db.commit()
        fresh=self.threads.get(self.bind())
        with self.assertRaises(Blocked):self.threads.get(self.thread_id)
        self.assertTrue(self.threads.already_replied(fresh));self.assertEqual(self.threads.offered(),[])

    def test_old_reply_parent_proof_uses_a_separate_verified_identity_correction(self):
        prior=self.observed_old_reply();row=self.store.db.execute("SELECT id,sources FROM social_outbox WHERE kind='reply_video_comment'").fetchone()
        old_id=next(s for s in json.loads(row[1]) if s.startswith('thread:'));old=self.threads.get(old_id)
        detail=self.capture('corrected-old-detail',[('primary_message',self.root_text),('primary_message',old['text'])])
        corrected=self.threads.bind(self.source,self.root_id,self.sender,'Public sender',old['text'],self.actor,(detail,detail),self.profile)
        invalid={**old,'sender_uid':'567890'};invalid.pop('thread_id');raw=json.dumps(invalid,ensure_ascii=False)
        self.store.db.execute('UPDATE content_segments SET body=?,summary=? WHERE id=?',(raw,hashlib.sha256(raw.encode()).hexdigest(),old_id[7:]));self.store.db.commit()
        ui=self.reply_ui();answer='New answer based on the verified current question.'
        self.assertIsNone(ui.reply_evidence(self.reply_frame('before',prior),self.reply_frame('after',prior,answer),answer,self.actor['name']))
        self.store.db.execute('UPDATE social_outbox SET evidence=? WHERE id=?',(json.dumps({'corrected_thread_source_id':corrected}),row[0]));self.store.db.commit()
        self.assertTrue(ui.reply_evidence(self.reply_frame('before',prior),self.reply_frame('after',prior,answer),answer,self.actor['name'])['same_original_parent'])

    def native_thread(self):
        parent,entry,incoming,after,preview=self.split_captures()
        return self.threads.bind(self.source,self.root_id,None,'Public sender',self.question,self.actor,(incoming,after),None,parent=parent,entry=entry,preview_text=preview)

    def test_native_thread_can_bind_without_visiting_the_sender_profile(self):
        t=self.threads.get(self.native_thread())
        self.assertIsNone(t['sender_uid']);self.assertEqual(len(t['captures']),4)
        self.assertIn('native_thread',t['identity_kind'])
        Path(t['captures'][1]['xml_path']).write_bytes(b'<hierarchy/>')
        with self.assertRaises(Blocked):self.threads.get(t['thread_id'])

    def test_omitting_sender_uid_still_requires_the_native_parent_and_entry_proofs(self):
        with self.assertRaises(Blocked):self.threads.bind(self.source,self.root_id,None,'Public sender',self.question,self.actor,(self.detail,self.detail),None)

    def test_optional_sender_uid_does_not_reoffer_a_previously_answered_native_question(self):
        old=self.threads.get(self.thread_id);binding=replace(self.binding,kind='comment_thread',peer=self.video+':'+old['ui_thread_key'])
        self.social.prepare_media(self.policy,binding,'reply_video_comment',self.source,'Already answered','Fixture',thread_sources=[self.thread_id])
        native=self.threads.get(self.native_thread())
        self.assertTrue(self.threads.already_replied(native));self.assertEqual(self.threads.offered(),[])
