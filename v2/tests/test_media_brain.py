import json,unittest
from digitallife_v2.brain import LifeBrain
from digitallife_v2.safety import Blocked
from test_m1 import observation
import test_media as fixture


class MediaPlannerReferenceTests(unittest.IsolatedAsyncioTestCase):
    save=fixture.MediaTests.save
    def setUp(self):
        fixture.MediaTests.setUp(self)
        self.bid='a'*64
        self.store.media_context={'enabled':True,'sources':[{'source_id':self.source,'title':'Fixture video','summary':'Offline sample','uncertainty':'Fixture only','source_type':'live_scripted'}],
            'share_targets':[{'binding_id':self.bid,'app':'com.tencent.mobileqq','kind':'group'}]}

    async def decide(self,action):
        async def transport(payload):
            return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'intent':'explore','action':action,'note':'Offline fixture'})}}],
                'usage':{'prompt_tokens':1,'completion_tokens':1}}
        return await LifeBrain(self.cfg,self.store,transport).decide(observation('fixture'),{'com.reader':['com.reader/.Main']},[])

    async def test_only_offered_source_and_group_can_be_chosen(self):
        action={'type':'share_video_to_group','source_id':self.source,'binding_id':self.bid}
        self.assertEqual((await self.decide(action))['action'],action)

    async def test_model_cannot_invent_another_video_source(self):
        with self.assertRaisesRegex(Blocked,'MODEL_REPAIR_EXHAUSTED'):
            await self.decide({'type':'post_video_comment','source_id':'video:'+'f'*32})
        self.assertTrue(self.store.db.execute("SELECT 1 FROM events WHERE kind='model_invalid' AND payload LIKE '%MEDIA_SOURCE_NOT_OFFERED%'").fetchone())

    async def test_model_cannot_invent_an_unoffered_group(self):
        with self.assertRaisesRegex(Blocked,'MODEL_REPAIR_EXHAUSTED'):
            await self.decide({'type':'share_video_to_group','source_id':self.source,'binding_id':'b'*64})
        self.assertTrue(self.store.db.execute("SELECT 1 FROM events WHERE kind='model_invalid' AND payload LIKE '%MEDIA_GROUP_NOT_OFFERED%'").fetchone())
