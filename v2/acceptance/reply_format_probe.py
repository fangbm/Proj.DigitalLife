"""Synthetic operator scenarios, real model and native self-send, one output."""
import argparse,asyncio,json,time
from digitallife_v2.brain import LifeBrain
from digitallife_v2.config import Config
from digitallife_v2.session import SessionLoop,SessionStopped
from digitallife_v2.store import write_json
from digitallife_v2.voice import QQVoiceAgent
from digitallife_v2 import __version__

class FormatProbe(SessionLoop):
    def __init__(self,config,directory,scenario):
        super().__init__(config,directory,minutes=4,brain_factory=lambda c,s:LifeBrain(c,s))
        self.scenario=scenario
        self.voice_acceptance_context='操作员合成场景测试，不是真人来信；目标仅为当前QQ账号自己。自然决定是否回复及文字/语音格式，最多发一条，不照抄模板。'
    async def step(self):
        if not self.voice_inbox:raise SessionStopped('FORMAT_PROBE_NATIVE_BRIDGE_UNAVAILABLE')
        agent=QQVoiceAgent(self,self.voice_inbox);binding=await self.guarded(agent.sender.binding('self'))
        cue=('现在不方便听语音，请用一句简短文字打个招呼。' if self.scenario=='text' else '我现在不方便看屏幕，可以用一句简短语音打个招呼吗？')
        source='operator-format:'+self.scenario+':'+__version__
        result=await agent.compose(binding,'auto_reply',source,[],{'text':cue,'complete':True,'uncertain':False,
            'source_ref':source,'engine_id':'operator_synthetic_scenario','trust':'scripted_test_data_not_human_incoming'},False,
            ['text','voice'],agent.sender.settings())
        passed=result.get('outcome')=='sent_observed' and result.get('reply_format')==self.scenario
        write_json(self.directory/'FORMAT_READY.json',{'source':'SYNTHETIC_SCENARIO_REAL_MODEL_REAL_NATIVE_SELF_SEND',
            'scenario':self.scenario,'model_selected':result.get('reply_format'),'result':result,'accepted':passed,
            'human_incoming':False,'maximum_outgoing':1,'speaker_used':False,'microphone_used':False})
        raise SessionStopped('FORMAT_PROBE_ACCEPTED' if passed else 'FORMAT_PROBE_OTHER_DECISION')
def main():
    p=argparse.ArgumentParser();p.add_argument('--profile',default='find-x6');p.add_argument('--session',required=True);p.add_argument('--scenario',choices=['text','voice'],required=True)
    a=p.parse_args();r=asyncio.run(FormatProbe(Config.load(a.profile),a.session,a.scenario).run());print(json.dumps(r));return 0 if r['reason']=='FORMAT_PROBE_ACCEPTED' else 2
if __name__=='__main__':raise SystemExit(main())
