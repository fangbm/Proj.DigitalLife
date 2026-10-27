from contextlib import asynccontextmanager
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from digitallife_v2.social import Binding, digest
from digitallife_v2.social_agent import SocialAgent
from digitallife_v2.store import LifeStore
from digitallife_v2.safety import Blocked, UnknownDispatch
from test_m1 import config, observation


class SocialAgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name);self.cfg=config(root);self.cfg.full_access=True
        directory=root/'session';directory.mkdir()
        self.store=LifeStore(self.cfg.data,str(directory),self.cfg.serial,'fixture');self.addCleanup(self.store.close)
        self.grant={'version':1,'serial':self.cfg.serial,'enabled':True,'accounts':{'com.tencent.mobileqq':'12345678'},
            'contacts':{'mode':'all_existing_contacts','auto_reply':True,'proactive_dm':True},'quiet_hours':['00:00','00:00']}
        self.save()
        self.binding=Binding('com.tencent.mobileqq','12345678','23456789','dm',True,('verified-profile',),time.time(),'fixture','测试联系人')
        self.incoming='你好，测试消息'
        def page(oid,text):
            obs=observation(oid,foreground='com.tencent.mobileqq/.Chat')
            editor=replace(obs.controls[0],package='com.tencent.mobileqq',resource='com.tencent.mobileqq:id/input',
                kind='android.widget.EditText',focused=True,text=text)
            send=replace(editor,control_id=oid+':1',resource='com.tencent.mobileqq:id/send_btn',kind='android.widget.Button',text='发送',focused=False,bounds=(610,200,700,300))
            return replace(obs,controls=[editor,send])
        self.before=page('before','');self.ready=page('ready','中文回复');self.fresh=page('fresh','中文回复');self.after=page('after','')
        context={'messages':[{'direction':'incoming','text':self.incoming}],
            'incoming_ref':digest(self.binding.account+'|'+self.binding.peer+'|'+self.incoming),'event_matched':True}
        self.ui=SimpleNamespace(resolve=AsyncMock(return_value=(self.binding,context,self.before)),
            refresh=AsyncMock(return_value=(self.binding,self.before)),focus_editor=AsyncMock(return_value=self.before),
            recheck=AsyncMock(return_value=(self.binding,self.ready)),send_control=lambda obs:obs.controls[1],
            messages=lambda obs:[{'direction':'incoming','text':self.incoming}],verify_composer=lambda *args:None,
            outgoing_evidence=lambda *args:{'observation_id':'after','new_own_bubble':True,'editor_cleared':True})
        async def structured(system,data,images,validator):
            result={'decision':'send','text':'中文回复','motive':'结合当前真实会话回应','sources':[data['offered_sources'][0]]}
            validator(result);return result
        async def guarded(awaitable):return await awaitable
        self.model=AsyncMock(side_effect=structured)
        self.loop=SimpleNamespace(config=self.cfg,store=self.store,directory=directory,check_stop=lambda:None,
            guarded=guarded,brain=SimpleNamespace(structured=self.model),memory=None,
            phone=SimpleNamespace(observe=AsyncMock(return_value=self.fresh),prepare=AsyncMock(return_value=('shell','input','tap','655','250'))),
            observed=lambda obs:None,send=AsyncMock(return_value=(self.after,'changed')))
        @asynccontextmanager
        async def temporary():yield
        self.input=SimpleNamespace(temporary=temporary,insert=AsyncMock(return_value=self.ready))
        self.patcher=patch('digitallife_v2.social_agent.InputBridge',return_value=self.input);self.patcher.start();self.addCleanup(self.patcher.stop)

    def save(self):
        (self.cfg.data/'social.private.json').write_text(json.dumps(self.grant),encoding='utf-8')

    def status(self):return self.store.db.execute('SELECT status FROM social_outbox').fetchone()[0]

    async def test_full_orchestration_sends_once_and_requires_post_observation(self):
        result=await SocialAgent(self.loop,self.ui).run('auto_reply')
        self.assertEqual(result['outcome'],'sent_observed');self.assertEqual(self.status(),'sent_observed')
        self.loop.send.assert_awaited_once();self.model.assert_awaited_once()
        self.assertEqual(self.loop.send.call_args.args[1]['type'],'social_send')
        self.store.db.execute('UPDATE social_outbox SET created=created-31');self.store.db.commit()
        repeated=await SocialAgent(self.loop,self.ui).run('auto_reply',event={'event_id':'new-notification-id-for-same-message'})
        self.assertEqual(repeated['outcome'],'incoming_already_recorded')
        self.model.assert_awaited_once()
        self.loop.send.assert_awaited_once()

    async def test_already_answered_visible_conversation_does_not_generate_another_reply(self):
        binding,context,obs=self.ui.resolve.return_value
        self.ui.resolve.return_value=(binding,{**context,'messages':context['messages']+[{'direction':'outgoing','text':'已经回复'}]},obs)
        result=await SocialAgent(self.loop,self.ui).run('auto_reply')
        self.assertEqual(result['outcome'],'no_unanswered_incoming')
        self.model.assert_not_awaited();self.input.insert.assert_not_awaited();self.loop.send.assert_not_awaited()

    async def test_revocation_after_typing_prevents_send(self):
        async def recheck(*args,**kwargs):
            self.grant['enabled']=False;self.save();return self.binding,self.ready
        self.ui.recheck=AsyncMock(side_effect=recheck)
        with self.assertRaisesRegex(Blocked,'SOCIAL_DISABLED'):
            await SocialAgent(self.loop,self.ui).run('auto_reply')
        self.loop.send.assert_not_awaited();self.assertEqual(self.status(),'cancelled_before_send')

    async def test_last_moment_optout_cancels_without_send(self):
        self.ui.messages=lambda obs:[{'direction':'incoming','text':'请不要再联系我' if obs.observation_id=='fresh' else self.incoming}]
        with self.assertRaisesRegex(Blocked,'OPTED_OUT'):
            await SocialAgent(self.loop,self.ui).run('auto_reply')
        self.loop.send.assert_not_awaited();self.assertEqual(self.status(),'cancelled_before_send')

    async def test_unknown_dispatch_or_missing_own_bubble_is_never_retried(self):
        self.loop.send.side_effect=UnknownDispatch('fixture_unknown')
        with self.assertRaises(UnknownDispatch):await SocialAgent(self.loop,self.ui).run('auto_reply')
        self.assertEqual(self.status(),'delivery_unknown')
        self.loop.send.side_effect=None
        with self.assertRaisesRegex(Blocked,'UNKNOWN_SEND'):await SocialAgent(self.loop,self.ui).run('auto_reply')
        self.loop.send.assert_awaited_once()

    async def test_input_success_without_new_outgoing_bubble_is_unknown(self):
        self.ui.outgoing_evidence=lambda *args:None
        result=await SocialAgent(self.loop,self.ui).run('auto_reply')
        self.assertEqual(result['outcome'],'delivery_unknown');self.assertEqual(self.status(),'delivery_unknown')

    async def test_event_must_match_current_thread_before_model_or_typing(self):
        binding,context,obs=self.ui.resolve.return_value
        self.ui.resolve.return_value=(binding,{**context,'event_matched':False},obs)
        with self.assertRaisesRegex(Blocked,'EVENT_NOT_MATCHED'):
            await SocialAgent(self.loop,self.ui).run('auto_reply',event={'event_id':'offered-notification'})
        self.model.assert_not_awaited();self.input.insert.assert_not_awaited();self.loop.send.assert_not_awaited()

    async def test_scripted_binding_fallback_requires_actual_human_marker(self):
        self.loop.run_kind='scripted';self.loop.social_acceptance_marker='M4-test-marker'
        with self.assertRaisesRegex(Blocked,'MARKER_NOT_IN_ACTUAL_THREAD'):
            await SocialAgent(self.loop,self.ui).run('auto_reply')
        self.model.assert_not_awaited();self.input.insert.assert_not_awaited()

    async def test_scripted_notification_link_requires_actual_incoming_minute(self):
        self.loop.run_kind='scripted';self.loop.social_acceptance_marker=self.incoming
        self.loop.social_acceptance_post_minute='22:02'
        with self.assertRaisesRegex(Blocked,'MINUTE_NOT_MATCHED'):
            await SocialAgent(self.loop,self.ui).run('auto_reply')
        self.model.assert_not_awaited();self.loop.send.assert_not_awaited()
