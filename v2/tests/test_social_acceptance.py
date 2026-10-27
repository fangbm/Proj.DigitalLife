"""Offline harness checks: model deferral or missing SQL proof is never a pass."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,patch
from acceptance.social_reply_probe import Probe
from acceptance.group_probe import Probe as GroupProbe
from digitallife_v2.safety import Blocked
from digitallife_v2.session import SessionStopped
from digitallife_v2.store import LifeStore
from test_m1 import config


class AcceptanceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name);self.cfg=config(root);directory=root/'session';directory.mkdir()
        self.store=LifeStore(self.cfg.data,str(directory),self.cfg.serial,'fixture');self.addCleanup(self.store.close)
        self.loop=SimpleNamespace()
        self.probe=Probe(self.cfg,self.store,self.loop,'com.tencent.mobileqq',0,'auto_reply')

    async def test_model_deferral_does_not_write_ready_or_advance_to_success(self):
        with patch('acceptance.social_reply_probe.SocialAgent') as agent:
            agent.return_value.run=AsyncMock(return_value={'outcome':'defer'})
            with self.assertRaisesRegex(SessionStopped,'NOT_CONFIRMED:defer'):
                await self.probe.decide(None,{},[])
        self.assertFalse((self.store.directory/'M4_READY.json').exists())
        self.assertEqual(self.probe.stage,0)

    async def test_ready_file_without_persisted_send_proof_is_not_a_pass(self):
        self.probe.stage=1
        (self.store.directory/'M4_READY.json').write_text(json.dumps({'result':{'outbound_id':'not-a-sent-outbox'}}),encoding='utf-8')
        with self.assertRaisesRegex(SessionStopped,'M4_SEND_EVIDENCE_MISSING'):
            await self.probe.decide(None,{},[])

    async def test_group_ui_block_stops_the_harness_instead_of_reopening_repeatedly(self):
        probe=GroupProbe(self.cfg,self.store,self.loop,'com.tencent.mobileqq',None)
        with patch('acceptance.group_probe.SocialAgent') as agent:
            agent.return_value.run=AsyncMock(side_effect=Blocked('KNOWN_HEADER_AMBIGUITY'))
            with self.assertRaisesRegex(SessionStopped,'GROUP_ACCEPTANCE_BLOCKED:KNOWN_HEADER_AMBIGUITY'):
                await probe.decide(None,{},[])
            agent.return_value.run.assert_awaited_once()

    async def test_group_receipt_cannot_promote_an_offline_fixture_to_device_evidence(self):
        from acceptance.group_receipt import load
        from test_m1 import observation
        obs=observation('fixture-source');self.store.event('observed',obs.summary())
        path=self.store.directory/'receipt.json'
        path.write_text(json.dumps({'account_observation':obs.observation_id,'group_observation':obs.observation_id,'message_observation':obs.observation_id}),encoding='utf-8')
        with self.assertRaisesRegex(Blocked,'NOT_A_REAL_DEVICE_OBSERVATION'):
            load(SimpleNamespace(config=self.cfg,store=self.store),path)
