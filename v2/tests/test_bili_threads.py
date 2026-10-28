from dataclasses import replace
from types import SimpleNamespace
from pathlib import Path
import hashlib,unittest,xml.etree.ElementTree as ET
from digitallife_v2.bili_threads import ThreadSources
from digitallife_v2.media import BILIBILI
from digitallife_v2.safety import Blocked
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
        for rid,text in fields:ET.SubElement(root,'node',{'resource-id':BILIBILI+':id/'+rid if rid else '', 'text':text})
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
