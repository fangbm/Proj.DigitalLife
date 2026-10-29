"""Offline regression for current-page flows, with no device or message sends."""
from dataclasses import replace
import json
import hashlib
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from PIL import Image
from unittest.mock import AsyncMock, Mock, patch

from digitallife_v2.bilibili import BilibiliUI
from digitallife_v2.media import BILIBILI, VideoSources
from digitallife_v2.native_share import NativeShareUI
from digitallife_v2.safety import Blocked
from digitallife_v2.social import Binding, SocialStore
from digitallife_v2.social_ui import AndroidSocialUI, QQ, WX
from digitallife_v2.store import LifeStore
from test_m1 import config, observation
import test_media as media_fixture


def chat(app, title='Fixture group(12)', oid='current-chat'):
    obs=observation(oid,foreground=app+'/.ChatActivity')
    base=obs.controls[0]
    obs.controls=[replace(base,package=app,control_id='header',resource=app+':id/3o4' if app==QQ else '',
        text=title,bounds=(200,110,480,145))]
    if app==QQ:
        obs.controls.append(replace(base,package=app,control_id='editor',resource=app+':id/input',text=''))
    return obs


class CurrentChatTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.cfg=config(Path(folder.name));session=Path(folder.name)/'session';session.mkdir()
        self.store=LifeStore(self.cfg.data,str(session),self.cfg.serial,'fixture');self.addCleanup(self.store.close)
        self.social=SocialStore(self.store)
        grant={'version':1,'serial':self.cfg.serial,'accounts':{QQ:'12345678',WX:'fixture_account'}}
        (self.cfg.data/'social.private.json').write_text(json.dumps(grant),encoding='utf-8')
        self.loop=SimpleNamespace(config=self.cfg,store=self.store,run_kind='fixture')

    def ui(self,app):
        ui=AndroidSocialUI(self.loop,app)
        ui.act=AsyncMock(side_effect=AssertionError('Unexpected profile/settings navigation'))
        ui.root=AsyncMock(side_effect=AssertionError('Unexpected return to root'))
        return ui

    async def test_accounts_are_reused_without_opening_self_profiles(self):
        for app,account in ((QQ,'12345678'),(WX,'fixture_account')):
            with self.subTest(app=app):
                ui=self.ui(app);self.assertEqual(await ui.self_account(),account)
                ui.act.assert_not_awaited();ui.root.assert_not_awaited()

    async def test_bili_account_uses_configuration_without_profile_navigation(self):
        (self.cfg.data/'media.private.json').write_text(json.dumps({'version':1,'serial':self.cfg.serial,
            'accounts':{BILIBILI:'12345678'}}),encoding='utf-8')
        ui=BilibiliUI(self.loop);ui.home_page=AsyncMock(side_effect=AssertionError('Unexpected self profile navigation'))
        self.assertEqual(await ui.own_account(),{'id':'12345678','name':''});ui.home_page.assert_not_awaited()

    async def test_current_chat_is_used_without_reopening_recent_list(self):
        for app in (QQ,WX):
            with self.subTest(app=app):
                ui=self.ui(app);obs=chat(app);ui.observe=AsyncMock(return_value=obs)
                self.assertIs(await ui.open_hint('Fixture group(11)'),obs)
                ui.act.assert_not_awaited();ui.root.assert_not_awaited()

    async def test_bound_dm_and_group_refresh_stay_on_current_page(self):
        for app,account in ((QQ,'12345678'),(WX,'fixture_account')):
            for kind in ('dm','group'):
                with self.subTest(app=app,kind=kind):
                    title='Fixture contact' if kind=='dm' else 'Fixture group(12)'
                    old_title='Fixture contact' if kind=='dm' else 'Fixture group(11)'
                    ui=self.ui(app);obs=chat(app,title);ui.observe=AsyncMock(return_value=obs)
                    old=Binding(app,account,'saved_peer',kind,True,('old-page',),time.time()-100,'fixture',old_title)
                    fresh,after=await ui.refresh(old)
                    self.assertEqual(fresh.identity,old.identity);self.assertIs(after,obs)
                    self.assertEqual(fresh.observations,(obs.observation_id,));fresh.validate()
                    ui.act.assert_not_awaited()

    async def test_recheck_reads_draft_in_place_without_opening_contact_settings(self):
        ui=self.ui(QQ);obs=chat(QQ,'Fixture contact');ui.observe=AsyncMock(return_value=obs)
        obs.controls[-1]=replace(obs.controls[-1],text='Prepared message')
        binding=Binding(QQ,'12345678','saved_peer','dm',True,('old',),time.time(),'fixture','Fixture contact')
        _,after=await ui.recheck(binding,'Prepared message')
        self.assertIs(after,obs);ui.act.assert_not_awaited();ui.root.assert_not_awaited()
        obs.controls[0]=replace(obs.controls[0],text='Other contact')
        with self.assertRaisesRegex(Blocked,'CURRENT_TARGET_CHANGED'):await ui.recheck(binding,'Prepared message')

    async def test_first_visit_uses_local_route_and_later_reuses_saved_platform_binding(self):
        ui=self.ui(QQ);obs=chat(QQ)
        first,_=await ui.verify_peer(obs,'12345678','Fixture group(12)')
        self.assertTrue(first.peer.startswith('ui-chat:'));self.assertEqual(first.kind,'group');first.validate()
        legacy=replace(first,peer='87654321');self.social.bind(legacy)
        fresh,_=await ui.verify_peer(chat(QQ,'Fixture group(13)'),'12345678','Fixture group(13)')
        self.assertEqual(fresh.identity,legacy.identity);ui.act.assert_not_awaited()

    async def test_configured_account_change_still_stops_in_current_chat(self):
        ui=self.ui(QQ);binding=Binding(QQ,'87654321','saved_peer','dm',True,('old',),time.time(),'fixture','Fixture contact')
        with self.assertRaisesRegex(Blocked,'ACCOUNT_CHANGED'):
            await ui.verify_peer(chat(QQ,'Fixture contact'),'12345678','Fixture contact',binding)
        ui.act.assert_not_awaited()


class CurrentVideoTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_video_does_not_reopen_history(self):
        ui=BilibiliUI(SimpleNamespace());obs=observation('video',foreground=BILIBILI+'/.UnitedBizDetailsActivity')
        obs.controls=[];identity={'video_id':'BV1234567890','title':'Fixture video'}
        ui.observe=AsyncMock(return_value=obs);ui.dismiss_empty_editor=AsyncMock(return_value=obs)
        ui.ensure_identity=AsyncMock(return_value=(identity,obs));ui.home_page=AsyncMock(side_effect=AssertionError('Unexpected history navigation'))
        self.assertEqual(await ui.open_watched_video(identity),(identity,obs));ui.home_page.assert_not_awaited()

    async def test_inspected_thread_is_reused_without_repeating_inspection(self):
        loop=SimpleNamespace(action_ids=['inspection-input'],store=SimpleNamespace(state={'dispatched':7}),run_kind='scripted')
        ui=BilibiliUI(loop);obs=observation('thread',foreground=BILIBILI+'/.UnitedBizDetailsActivity')
        base=obs.controls[0]
        obs.controls=[replace(base,package=BILIBILI,control_id=role,resource=BILIBILI+':id/'+role,text=text,bounds=(100,y,400,y+40))
            for role,text,y in [('title','评论详情',100),('user_name','Fixture sender',400),('primary_message','Actual question',450),('reply_button','回复',500)]]
        source={'source_id':'video:fixture','video_id':'BV1234567890','title':'Fixture video'}
        thread={'thread_id':'thread:fixture','ui_thread_key':'123456','sender_name':'Fixture sender','text':'Actual question','root_text':'Root comment'}
        actor={'id':'12345678','name':'Fixture owner'}
        route={'activity':obs.foreground,'followed':True}
        loop.bili_reply_context={'source_id':source['source_id'],'thread_id':thread['thread_id'],'actor':actor,
            'route':route,'reply':{'sender_name':thread['sender_name'],'text':thread['text']},'dispatch_count':7}
        # SessionLoop.step clears this per-step list before the reply decision.
        loop.action_ids=[]
        ui.observe=AsyncMock(return_value=obs);ui.inspect_replies=AsyncMock(side_effect=AssertionError('Unexpected repeated thread navigation'))
        binding,context,after=await ui.resolve_reply(source,thread)
        self.assertIs(after,obs);self.assertEqual(binding.peer,'BV1234567890:123456')
        self.assertEqual(context['reply'],thread);ui.inspect_replies.assert_not_awaited()
        # Any intervening device action invalidates the shortcut.
        loop.store.state['dispatched']+=1
        ui.inspect_replies=AsyncMock(return_value=(thread,None,obs))
        await ui.resolve_reply(source,thread);ui.inspect_replies.assert_awaited_once_with(source,thread)

    async def test_watch_reads_visible_author_without_opening_author_profile(self):
        loop=SimpleNamespace(store=SimpleNamespace(event=Mock()))
        ui=BilibiliUI(loop);obs=observation('video');obs.controls=[]
        identity={'video_id':'BV1234567890','title':'Fixture video','author_name':'Fixture author','followed':True}
        ui.ensure_identity=AsyncMock(return_value=(identity,obs))
        ui.author_identity=AsyncMock(side_effect=AssertionError('Unexpected uploader profile navigation'))
        with patch('digitallife_v2.bilibili.ContentPerception') as perception,patch('digitallife_v2.bilibili.VideoSources') as sources:
            perception.return_value.run=AsyncMock(return_value={'segment_id':'fixture'})
            sources.return_value.bind.return_value='video:fixture'
            result=await ui.watch(5)
            self.assertEqual(result['source_id'],'video:fixture')
            self.assertIsNone(sources.return_value.bind.call_args.kwargs['uploader'])
            self.assertEqual(sources.return_value.bind.call_args.kwargs['uploader_name'],'Fixture author')
        ui.author_identity.assert_not_awaited()


