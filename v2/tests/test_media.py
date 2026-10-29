from dataclasses import replace
import hashlib,json,tempfile,time,unittest
from pathlib import Path
from digitallife_v2.media import BILIBILI,VideoBinding,VideoSources,MediaPolicy,MediaStore,canonical_video
from digitallife_v2.social import Binding,SocialPolicy
from digitallife_v2.store import LifeStore
from digitallife_v2.safety import Blocked
from test_m1 import config


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.cfg=config(self.root)
        directory=self.root/'fixture';directory.mkdir();self.store=LifeStore(self.cfg.data,str(directory),self.cfg.serial,'fixture');self.addCleanup(self.store.close)
        self.policy=MediaPolicy(self.cfg);self.social=MediaStore(self.store)
        self.video='BV1234567890';self.url='https://www.bilibili.com/video/'+self.video
        self.binding=VideoBinding(BILIBILI,'12345678',self.video,'video',True,('profile','video'),time.time(),'fixture','Video title')
        self.group=Binding('com.tencent.mobileqq','12345678','23456789','group',True,('actual-group-info',),time.time(),'fixture','Test group')
        self.grant={'version':1,'serial':self.cfg.serial,'enabled':True,'accounts':{BILIBILI:'12345678','com.tencent.mobileqq':'12345678'},'comments':{'post_video_comment':True,'reply_video_comment':True,'following_only':True},'shares':{'group_video_share':True,'allowed_groups':['com.tencent.mobileqq/23456789']},'quiet_hours':['00:00','00:00']}
        self.save();self.segment='1'*32
        image=self.root/'fixture.png';image.write_bytes(b'offline fixture, not a device image')
        sha=hashlib.sha256(image.read_bytes()).hexdigest()
        self.body={'samples':[{'foreground':BILIBILI+'/.Video','observation_id':x,'png_path':str(image),'png_sha256':sha,'redacted':False,'incomplete':False,'texts':['Video title']} for x in ('frame-1','frame-2')],'time_range_seconds':[0,5]}
        summary={'summary':'Observed fixture content','evidence_ids':['frame-1','frame-2'],'uncertainty':'Offline fixture'}
        self.store.db.execute('INSERT INTO content_segments VALUES(?,?,?,?,?,?,?,?)',(self.segment,self.store.session,'watch_video','live_scripted',time.time(),json.dumps(self.body),json.dumps(summary),'summarized'));self.store.db.commit()
        self.captures=[{'app':BILIBILI,'kind':'native_share','url':self.url,'title':'Video title','observation_id':x,'png_path':str(image),'png_sha256':sha} for x in ('share-before','share-after')]
        self.source=VideoSources(self.store).bind(self.segment,public_url=self.url,title='Video title',uploader='98765432',followed=True,captures=self.captures)

    def save(self):self.policy.path.write_text(json.dumps(self.grant),encoding='utf-8')
    def prepare(self,kind='post_video_comment',binding=None,text='One source-grounded comment'):
        return self.social.prepare_media(self.policy,binding or self.binding,kind,self.source,text,'Real observed context')

    def test_canonical_public_link_strips_tracking_and_rejects_other_sources(self):
        self.assertEqual(canonical_video(self.url+'?share_source=copy_link'),(self.video,self.url))
        for bad in ['https://www.bilibili.com.evil.test/video/'+self.video,'http://www.bilibili.com/video/'+self.video,'https://user@www.bilibili.com/video/'+self.video,'https://www.bilibili.com/space/123','bilibili://video/123','https://www.bilibili.com:1234/video/'+self.video]:
            with self.subTest(url=bad),self.assertRaises(Blocked):canonical_video(bad)

    def test_visible_author_name_can_bind_a_watched_video_without_profile_uid(self):
        segment='3'*32
        self.store.db.execute('INSERT INTO content_segments SELECT ?,session,kind,source_type,started,?,summary,status FROM content_segments WHERE id=?',
            (segment,json.dumps(self.body),self.segment));self.store.db.commit()
        source=VideoSources(self.store).bind(segment,public_url=self.url,title='Video title',uploader=None,
            uploader_name='Visible fixture author',followed=True,captures=self.captures)
        result=VideoSources(self.store).get(source)
        self.assertIsNone(result['uploader']);self.assertEqual(result['uploader_name'],'Visible fixture author')
        self.assertEqual(result['video_id'],self.video)

    def test_missing_author_name_and_uid_does_not_invent_an_uploader(self):
        with self.assertRaisesRegex(Blocked,'ANCHOR_UNVERIFIED'):
            VideoSources(self.store).bind(self.segment,public_url=self.url,title='Video title',uploader=None,followed=True,captures=self.captures)

    def test_scope_requires_account_kind_and_current_follow_relationship(self):
        self.assertTrue(self.policy.check(self.binding,'post_video_comment'))
        for b in [replace(self.binding,account='99999999'),replace(self.binding,existing_contact=False),replace(self.binding,verified_at=time.time()-60)]:
            with self.assertRaises(Blocked):self.policy.check(b,'post_video_comment')
        self.grant['comments']['post_video_comment']=False;self.save()
        with self.assertRaises(Blocked):self.policy.check(self.binding,'post_video_comment')

    def test_m4_group_post_permission_does_not_grant_video_share(self):
        path=self.cfg.data/'social.private.json';path.write_text(json.dumps({'version':1,'serial':self.cfg.serial,'enabled':True,'accounts':{'com.tencent.mobileqq':'12345678'},'groups':{'mode':'all_existing_groups','group_post':True},'quiet_hours':['00:00','00:00']}),encoding='utf-8')
        self.assertTrue(SocialPolicy(self.cfg).check(self.group,'group_post'))
        self.grant['shares']['group_video_share']=False;self.save()
        with self.assertRaisesRegex(Blocked,'SHARE_NOT_ALLOWED'):self.policy.check(self.group,'group_video_share')

    def test_video_share_does_not_require_enabling_ordinary_group_posts(self):
        self.assertTrue(self.policy.check(self.group,'group_video_share'))
        with self.assertRaisesRegex(Blocked,'SHARE_NOT_ALLOWED'):self.policy.check(replace(self.group,peer='34567890'),'group_video_share')
        with self.assertRaises(Blocked):self.policy.check(replace(self.group,existing_contact=False),'group_video_share')

    def test_wrong_video_and_unbound_reply_thread_are_rejected(self):
        with self.assertRaisesRegex(Blocked,'SOURCE_THREAD_MISMATCH'):self.prepare(binding=replace(self.binding,peer='BVabcdefghij'))
        thread=replace(self.binding,kind='comment_thread',peer=self.video+':123456')
        with self.assertRaisesRegex(Blocked,'CONTEXT_MISSING'):self.prepare('reply_video_comment',thread)

    def test_source_brackets_must_agree_and_watch_title_cannot_change(self):
        self.captures[1]['url']='https://www.bilibili.com/video/BVabcdefghij'
        with self.assertRaises(Blocked):VideoSources(self.store).bind(self.segment,public_url=self.url,title='Video title',uploader='98765432',followed=True,captures=self.captures)
        self.body['samples'][-1]['texts']=['Another video']
        self.store.db.execute('UPDATE content_segments SET body=? WHERE id=?',(json.dumps(self.body),self.segment));self.store.db.commit()
        self.captures[1]['url']=self.url
        with self.assertRaisesRegex(Blocked,'WATCH_CHANGED'):VideoSources(self.store).bind(self.segment,public_url=self.url,title='Video title',uploader='98765432',followed=True,captures=self.captures)

    def test_missing_or_changed_watch_image_cannot_be_exported(self):
        Path(self.body['samples'][0]['png_path']).write_bytes(b'changed')
        with self.assertRaisesRegex(Blocked,'WATCH_EVIDENCE'):VideoSources(self.store).get(self.source)

    def test_scope_revocation_after_typing_prevents_public_comment(self):
        oid=self.prepare();self.social.transition(oid,'prepared','typed')
        self.grant['comments']['post_video_comment']=False;self.save()
        with self.assertRaises(Blocked):self.social.before_media_send(self.policy,self.binding,oid)
        self.assertIsNone(self.store.db.execute('SELECT attempted FROM social_outbox WHERE id=?',(oid,)).fetchone()[0])

    def test_same_video_and_group_cannot_be_resent_with_different_caption(self):
        self.prepare('group_video_share',self.group,'First caption')
        with self.assertRaisesRegex(Blocked,'INTENT_ALREADY_RECORDED'):self.prepare('group_video_share',self.group,'Different caption')

    def test_unknown_comment_freezes_its_thread_and_never_retries(self):
        oid=self.prepare();self.social.transition(oid,'prepared','typed');self.social.before_media_send(self.policy,self.binding,oid)
        self.social.transition(oid,'typed','dispatch_attempted');self.social.transition(oid,'dispatch_attempted','delivery_unknown')
        with self.assertRaises(Blocked):self.prepare(text='Try again')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM social_outbox').fetchone()[0],1)

    def test_link_fallback_cannot_change_or_omit_the_video_URL(self):
        from digitallife_v2.media_agent import MediaLinkStore
        outbox=MediaLinkStore(self.store,self.source)
        for bad in ['No link','Another link https://evil.test/video',self.url+' trailing text']:
            with self.assertRaisesRegex(Blocked,'LINK_MISSING_OR_CHANGED'):
                outbox.prepare(self.policy,self.group,'group_video_share','ignored',bad,'Observed content',[self.source])
        oid=outbox.prepare(self.policy,self.group,'group_video_share','ignored','Based on the sampled segment '+self.url,'Observed content',[self.source])
        self.assertTrue(oid)

    def test_tighter_existing_global_limits_also_apply_to_media(self):
        p=self.cfg.data/'social.private.json';p.write_text(json.dumps({'version':1,'serial':self.cfg.serial,'limits':{'hour':2,'day':7}}),encoding='utf-8')
        limits=self.policy.check(self.binding,'post_video_comment');self.assertEqual(limits['hour'],2);self.assertEqual(limits['day'],7)

    def test_native_video_XML_is_revalidated_when_a_source_is_reused(self):
        xml=self.root/'fixture.xml';xml.write_text('<hierarchy><node resource-id="'+BILIBILI+':id/desc" text="'+self.video+' public description"/></hierarchy>')
        self.store.db.execute('UPDATE content_segments SET body=? WHERE id=?',(json.dumps(self.body),self.segment));self.store.db.commit()
        for c in self.captures:c.update(kind='native_video_identity',xml_path=str(xml),xml_sha256=hashlib.sha256(xml.read_bytes()).hexdigest())
        VideoSources(self.store).bind(self.segment,public_url=self.url,title='Video title',uploader='98765432',followed=True,captures=self.captures)
        self.assertEqual(VideoSources(self.store).get(self.source)['video_id'],self.video)
        xml.write_text('<hierarchy/>')
        with self.assertRaises(Blocked):VideoSources(self.store).get(self.source)
