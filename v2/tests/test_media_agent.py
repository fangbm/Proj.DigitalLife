from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
import time,unittest
from digitallife_v2.media_agent import MediaAgent
from digitallife_v2.social import digest
from digitallife_v2.safety import Blocked,UnknownDispatch
from test_m1 import observation
import test_media as media_fixture


class MediaCommentAgentTests(unittest.IsolatedAsyncioTestCase):
    save=media_fixture.MediaTests.save
    def setUp(self):
        media_fixture.MediaTests.setUp(self)
        self.before=observation('before',foreground='tv.danmaku.bili/.Video')
        self.focused=observation('focused',foreground=self.before.foreground)
        self.typed=observation('typed',foreground=self.before.foreground)
        self.after=observation('after',foreground=self.before.foreground)
        self.text='我是数字生命，采样片段中保留人工确认的设计很清楚。'
        self.ui=SimpleNamespace(resolve_comment=AsyncMock(return_value=(self.binding,{'visible_comments':['Public discussion'],'actor_name':'Fixture AI'},self.before)),
            read_comments=AsyncMock(side_effect=lambda obs,**kw:obs),visible_comments=lambda obs:['Public discussion'],
            focus_comment=AsyncMock(return_value=self.focused),verify_comment_route=lambda *args:None,
            observe=AsyncMock(return_value=self.focused),
            check_dynamic_off=AsyncMock(),publish_control=AsyncMock(return_value=self.typed.controls[0]),
            comment_evidence=lambda *args:{'observation_id':'after','new_comment_exact_text':True})
        async def insert(*args,**kwargs):return self.typed
        async def inspect(obs):obs.input_editor={'text_sha256':digest(self.text)}
        @asynccontextmanager
        async def temporary():yield self.controller
        self.controller=SimpleNamespace(temporary=temporary,insert=AsyncMock(side_effect=insert),observe_editor=AsyncMock(side_effect=inspect))
        async def guarded(awaitable):return await awaitable
        async def structured(system,data,images,validate):
            self.assertEqual(set(data),{'video','visible_public_comments'})
            draft={'decision':'send','text':self.text,'motive':'A specific observed design choice','sources':[self.source]};validate(draft);return draft
        self.loop=SimpleNamespace(config=self.cfg,store=self.store,check_stop=lambda:None,deadline=time.monotonic()+600,
            guarded=guarded,brain=SimpleNamespace(structured=AsyncMock(side_effect=structured)),memory=None,
            phone=SimpleNamespace(prepare=AsyncMock(return_value=('shell','input','tap','20','30'))),send=AsyncMock(return_value=(self.after,'changed')))

    async def run_comment(self):
        with patch('digitallife_v2.bilibili.BilibiliUI',return_value=self.ui),patch('digitallife_v2.media_agent.InputBridge',return_value=self.controller):
            return await MediaAgent(self.loop).comment(self.source)

    def row(self):return self.store.db.execute('SELECT status,attempted FROM social_outbox').fetchone()

    async def test_actual_send_path_is_one_attempt_and_needs_independent_comment_evidence(self):
        self.assertEqual((await self.run_comment())['outcome'],'sent_observed');self.loop.send.assert_awaited_once()
        self.ui.check_dynamic_off.assert_awaited_once();self.controller.observe_editor.assert_awaited_once()
        self.assertEqual(self.row()[0],'sent_observed')

    async def test_dynamic_repost_not_verified_off_prevents_publication(self):
        self.ui.check_dynamic_off.side_effect=Blocked('MEDIA_DYNAMIC_REPOST_NOT_VERIFIED_OFF')
        with self.assertRaises(Blocked):await self.run_comment()
        self.loop.send.assert_not_awaited();self.assertEqual(self.row(),('cancelled_before_send',None))

    async def test_revoke_after_input_prevents_publish(self):
        async def revoke(*args,**kwargs):self.grant['comments']['post_video_comment']=False;self.save();return self.typed
        self.controller.insert.side_effect=revoke
        with self.assertRaises(Blocked):await self.run_comment()
        self.loop.send.assert_not_awaited();self.assertEqual(self.row(),('cancelled_before_send',None))

    async def test_uncertain_publish_is_persisted_unknown_with_no_second_click(self):
        self.loop.send.side_effect=UnknownDispatch('DISPATCH_OR_POST_OBSERVATION_UNCERTAIN')
        with self.assertRaises(UnknownDispatch):await self.run_comment()
        self.loop.send.assert_awaited_once();self.assertEqual(self.row()[0],'delivery_unknown');self.assertIsNotNone(self.row()[1])

    async def test_recorded_video_comment_is_rejected_before_navigation_or_model(self):
        self.social.prepare_media(self.policy,self.binding,'post_video_comment',self.source,self.text,'Fixture evidence')
        with self.assertRaisesRegex(Blocked,'NO_REPOST'):await self.run_comment()
        self.ui.resolve_comment.assert_not_awaited();self.loop.brain.structured.assert_not_awaited();self.loop.send.assert_not_awaited()

    async def test_same_video_group_share_is_rejected_before_UI_and_model(self):
        bid=self.social.bind(self.group)
        self.social.prepare_media(self.policy,self.group,'group_video_share',self.source,'A fixture caption','Fixture evidence')
        with patch('digitallife_v2.media_agent.SocialAgent') as social_agent:
            with self.assertRaisesRegex(Blocked,'NO_RESHARE'):
                await MediaAgent(self.loop).share_link(self.source,bid,fallback_reason='offline fixture')
            social_agent.assert_not_called()

    async def test_share_resume_never_accepts_an_unknown_send(self):
        bid=self.social.bind(self.group)
        oid=self.social.prepare_media(self.policy,self.group,'group_video_share',self.source,'Fixture caption','Fixture evidence')
        self.social.transition(oid,'prepared','typed');self.social.transition(oid,'typed','dispatch_attempted');self.social.transition(oid,'dispatch_attempted','delivery_unknown')
        with patch('digitallife_v2.media_agent.SocialAgent') as social_agent:
            with self.assertRaisesRegex(Blocked,'NOT_KNOWN_UNSENT'):
                await MediaAgent(self.loop).share_link(self.source,bid,fallback_reason='offline fixture',resume_outbound=oid)
            social_agent.assert_not_called()

    async def test_official_share_failure_never_silently_sends_a_link(self):
        with patch('digitallife_v2.native_card.NativeCardAgent.run',new=AsyncMock(side_effect=Blocked('SDK_UNAVAILABLE'))) as native,patch.object(MediaAgent,'share_link',new=AsyncMock()) as link:
            with self.assertRaisesRegex(Blocked,'SDK_UNAVAILABLE'):await MediaAgent(self.loop).share(self.source,'fixture-binding')
            native.assert_awaited_once();link.assert_not_awaited();self.loop.send.assert_not_awaited()

    async def test_reply_uses_observed_thread_sources_and_publishes_once(self):
        from dataclasses import replace
        tid='thread:'+'b'*32
        thread={'source_id':self.source,'video_id':self.binding.peer,'account':self.binding.account,'ui_thread_key':'123456789',
            'thread_id':tid,'sender_name':'Other','text':'What is the benefit?','root_text':'Original source comment'}
        binding=replace(self.binding,peer=self.binding.peer+':123456789',kind='comment_thread')
        self.ui.resolve_reply=AsyncMock(return_value=(binding,{'actor_name':'Fixture AI'},self.before))
        self.ui.focus_reply=AsyncMock(return_value=self.focused);self.ui.read_reply_after=AsyncMock(return_value=self.after)
        self.ui.reply_evidence=lambda *args:{'new_own_reply_exact_text':True}
        async def draft(system,data,images,validate):
            self.assertEqual(data['reply_context']['thread_id'],tid)
            value={'decision':'send','text':self.text,'motive':'Answer actual question','sources':[self.source,tid]};validate(value);return value
        self.loop.brain.structured.side_effect=draft
        with patch('digitallife_v2.bili_threads.ThreadSources.get',return_value=thread),patch('digitallife_v2.bilibili.BilibiliUI',return_value=self.ui),patch('digitallife_v2.media_agent.InputBridge',return_value=self.controller):
            self.assertEqual((await MediaAgent(self.loop).reply(tid))['outcome'],'sent_observed')
        self.loop.send.assert_awaited_once();self.ui.focus_reply.assert_awaited_once();self.ui.focus_comment.assert_not_awaited()
        kind,sources=self.store.db.execute('SELECT kind,sources FROM social_outbox').fetchone()
        self.assertEqual(kind,'reply_video_comment');self.assertIn(tid,sources)

    async def test_notification_requires_full_actual_question_before_reply(self):
        oid=self.social.prepare_media(self.policy,self.binding,'post_video_comment',self.source,'Original fixture root','Offline fixture')
        self.social.transition(oid,'prepared','typed');self.social.transition(oid,'typed','dispatch_attempted');self.social.transition(oid,'dispatch_attempted','sent_observed',{'offline_fixture':True})
        thread={'thread_id':'thread:'+'c'*32,'text':'What is the benefit of preserving a confirmation step?'}
        self.ui.inspect_replies=AsyncMock(return_value=(thread,None,self.before))
        with patch('digitallife_v2.bilibili.BilibiliUI',return_value=self.ui),patch('digitallife_v2.media_agent.VideoSources.offered',return_value=[{'source_id':'newer-unrelated-video'}]) as latest,patch.object(MediaAgent,'reply',new=AsyncMock(return_value={'outcome':'defer'})) as reply:
            with self.assertRaisesRegex(Blocked,'NOT_MATCHED'):await MediaAgent(self.loop).reply_from_event({'event_id':'fixture','preview':'Unrelated notification'})
            reply.assert_not_awaited()
            await MediaAgent(self.loop).reply_from_event({'event_id':'fixture','preview':'Other replied: '+thread['text']})
            reply.assert_awaited_once_with(thread['thread_id'])
            latest.assert_not_called()
            self.assertEqual(self.ui.inspect_replies.call_args.args[0]['source_id'],self.source)