class CardResultTests(unittest.IsolatedAsyncioTestCase):
    async def test_card_result_uses_completed_send_and_new_card_without_avatar_checks(self):
        for app in (QQ,WX):
            with self.subTest(app=app):
                before=chat(app);after=chat(app,oid='card-after')
                source={'title':'Fixture video','video_id':'BV1234567890'}
                after.controls.append(replace(after.controls[0],control_id='card-title',text=source['title'],bounds=(100,500,400,550)))
                native=NativeShareUI(None);native.before_chat=before
                native.avatar_region=AsyncMock(side_effect=AssertionError('Unexpected avatar check'))
                def evidence():return native.qq_card_evidence(before,after,source) if app==QQ else None
                self.assertIsNone(evidence() if app==QQ else await native.wechat_card_evidence(after,source))
                native.client_send_observed=True
                result=evidence() if app==QQ else await native.wechat_card_evidence(after,source)
                self.assertTrue(result['new_official_card_title']);self.assertFalse(result['owner_avatar_checked'])
                native.before_chat=after
                self.assertIsNone(native.qq_card_evidence(after,after,source) if app==QQ else await native.wechat_card_evidence(after,source))
                native.avatar_region.assert_not_awaited()


class OfficialPickerTests(unittest.IsolatedAsyncioTestCase):
    setUp=media_fixture.MediaTests.setUp
    save=media_fixture.MediaTests.save

    async def test_both_official_pickers_use_visible_recipient_rows_without_avatar_or_roster(self):
        for app in (QQ,WX):
            with self.subTest(app=app):
                account='12345678';binding=replace(self.group,app=app,account=account,route_hint='Fixture group(12)')
                self.grant['accounts'][app]=account;self.grant['shares']['allowed_groups'].append(app+'/'+binding.peer);self.save()
                before=chat(app);picker=observation('picker',foreground=app+('/.QPublicTransFragmentActivity' if app==QQ else '/.SelectConversationUI'))
                path=self.root/'picker-fixture.png';Image.new('RGB',(picker.width,picker.height),'white').save(path)
                picker.png_path=str(path);picker.png_sha256=hashlib.sha256(path.read_bytes()).hexdigest()
                base=picker.controls[0]
                picker.controls=[replace(base,package=app,control_id='recent',text='最近聊天',bounds=(200,300,500,340)),
                    replace(base,package=app,control_id='recipient',text='Fixture group',description='Fixture group，12人，当前聊天',bounds=(200,500,500,550))]
                modal=observation('modal',foreground=app+'/.OfficialModal')
                ui=Mock(spec=AndroidSocialUI);ui.observe=AsyncMock(return_value=before)
                ui.act=AsyncMock(side_effect=[before,modal]);ui.resolve=AsyncMock(return_value=(binding,{'messages':[]},before))
                ui.route_name=AndroidSocialUI.route_name
                ui.visual_tap=AsyncMock(side_effect=AssertionError('Unexpected profile or group information'))
                ui.tap=AsyncMock(side_effect=AssertionError('Unexpected group settings navigation'))
                loop=SimpleNamespace(config=self.cfg);native=NativeShareUI(loop)
                native.avatar_region=AsyncMock(side_effect=AssertionError('Unexpected avatar localization'))
                native.avatar_matches=Mock(side_effect=AssertionError('Unexpected avatar comparison'))
                bili=SimpleNamespace(select=AsyncMock(return_value=picker))
                native.open_source_video=AsyncMock(return_value=(bili,picker));native.open_wechat_picker=AsyncMock(return_value=picker)
                native.cancel_wechat_picker=AsyncMock(return_value=before);native.restore_wechat=AsyncMock(return_value=before)
                with patch('digitallife_v2.native_share.AndroidSocialUI',return_value=ui):
                    result,after=await (native.prepare_qq({},'fixture-binding') if app==QQ else native.prepare_wechat({},'fixture-binding'))
                self.assertEqual(result.identity,binding.identity);self.assertIs(after,modal)
                self.assertEqual(ui.act.await_count,2);ui.resolve.assert_awaited_once()
                tapped=ui.act.await_args.args[0]['control_id']
                self.assertEqual(next(c.bounds for c in picker.controls if c.control_id==tapped),(200,500,500,550))
                ui.visual_tap.assert_not_awaited();ui.tap.assert_not_awaited()
                native.avatar_region.assert_not_awaited();native.avatar_matches.assert_not_called()
