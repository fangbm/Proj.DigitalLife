import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,patch
from digitallife_v2.brain import LifeBrain
from digitallife_v2.config import Config
from digitallife_v2.content import ContentPerception,sample
from digitallife_v2.events import EventHub
from digitallife_v2.safety import Blocked,parse_decision
from digitallife_v2.store import LifeStore,open_database
from test_m1 import config,observation,decision


class M3Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.config=config(self.root)
        self.directory=self.root/'session';self.directory.mkdir()
        self.store=LifeStore(self.config.data,str(self.directory),self.config.serial,'fixture')
        self.addCleanup(self.store.close)
        self.hub=EventHub(self.store,['io.digitallife.companion'])

    def incoming(self,key='key-one'):
        return {'package':'io.digitallife.companion','event_key':key,'posted_at':time.time(),'title':'测试通知','text':'同样的内容'}

    def test_event_payload_cannot_overwrite_journal_kind_source_or_timestamp(self):
        self.store.event('content_started',{'kind':'watch_video','source':'forged','at':'forged'})
        row=json.loads((self.directory/'events.jsonl').read_text(encoding='utf-8').splitlines()[-1])
        self.assertEqual(row['kind'],'content_started');self.assertEqual(row['source'],'v2');self.assertNotEqual(row['at'],'forged')

    def test_inbox_deduplicates_source_event_but_not_separate_identical_messages(self):
        a=self.incoming();eid=self.hub.ingest(a)
        self.assertIsNotNone(eid);self.assertIsNone(self.hub.ingest(a))
        self.assertNotEqual(eid,self.hub.ingest(self.incoming('key-two')))
        self.assertEqual(self.hub.counts(),{'pending':2})
        event=self.hub.next();self.assertFalse(event['context_complete']);self.assertIsNone(event['conversation_ref'])
        fresh=EventHub(self.store,['io.digitallife.companion'])
        self.assertEqual(fresh.next()['event_id'],eid)

    def test_inbox_rejects_unlisted_or_stale_sources_and_expires_private_preview(self):
        self.assertIsNone(self.hub.ingest({**self.incoming(),'package':'com.other'}))
        self.assertIsNone(self.hub.ingest({**self.incoming(),'posted_at':time.time()-4000}))
        eid=self.hub.ingest(self.incoming())
        self.store.db.execute('UPDATE inbound_events SET expires=?',(time.time()-1,));self.store.db.commit()
        self.assertIsNone(self.hub.next())
        self.assertEqual(self.store.db.execute('SELECT preview FROM inbound_events WHERE id=?',(eid,)).fetchone(),('',))
        log=(self.directory/'events.jsonl').read_text(encoding='utf-8')
        self.assertNotIn('同样的内容',log)

    def test_retained_snapshot_preserves_actual_post_time_and_deduplicates_callback(self):
        raw={**self.incoming(),'posted_at':time.time()-120,'source_kind':'active_snapshot'}
        eid=self.hub.ingest(raw)
        self.assertEqual(self.hub.next()['posted_at'],raw['posted_at'])
        self.assertIsNone(self.hub.ingest({**raw,'source_kind':'callback'}))
        event=json.loads(self.store.db.execute("SELECT payload FROM events WHERE kind='inbound_received'").fetchone()[0])
        self.assertEqual(event['source_kind'],'active_snapshot')
        self.assertEqual(self.hub.counts(),{'pending':1})

    def test_reply_now_is_only_an_intent_never_an_outbound_action(self):
        self.hub.ingest(self.incoming());offered=self.hub.next()
        self.hub.resolve(offered,{'event_id':offered['event_id'],'decision':'reply_now','note':'稍后可答复'})
        self.assertIsNone(self.hub.next())
        event=json.loads(self.store.db.execute("SELECT payload FROM events WHERE kind='inbound_decision'").fetchone()[0])
        self.assertFalse(event['outbound_dispatched']);self.assertEqual(event['reply_now_deferred_to'],'M4')
        self.assertFalse(self.store.db.execute("SELECT 1 FROM events WHERE kind='action_dispatched'").fetchone())

    def test_defer_does_not_hot_loop_and_unknown_event_reference_is_rejected(self):
        self.hub.ingest(self.incoming());offered=self.hub.next()
        self.hub.resolve(offered,{'event_id':'wrong','decision':'ignore'})
        self.assertIsNone(self.hub.next())
        self.assertEqual(self.hub.counts(),{'pending':1})

    def test_removed_source_is_not_offered_from_the_persisted_inbox(self):
        self.hub.ingest(self.incoming())
        revoked=EventHub(self.store,[])
        self.assertIsNone(revoked.next())
        self.assertEqual(revoked.counts(),{'pending':1})

    def test_activity_and_event_metadata_have_bounded_strict_schema(self):
        for kind in ['watch_video','read_article']:
            self.assertEqual(parse_decision(json.dumps(decision({'type':kind,'seconds':20})))['action']['type'],kind)
            for seconds in [True,0,61]:
                with self.assertRaises(Blocked):parse_decision(json.dumps(decision({'type':kind,'seconds':seconds})))
        p=decision({'type':'rest','seconds':5});p['event_response']={'event_id':'x','decision':'send','note':''}
        with self.assertRaises(Blocked):parse_decision(json.dumps(p))

    def test_default_vision_is_only_enabled_for_confirmed_deepseek_model(self):
        p=self.root/'profile.json';p.write_text(json.dumps({'serial':self.config.serial,'identity':self.config.identity}),encoding='utf-8')
        with patch.dict('os.environ',{},clear=True):
            c=Config.load(str(p));self.assertTrue(c.vision);self.assertEqual(c.image_token_reserve,1024)
        with patch.dict('os.environ',{'DIGITALLIFE_V2_MODEL':'unknown-model'},clear=True):self.assertFalse(Config.load(str(p)).vision)

    def test_sampling_keeps_real_body_and_marks_subtitles_only_from_identified_controls(self):
        obs=observation();obs.controls=[replace(obs.controls[0],text='导航')]
        obs.controls.append(replace(obs.controls[0],text='正文内容'*40,resource='body'))
        obs.controls.append(replace(obs.controls[0],text='实际字幕',resource='subtitle'))
        captured=sample(obs,3)
        self.assertIn('正文内容'*40,captured['texts']);self.assertEqual(captured['subtitles'],['实际字幕'])

    async def test_content_sampling_is_bounded_deduplicated_and_never_dispatches(self):
        before=observation();phone=SimpleNamespace(observe=AsyncMock(side_effect=[observation('two'),observation('three')]),dispatch=AsyncMock())
        async def guarded(awaitable):return await awaitable
        async def wait(seconds):pass
        async def summarize(system,data,images,validate):
            self.assertTrue(data['audio_unavailable']);self.assertFalse(images)
            self.assertEqual(len(data['samples']),1)
            value={'summary':'可见正文摘要','topics':['阅读'],'evidence_ids':[before.observation_id],'uncertainty':'只有当前页文字，无音频。'};validate(value);return value
        loop=SimpleNamespace(config=self.config,run_kind='fixture',store=self.store,phone=phone,
            brain=SimpleNamespace(structured=summarize),memory=None,guarded=guarded,wait_for_event=wait,inbound_wake=asyncio.Event(),observed=lambda o:self.store.event('observed',o.summary()))
        result=await ContentPerception(loop).run(decision({'type':'read_article','seconds':10}),before)
        phone.dispatch.assert_not_awaited();self.assertEqual(phone.observe.await_count,2)
        row=self.store.db.execute('SELECT source_type,status,body FROM content_segments WHERE id=?',(result['segment_id'],)).fetchone()
        self.assertEqual(row[:2],('fixture','summarized'));self.assertEqual(len(json.loads(row[2])['samples']),3)
        self.assertFalse(self.store.db.execute('SELECT 1 FROM memories').fetchone())

    async def test_perception_repair_rejects_invented_evidence_and_accounts_both_requests(self):
        responses=[{'summary':'invented','evidence_ids':['fake']},{'summary':'observed','evidence_ids':['offered']}]
        transport=AsyncMock(side_effect=[{'choices':[{'finish_reason':'stop','message':{'content':json.dumps(r)}}]} for r in responses])
        def validate(value):
            if value['evidence_ids']!=['offered']:raise ValueError()
        value=await LifeBrain(self.config,self.store,transport).structured('summary',{},[],validate)
        self.assertEqual(value['summary'],'observed');self.assertEqual(self.store.state['requests'],2)

    async def test_inbound_classification_is_required_and_uses_one_bounded_repair(self):
        self.hub.ingest(self.incoming());self.store.inbound_context=self.hub.next()
        bad={'event_id':'not-offered','decision':'read_only','note':'错误引用'}
        complete={'event_id':self.store.inbound_context['event_id'],'decision':'read_only','note':'仅阅读'}
        transport=AsyncMock(side_effect=[{'choices':[{'finish_reason':'stop','message':{'content':json.dumps(r)}}]} for r in [bad,complete]])
        result=await LifeBrain(self.config,self.store,transport).decide(observation(),{},[])
        self.assertEqual(result['event_response']['decision'],'read_only');self.assertEqual(transport.await_count,2)
        self.assertEqual(result['action'],{'type':'rest','seconds':1})

    async def test_modified_image_never_enters_provider_request(self):
        image=self.root/'frame.png';image.write_bytes(b'changed')
        transport=AsyncMock()
        with self.assertRaisesRegex(Blocked,'HASH_MISMATCH'):
            await LifeBrain(self.config,self.store,transport).structured('summary',{},[{'observation_id':'frame','png_path':str(image),'png_sha256':hashlib.sha256(b'original').hexdigest()}],lambda v:None)
        transport.assert_not_awaited();self.assertEqual(self.store.state['requests'],0)

    def test_m2_migration_has_consistent_backup_and_preserves_life_data(self):
        self.store.db.execute('PRAGMA user_version=2');self.store.db.commit()
        db=open_database(self.config.data);db.close()
        backups=list((self.config.data/'backups').glob('pre-m3-*.sqlite3'));self.assertEqual(len(backups),1)
        backup=sqlite3.connect(backups[0]);self.addCleanup(backup.close)
        self.assertEqual(backup.execute('PRAGMA user_version').fetchone()[0],2)
        self.assertEqual(backup.execute('SELECT count(*) FROM sessions').fetchone()[0],1)


if __name__=='__main__':unittest.main()
