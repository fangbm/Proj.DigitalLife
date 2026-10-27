from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from digitallife_v2.social import Binding, CHINA, SocialPolicy, SocialStore, digest
from digitallife_v2.safety import Blocked
from digitallife_v2.store import LifeStore, open_database
from test_m1 import config


class SocialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.config = config(self.root)
        self.directory = self.root / 'session'; self.directory.mkdir()
        self.store = LifeStore(self.config.data, str(self.directory), self.config.serial, 'fixture')
        self.addCleanup(self.store.close)
        self.social = SocialStore(self.store); self.policy = SocialPolicy(self.config)
        self.grant = {'version': 1, 'serial': self.config.serial, 'enabled': True,
            'accounts': {'com.tencent.mobileqq': '12345678'},
            'contacts': {'mode': 'all_existing_contacts', 'auto_reply': True, 'proactive_dm': True},
            'groups': {'allowed': {}}, 'quiet_hours': ['23:00', '08:00']}
        self.save()
        self.binding = Binding('com.tencent.mobileqq', '12345678', '23456789', 'dm', True,
            ('account-observation', 'peer-profile-observation', 'chat-observation'), time.time(), 'fixture')

    def save(self):
        self.policy.path.write_text(json.dumps(self.grant), encoding='utf-8')

    def prepared(self, text='你好，测试联系人。', kind='auto_reply', trigger='incoming-message-one'):
        return self.social.prepare(self.policy, self.binding, kind, trigger, text, '根据本会话真实上下文回复', ['source-observation'])

    def typed(self):
        oid = self.prepared(); self.social.transition(oid, 'prepared', 'typed', {'observation_id': 'typed-source'})
        return oid

    def test_all_contacts_authorization_still_requires_existing_contact_account_and_action(self):
        self.assertTrue(self.policy.check(self.binding, 'auto_reply'))
        for binding, kind in [(replace(self.binding, existing_contact=False), 'auto_reply'),
            (replace(self.binding, account='99999999'), 'auto_reply'),
            (replace(self.binding, kind='group'), 'group_post'), (self.binding, 'group_post')]:
            with self.assertRaises(Blocked): self.policy.check(binding, kind)

    def test_independent_proactive_permission_and_quiet_hours(self):
        self.grant['contacts']['proactive_dm'] = False; self.save()
        with self.assertRaisesRegex(Blocked, 'CAPABILITY_NOT_ALLOWED'):
            self.policy.check(self.binding, 'proactive_dm')
        self.grant['contacts']['proactive_dm'] = True; self.save()
        late = datetime(2026, 10, 27, 23, 30, tzinfo=CHINA).timestamp()
        with self.assertRaisesRegex(Blocked, 'QUIET_HOURS'):
            self.policy.check(replace(self.binding, verified_at=late), 'proactive_dm', late)
        self.assertTrue(self.policy.check(replace(self.binding, verified_at=late), 'auto_reply', late))

    def test_all_joined_groups_requires_actual_membership_and_group_capability(self):
        self.grant['groups']={'mode':'all_existing_groups','group_post':True};self.grant['quiet_hours']=['00:00','00:00'];self.save()
        binding=replace(self.binding,kind='group')
        self.assertTrue(self.policy.check(binding,'group_post'))
        with self.assertRaisesRegex(Blocked,'CAPABILITY_NOT_ALLOWED'):
            self.policy.check(replace(binding,existing_contact=False),'group_post')

    def test_explicit_known_unsent_recovery_never_reopens_an_attempted_send(self):
        oid=self.typed();self.social.transition(oid,'typed','cancelled_before_send')
        self.social.resume_pre_send(self.policy,self.binding,oid,'fresh-draft-proof')
        self.social.before_send(self.policy,self.binding,oid)
        self.social.transition(oid,'typed','dispatch_attempted');self.social.transition(oid,'dispatch_attempted','delivery_unknown')
        with self.assertRaisesRegex(Blocked,'NOT_KNOWN_UNSENT'):
            self.social.resume_pre_send(self.policy,self.binding,oid,'different-proof')

    def test_identity_proof_must_be_fresh_and_has_sources(self):
        for binding in [replace(self.binding, verified_at=time.time()-60), replace(self.binding, observations=())]:
            with self.assertRaisesRegex(Blocked, 'IDENTITY_UNVERIFIED'):
                self.policy.check(binding, 'auto_reply')

    def test_chinese_draft_not_sent_and_journal_contains_hash_instead_of_body(self):
        oid = self.prepared()
        self.assertEqual(self.store.db.execute('SELECT status FROM social_outbox WHERE id=?', (oid,)).fetchone()[0], 'prepared')
        journal=(self.directory/'events.jsonl').read_text(encoding='utf-8')
        self.assertNotIn('你好，测试联系人', journal)
        self.assertIn('text_sha256', journal)

    def test_same_intent_or_content_cannot_generate_second_send(self):
        self.prepared()
        with self.assertRaisesRegex(Blocked, 'INTENT_ALREADY_RECORDED'): self.prepared(text='不同措辞仍是同一触发。')
        with self.assertRaisesRegex(Blocked, 'DUPLICATE_CONTENT'): self.prepared(trigger='different-event')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM social_outbox').fetchone()[0], 1)

    def test_revocation_and_account_change_rechecked_after_typing(self):
        oid = self.typed()
        self.grant['enabled'] = False; self.save()
        with self.assertRaisesRegex(Blocked, 'SOCIAL_DISABLED'): self.social.before_send(self.policy, self.binding, oid)
        self.grant['enabled'] = True; self.save()
        with self.assertRaisesRegex(Blocked, 'CONVERSATION_CHANGED'):
            self.social.before_send(self.policy, replace(self.binding, peer='99999999'), oid)
        self.assertEqual(self.store.db.execute('SELECT status FROM social_outbox').fetchone()[0], 'typed')

    def test_only_one_dispatch_attempt_and_unknown_freezes_exact_thread(self):
        oid = self.typed()
        self.social.before_send(self.policy, self.binding, oid)
        self.social.transition(oid, 'typed', 'dispatch_attempted')
        with self.assertRaises(Blocked): self.social.transition(oid, 'typed', 'dispatch_attempted')
        self.social.transition(oid, 'dispatch_attempted', 'delivery_unknown')
        with self.assertRaisesRegex(Blocked, 'UNKNOWN_SEND'):
            self.social.prepare(self.policy, self.binding, 'auto_reply', 'new-message', '新内容', '真实上下文', [])
        other=replace(self.binding, peer='34567890')
        self.assertTrue(self.social.prepare(self.policy, other, 'auto_reply', 'other-message', '另一个联系人内容', '真实上下文', []))

    def test_optout_blocks_existing_all_contacts_authorization(self):
        bid = self.social.bind(self.binding)
        self.assertTrue(self.social.opt_out(bid, '请不要再联系我'))
        with self.assertRaisesRegex(Blocked, 'OPTED_OUT'): self.prepared()
        self.assertEqual(self.social.targets(), [])

    def test_reservations_count_toward_quota_before_dispatch(self):
        self.grant['limits']={'hour': 1}; self.save(); self.prepared()
        with self.assertRaisesRegex(Blocked, 'RATE_LIMIT'):
            self.social.prepare(self.policy, replace(self.binding, peer='34567890'), 'auto_reply', 'other-event', '另一个联系人', '真实上下文', [])

    def test_proactive_needs_evidence_and_has_independent_cooldown(self):
        morning=datetime(2026,10,27,10,0,tzinfo=CHINA).timestamp()
        binding=replace(self.binding,verified_at=morning)
        with self.assertRaisesRegex(Blocked, 'DRAFT_OR_MOTIVE_INVALID'):
            self.social.prepare(self.policy,binding,'proactive_dm','experience','你好','有真实经历',[],morning)
        oid=self.social.prepare(self.policy,binding,'proactive_dm','experience','你好','有真实经历',['real-memory'],morning)
        self.social.transition(oid,'prepared','typed');self.social.transition(oid,'typed','dispatch_attempted');self.social.transition(oid,'dispatch_attempted','sent_observed')
        later=morning+3600
        with self.assertRaisesRegex(Blocked,'PROACTIVE_COOLDOWN'):
            self.social.prepare(self.policy,replace(binding,verified_at=later),'proactive_dm','other-experience','不同内容','有新的真实经历',['other-memory'],later)

    def test_schema_four_migration_backs_up_and_preserves_evidence(self):
        self.store.db.execute('PRAGMA user_version=4'); self.store.db.commit()
        db=open_database(self.config.data)
        try: self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 5)
        finally: db.close()
        copies=list((self.config.data/'backups').glob('pre-social-*.sqlite3'))
        self.assertEqual(len(copies),1)
        backup=sqlite3.connect(copies[0])
        try: self.assertEqual(backup.execute('PRAGMA user_version').fetchone()[0],4)
        finally: backup.close()

    def test_public_experience_requires_explicit_classification_and_intact_real_sources(self):
        from PIL import Image
        import hashlib
        self.assertEqual(self.social.public_experiences(),[])
        png=self.root/'source.png';Image.new('RGB',(20,20),'red').save(png)
        sid='a'*32;summary=json.dumps({'summary':'Public flower sample','evidence_ids':['frame-one'],'uncertainty':'Sample only'})
        body=json.dumps({'samples':[{'observation_id':'frame-one','png_path':str(png),'png_sha256':hashlib.sha256(png.read_bytes()).hexdigest()}]})
        self.store.db.execute('INSERT INTO content_segments VALUES(?,?,?,?,?,?,?,?)',(sid,self.store.session,'watch_video','live_scripted',time.time(),body,summary,'summarized'));self.store.db.commit()
        manifest={'version':1,'serial':self.config.serial,'sources':[{'segment_id':sid,'summary_sha256':digest(summary),'public_url':'https://example.org/public-flower.mp4'}]}
        (self.config.data/'social-public-sources.private.json').write_text(json.dumps(manifest),encoding='utf-8')
        source=self.social.public_experiences()[0]
        self.assertTrue(source['public']);self.assertEqual(source['source_type'],'live_scripted')
        self.assertNotIn('png_path',json.dumps(source))
        Image.new('RGB',(20,20),'blue').save(png)
        with self.assertRaisesRegex(Blocked,'PUBLIC_SOURCE_UNVERIFIED'):self.social.public_experiences()

    def test_relationship_summary_is_target_local_and_does_not_revoke_optout(self):
        bid=self.social.bind(self.binding);self.social.opt_out(bid,'请不要再联系我')
        self.social.record_exchange(bid,'auto_reply','fixture-outbound')
        row=self.store.db.execute('SELECT opted_out,summary FROM social_relations WHERE binding_id=?',(bid,)).fetchone()
        self.assertEqual(row[0],1);self.assertIn('fixture-outbound',row[1])
        self.assertNotIn('fixture-outbound',json.dumps(self.social.targets()))
