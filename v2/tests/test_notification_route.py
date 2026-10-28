import asyncio,json,time,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from digitallife_v2.bridge import grant_sensitive_notifications,PACKAGE
from digitallife_v2.session import SessionLoop
from digitallife_v2.safety import KnownNonDispatch,UnknownDispatch,Blocked
from digitallife_v2.store import LifeStore
from digitallife_v2.events import EventHub
from test_m1 import config,observation

class RouteFixTests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.cfg=config(self.root)
  self.session=self.root/'session';self.session.mkdir();self.store=LifeStore(self.cfg.data,str(self.session),self.cfg.serial,'fixture');self.addCleanup(self.store.close)
 async def test_classifier_explains_identity_inspection_without_promising_unchecked_send(self):
  from digitallife_v2.brain import LifeBrain
  brain=LifeBrain(self.cfg,self.store);self.store.inbound_context={'event_id':'actual-id','account_ref':None,'conversation_ref':None}
  for enabled in (False,True):
   self.store.social_context={'enabled':enabled}
   brain.structured=AsyncMock(return_value={'event_id':'actual-id','decision':'reply_later','note':'Later'})
   result=await brain.decide(observation(),{},[])
   args=brain.structured.call_args.args
   self.assertEqual(args[1]['routing_capabilities'],{'inspect_original_notification_thread':enabled,'send_without_identity_check':False})
   self.assertEqual(result['event_response']['decision'],'reply_later')
   self.assertEqual('M4已启用' in args[0],enabled)
   prompt=brain.payload(observation(),{},[])['messages'][0]['content']
   self.assertEqual('M3 仅接收和分类' in prompt,not enabled)
 async def test_explicit_sensitive_grant_verifies_readback_and_keeps_original_backup(self):
  phone=SimpleNamespace(identity=AsyncMock(),_adb=AsyncMock(),_text=AsyncMock(side_effect=['RECEIVE_SENSITIVE_NOTIFICATIONS: ignore','RECEIVE_SENSITIVE_NOTIFICATIONS: allow','RECEIVE_SENSITIVE_NOTIFICATIONS: allow','RECEIVE_SENSITIVE_NOTIFICATIONS: allow']))
  await grant_sensitive_notifications(self.cfg,phone);await grant_sensitive_notifications(self.cfg,phone)
  backup=json.loads((self.cfg.data/'notification-permissions.private.json').read_text(encoding='utf-8'))
  self.assertIn('ignore',backup['sensitive_original']);self.assertEqual(phone._adb.call_args.args,('shell','cmd','appops','set',PACKAGE,'RECEIVE_SENSITIVE_NOTIFICATIONS','allow'))
 async def test_failed_permission_readback_never_reports_success(self):
  phone=SimpleNamespace(identity=AsyncMock(),_adb=AsyncMock(),_text=AsyncMock(return_value='RECEIVE_SENSITIVE_NOTIFICATIONS: ignore'))
  with self.assertRaisesRegex(Blocked,'NOT_GRANTED'):await grant_sensitive_notifications(self.cfg,phone)
 def loop(self):
  loop=SessionLoop(self.cfg,self.session);loop.run_kind='fixture';loop.store=self.store;loop.lock=SimpleNamespace(pending=self.root/'pending.json',emergency=self.root/'emergency');loop.deadline=time.monotonic()+60;loop.phone=SimpleNamespace(observe=AsyncMock(return_value=observation('after')))
  return loop
 async def test_confirmed_pre_dispatch_refusal_clears_only_its_pending_without_counting_input(self):
  loop=self.loop();operation=AsyncMock(side_effect=KnownNonDispatch('ROUTE_CANCELLED'))
  with self.assertRaises(KnownNonDispatch):await loop.send_operation(operation,{'type':'social_open_event'},observation())
  self.assertFalse(loop.lock.pending.exists());self.assertEqual(self.store.state['dispatched'],0);loop.phone.observe.assert_not_awaited()
 async def test_post_observation_failure_cannot_claim_pre_dispatch_refusal(self):
  loop=self.loop();loop.phone.observe=AsyncMock(side_effect=KnownNonDispatch('BAD_POST_READ'))
  with self.assertRaises(UnknownDispatch):await loop.send_operation(AsyncMock(),{'type':'social_open_event'},observation())
  self.assertTrue(loop.lock.pending.exists());self.assertEqual(self.store.state['dispatched'],1)
 def test_known_route_defer_requeues_real_event_with_backoff(self):
  hub=EventHub(self.store,['com.tencent.mobileqq']);eid=hub.ingest({'package':'com.tencent.mobileqq','event_key':'actual-key-fixture','posted_at':time.time(),'text':'Hello'})
  offered=hub.next();hub.resolve(offered,{'event_id':eid,'decision':'reply_now','note':'Read thread first'})
  self.assertIsNone(hub.next());hub.defer_handling(offered,'ROUTE_UNAVAILABLE');self.assertIsNone(hub.next())
  row=self.store.db.execute('SELECT status,next_at FROM inbound_events WHERE id=?',(eid,)).fetchone();self.assertEqual(row[0],'pending');self.assertGreater(row[1],time.time()+100)

 async def test_router_foregrounds_its_own_bridge_before_using_the_real_notification_key(self):
  from digitallife_v2.social_ui import AndroidSocialUI
  from digitallife_v2.social import Binding
  hub=EventHub(self.store,['com.tencent.mobileqq']);eid=hub.ingest({'package':'com.tencent.mobileqq','event_key':'source-key-from-NLS-fixture','posted_at':time.time(),'title':'Contact','text':'Hello'})
  offered=hub.next();order=[]
  chat=observation('chat',foreground='com.tencent.mobileqq/.Chat')
  binding=Binding('com.tencent.mobileqq','12345678','23456789','dm',True,('source-scene',),time.time(),'fixture','Contact')
  async def guarded(awaitable):return await awaitable
  async def request(method,path,body):
   if path=='/route_status':return {'available':True,'requires_foreground':True,'bridge_foreground':False}
   self.assertEqual(body,{'event_key':'source-key-from-NLS-fixture'});self.assertEqual(order,['bridge_foreground']);order.append('native_route');return {'dispatched':True}
  async def dispatch(operation,*args):await operation();return chat,'changed'
  loop=SimpleNamespace(config=self.cfg,store=self.store,run_kind='fixture',guarded=guarded,
   bridge=SimpleNamespace(request=AsyncMock(side_effect=request)),send_operation=AsyncMock(side_effect=dispatch))
  ui=AndroidSocialUI(loop,'com.tencent.mobileqq');ui.observe=AsyncMock(side_effect=[observation('home',foreground='com.android.launcher/.Launcher'),chat]);ui.composer=lambda o:None;ui.chat_hint=lambda o:'Contact'
  async def foreground(*args):order.append('bridge_foreground');return observation('bridge',foreground=PACKAGE+'/.MainActivity')
  ui.act=AsyncMock(side_effect=foreground);ui.self_account=AsyncMock(return_value='12345678');ui.open_hint=AsyncMock(return_value=chat);ui.verify_peer=AsyncMock(return_value=(binding,chat));ui.messages=lambda o:[{'direction':'incoming','text':'Hello'}]
  _,context,_=await ui.resolve(event=offered)
  self.assertTrue(context['event_matched']);self.assertEqual(order,['bridge_foreground','native_route'])
  self.assertEqual(ui.act.call_args.args[0],{'type':'open_app','package_name':PACKAGE})

 def reply_window(self,remaining=600):
  from acceptance.social_reply_listen import ReplyWindow
  win=ReplyWindow(self.cfg,self.session,'com.tencent.mobileqq');win.store=self.store;win.opened=time.time();win.deadline=time.monotonic()+remaining;win.bridge=SimpleNamespace(request=AsyncMock(return_value={'available':True}));win.poll_notifications=AsyncMock();win.hub=EventHub(self.store,['com.tencent.mobileqq'])
  async def guarded(awaitable):return await awaitable
  win.guarded=guarded
  win.hub.ingest({'package':'com.tencent.mobileqq','event_key':'real-event-fixture','posted_at':time.time(),'title':'Contact','text':'M4-QQ-1027 Hello'})
  return win
 async def test_new_notification_acceptance_uses_the_production_step_without_binding_shortcut(self):
  from unittest.mock import patch
  from digitallife_v2.session import SessionStopped
  win=self.reply_window();original=win.hub.next
  with patch.object(SessionLoop,'step',new=AsyncMock(return_value={'outcome':'defer'})) as production,patch('acceptance.social_reply_listen.SocialAgent') as shortcut:
   shortcut.return_value.run=AsyncMock(side_effect=AssertionError('Do not bypass production decisions'))
   with self.assertRaisesRegex(SessionStopped,'M4_INBOUND_NOT_SENT:defer'):await win.step()
   production.assert_awaited_once();shortcut.return_value.run.assert_not_awaited()
  self.assertEqual(win.hub.next,original)
 async def test_late_test_message_is_kept_before_routing_for_a_fresh_bounded_window(self):
  from unittest.mock import patch
  from digitallife_v2.session import SessionStopped
  win=self.reply_window(100)
  with patch.object(SessionLoop,'step',new=AsyncMock()) as production:
   with self.assertRaisesRegex(SessionStopped,'RESTART_WINDOW_BEFORE_ROUTE'):await win.step()
   production.assert_not_awaited()
  self.assertEqual(win.hub.counts(),{'pending':1})
 async def test_consumed_old_notification_does_not_end_a_new_receive_window(self):
  from unittest.mock import patch
  win=self.reply_window();win.bridge.request=AsyncMock(return_value={'available':False});win.inbound_wake=asyncio.Event();win.wait_for_event=AsyncMock()
  with patch.object(SessionLoop,'step',new=AsyncMock()) as production:
   self.assertEqual((await win.step())['outcome'],'waiting_for_human_test_message');production.assert_not_awaited()
