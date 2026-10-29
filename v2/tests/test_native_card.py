import time,unittest
from digitallife_v2.native_card import NativeCardPolicy,NativeCardStore,CARD_SOURCE
from digitallife_v2.safety import Blocked
import test_media as fixture


class OfficialCardOutboxTests(unittest.TestCase):
    save=fixture.MediaTests.save
    def setUp(self):
        fixture.MediaTests.setUp(self);self.cards=NativeCardStore(self.store);self.bid=self.cards.bind(self.group)
    def old_link(self,status='sent_observed'):
        oid=self.social.prepare_media(self.policy,self.group,'group_video_share',self.source,'A source caption '+self.url,'Offline fixture')
        self.social.transition(oid,'prepared','typed');self.social.transition(oid,'typed','dispatch_attempted');self.social.transition(oid,'dispatch_attempted',status,{'offline_fixture':True})
        self.store.db.execute('UPDATE social_outbox SET created=created-60 WHERE id=?',(oid,));self.store.db.commit()
    def test_native_payload_has_a_distinct_pre_send_stage_without_typing_text(self):
        oid=self.cards.prepare_native(self.policy,self.group,self.source,'Public source fixture')
        with self.assertRaisesRegex(Blocked,'NOT_PREPARED'):self.cards.before_send(self.policy,self.group,oid)
        self.cards.transition(oid,'prepared','card_ready',{'text_input_attempted':False})
        self.assertEqual(self.cards.before_send(self.policy,self.group,oid),'group_video_share')
        self.cards.transition(oid,'card_ready','dispatch_attempted');self.cards.transition(oid,'dispatch_attempted','delivery_unknown')
        with self.assertRaises(Blocked):self.cards.before_send(self.policy,self.group,oid)
        with self.assertRaises(Blocked):self.cards.prepare_native(self.policy,self.group,self.source,'Another motive')
    def test_explicit_native_retest_is_once_for_exact_target_after_observed_link(self):
        self.old_link();policy=NativeCardPolicy(self.cfg,retest_binding=self.bid,deadline=time.time()+120)
        oid=self.cards.prepare_native(policy,self.group,self.source,'Human requested official card acceptance',requested_retest=True)
        self.assertTrue(oid)
        with self.assertRaises(Blocked):self.cards.prepare_native(policy,self.group,self.source,'Repeat',requested_retest=True)
        self.assertEqual(self.policy.check(self.group,'group_video_share')['video_shares_group_day'],1)
    def test_unknown_old_link_wrong_target_or_expired_scope_cannot_authorize_retest(self):
        self.old_link('delivery_unknown')
        for policy in [NativeCardPolicy(self.cfg,retest_binding=self.bid,deadline=time.time()+120),NativeCardPolicy(self.cfg,retest_binding='wrong',deadline=time.time()+120),NativeCardPolicy(self.cfg,retest_binding=self.bid,deadline=time.time()-1)]:
            with self.assertRaises(Blocked):self.cards.prepare_native(policy,self.group,self.source,'Fixture',requested_retest=True)
    def test_permission_revocation_still_prevents_an_official_card_send(self):
        oid=self.cards.prepare_native(self.policy,self.group,self.source,'Fixture motive');self.cards.transition(oid,'prepared','card_ready')
        self.grant['shares']['group_video_share']=False;self.save()
        with self.assertRaises(Blocked):self.cards.before_send(self.policy,self.group,oid)
        self.assertIsNone(self.store.db.execute('SELECT attempted FROM social_outbox WHERE id=?',(oid,)).fetchone()[0])

    def test_text_payload_cannot_skip_input_as_a_native_card(self):
        oid=self.social.prepare_media(self.policy,self.group,'group_video_share',self.source,'Fixture link '+self.url,'Fixture')
        with self.assertRaisesRegex(Blocked,'NATIVE_CARD_PAYLOAD'):self.social.transition(oid,'prepared','card_ready')
        self.assertEqual(self.store.db.execute('SELECT status FROM social_outbox WHERE id=?',(oid,)).fetchone()[0],'prepared')

    def test_read_only_card_review_preserves_unknown_history_without_resending(self):
        oid=self.cards.prepare_native(self.policy,self.group,self.source,'Fixture card')
        self.cards.transition(oid,'prepared','card_ready');self.cards.transition(oid,'card_ready','dispatch_attempted')
        self.cards.transition(oid,'dispatch_attempted','delivery_unknown',{'original_reason':'fixture post-read failed'})
        proof={'expected_video_id':self.video,'new_own_official_card_title':True,'png_sha256':'offline-fixture','original_before_observation_id':'fixture-before'}
        result=self.cards.reconcile_card(oid,self.group,self.source,proof)
        self.assertTrue(result['review_only'])
        status,attempted,evidence=self.store.db.execute('SELECT status,attempted,evidence FROM social_outbox WHERE id=?',(oid,)).fetchone()
        import json
        self.assertEqual(status,'sent_observed');self.assertIsNotNone(attempted)
        self.assertEqual(json.loads(evidence)['original_unknown_evidence']['original_reason'],'fixture post-read failed')
        self.assertTrue(self.store.db.execute("SELECT 1 FROM events WHERE kind='social_delivery_unknown'").fetchone())
        with self.assertRaises(Blocked):self.cards.reconcile_card(oid,self.group,self.source,proof)

    def test_card_review_requires_real_attempt_source_and_new_outgoing_card(self):
        oid=self.cards.prepare_native(self.policy,self.group,self.source,'Fixture card')
        with self.assertRaises(Blocked):self.cards.reconcile_card(oid,self.group,self.source,{})
        self.cards.transition(oid,'prepared','card_ready');self.cards.transition(oid,'card_ready','dispatch_attempted');self.cards.transition(oid,'dispatch_attempted','delivery_unknown')
        with self.assertRaises(Blocked):self.cards.reconcile_card(oid,self.group,self.source,{'expected_video_id':'different-video','png_sha256':'fixture'})

    def new_source(self):
        from digitallife_v2.media import VideoSources
        import json
        url='https://www.bilibili.com/video/BV0987654321'
        segment='2'*32
        self.store.db.execute('INSERT INTO content_segments SELECT ?,session,kind,source_type,started,?,summary,status FROM content_segments WHERE id=?',(segment,json.dumps(self.body),self.segment));self.store.db.commit()
        return VideoSources(self.store).bind(segment,public_url=url,title='Video title',uploader='98765432',followed=True,captures=[{**c,'url':url} for c in self.captures])

    def test_new_video_acceptance_has_one_fixed_slot_without_changing_normal_limits(self):
        self.old_link();source=self.new_source()
        with self.assertRaisesRegex(Blocked,'SHARE_LIMIT'):self.cards.prepare_native(self.policy,self.group,source,'Normal fixture')
        policy=NativeCardPolicy(self.cfg,retest_binding=self.bid,deadline=time.time()+120,acceptance_source=source,acceptance_limit=2)
        oid=self.cards.prepare_native(policy,self.group,source,'Explicit bounded fixture');self.cards.transition(oid,'prepared','card_ready')
        self.assertEqual(self.cards.before_send(policy,self.group,oid),'group_video_share')
        self.assertEqual(self.policy.check(self.group,'group_video_share')['video_shares_group_day'],1)
        with self.assertRaises(Blocked):self.cards.prepare_native(policy,self.group,source,'Duplicate fixture')

    def test_acceptance_cannot_change_source_target_deadline_or_resend_an_old_video(self):
        source=self.new_source();policy=NativeCardPolicy(self.cfg,retest_binding=self.bid,deadline=time.time()+120,acceptance_source=source,acceptance_limit=2)
        with self.assertRaisesRegex(Blocked,'SCOPE_REQUIRED'):self.cards.prepare_native(policy,self.group,self.source,'Wrong source')
        policy.retest_deadline=time.time()-1
        with self.assertRaisesRegex(Blocked,'SCOPE_REQUIRED'):self.cards.prepare_native(policy,self.group,source,'Expired')
        policy.retest_deadline=time.time()+120;policy.retest_binding='wrong'
        with self.assertRaisesRegex(Blocked,'SCOPE_REQUIRED'):self.cards.prepare_native(policy,self.group,source,'Wrong group')

    def test_one_live_window_cannot_move_acceptance_allowance_to_a_second_video(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock,patch
        from digitallife_v2.native_card import NativeCardAgent
        self.store.state['deadline']=time.time()+600
        loop=SimpleNamespace(config=self.cfg,store=self.store,run_kind='scripted',media_acceptance_context='Human requested one bounded fixture',check_stop=lambda:None)
        other=self.new_source()
        with patch('digitallife_v2.native_share.NativeShareUI.prepare_qq',new=AsyncMock(side_effect=Blocked('FIXTURE_NAVIGATION_STOP'))) as navigate:
            with self.assertRaisesRegex(Blocked,'NAVIGATION_STOP'):asyncio.run(NativeCardAgent(loop).run(self.source,self.bid))
            with self.assertRaisesRegex(Blocked,'SCOPE_REQUIRED'):asyncio.run(NativeCardAgent(loop).run(other,self.bid))
            navigate.assert_awaited_once()
