import tempfile,time,unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch
from acceptance.media_reply_listen import MediaReplyWindow
from digitallife_v2.events import EventHub
from digitallife_v2.media import BILIBILI
from digitallife_v2.session import SessionLoop,SessionStopped
from digitallife_v2.store import LifeStore
from test_m1 import config


class NotificationAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.cfg=config(self.root);self.session=self.root/'session';self.session.mkdir()
        self.store=LifeStore(self.cfg.data,str(self.session),self.cfg.serial,'fixture');self.addCleanup(self.store.close)
        self.loop=MediaReplyWindow(self.cfg,self.session,'M5-FIXTURE');self.loop.store=self.store
        self.loop.opened=time.time()-60;self.loop.deadline=time.monotonic()+600
        self.loop.hub=EventHub(self.store,[BILIBILI,'com.tencent.mobileqq']);self.loop.bridge=True;self.loop.poll_notifications=AsyncMock()
    def ingest(self,key,package=BILIBILI,text='M5-FIXTURE A real fixture question',posted=None):
        return self.loop.hub.ingest({'package':package,'event_key':key,'posted_at':time.time() if posted is None else posted,'text':text})
    def test_only_current_real_pending_bilibili_marker_is_selected(self):
        self.ingest('old',posted=time.time()-120);self.ingest('other-package',package='com.tencent.mobileqq');self.ingest('unrelated',text='Another discussion')
        self.assertIsNone(self.loop.candidate())
        eid=self.ingest('actual');self.assertEqual(self.loop.candidate()[0],eid)
        self.store.db.execute("UPDATE inbound_events SET status='handled' WHERE id=?",(eid,));self.store.db.commit()
        self.assertIsNone(self.loop.candidate())
    async def test_actual_event_flows_through_production_step_and_filter_is_restored(self):
        eid=self.ingest('actual');original=self.loop.hub.next
        async def production():
            self.assertEqual(self.loop.hub.next()['event_id'],eid)
            self.assertEqual(self.loop.hub.next()['preview'],self.loop.candidate()[1])
            return {'outcome':'rest'}
        with patch.object(SessionLoop,'step',new=AsyncMock(side_effect=production)) as step:
            with self.assertRaisesRegex(SessionStopped,'NOT_SENT:rest'):await self.loop.step()
            step.assert_awaited_once()
        self.assertEqual(self.loop.hub.next,original)
    def test_explicit_original_window_cutoff_can_retain_the_same_real_receipt(self):
        eid=self.ingest('retained',posted=time.time()-120)
        self.assertIsNone(self.loop.candidate());self.loop.posted_after=time.time()-180
        self.assertEqual(self.loop.candidate()[0],eid)
    async def test_late_receipt_is_retained_without_opening_or_replying(self):
        self.ingest('actual');self.loop.deadline=time.monotonic()+200
        with patch.object(SessionLoop,'step',new=AsyncMock()) as step:
            with self.assertRaisesRegex(SessionStopped,'RETAINED'):await self.loop.step()
            step.assert_not_awaited()
    async def test_failed_production_inspection_is_not_automatically_replayed(self):
        from digitallife_v2.safety import Blocked
        self.ingest('actual');original=self.loop.hub.next
        with patch.object(SessionLoop,'step',new=AsyncMock(side_effect=Blocked('ACTUAL_UI_CHANGED'))) as step:
            with self.assertRaisesRegex(SessionStopped,'NOTIFICATION_BLOCKED:ACTUAL_UI_CHANGED'):await self.loop.step()
            step.assert_awaited_once()
        self.assertEqual(self.loop.hub.next,original)
