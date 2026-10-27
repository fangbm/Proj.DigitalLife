"""Real literal Unicode input in the companion's local-only editor."""
import argparse
import asyncio
import json
from digitallife_v2.bridge import PACKAGE
from digitallife_v2.config import Config
from digitallife_v2.input_bridge import InputBridge
from digitallife_v2.session import SessionLoop, SessionStopped
from digitallife_v2.store import write_json
from digitallife_v2.social import digest

TEXT = '你好，M4 中文输入验证🙂：标点 %s 保持原样。'


class Probe:
    def __init__(self, config, store, loop):
        self.store, self.loop, self.stage = store, loop, 0

    async def decide(self, observation, apps, recent):
        if self.stage == 0:
            self.stage = 1
            action = {'type': 'open_app', 'package_name': PACKAGE}
        elif self.stage == 1:
            choices = [c for c in observation.controls if c.text == '测试中文输入' and c.clickable]
            if len(choices) != 1: raise SessionStopped('UNICODE_TEST_BUTTON_MISSING')
            self.stage = 2
            action = {'type': 'tap', 'control_id': choices[0].control_id}
        elif self.stage == 2:
            choices = [c for c in observation.controls if c.resource == 'android:id/edit' and c.is_editor and not c.text]
            if len(choices) != 1: raise SessionStopped('UNICODE_TEST_EDITOR_MISSING')
            self.stage = 3
            action = {'type': 'tap', 'control_id': choices[0].control_id}
        elif self.stage == 3:
            controller = InputBridge(self.loop)
            original = await controller.selected()
            async with controller.temporary():
                fresh = await self.loop.guarded(self.loop.phone.observe()); self.loop.observed(fresh)
                after = await controller.insert(fresh, 'local-unicode-acceptance', TEXT)
            restored = await controller.selected()
            if restored != original: raise SessionStopped('UNICODE_IME_RESTORE_UNVERIFIED')
            write_json(self.store.directory / 'UNICODE_READY.json', {'source': 'LIVE_ANDROID_INPUT_CONNECTION_AND_INDEPENDENT_UI',
                'observation_id': after.observation_id, 'text_sha256': digest(TEXT), 'literal_text': TEXT,
                'original_ime_restored': True, 'external_message_sent': False})
            self.stage = 4
            action = {'type': 'home'}
        else:
            raise SessionStopped('UNICODE_ACCEPTANCE_DONE')
        return {'intent': 'explore', 'action': action, 'note': 'Explicit local-only Unicode input acceptance'}


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--session', required=True); parser.add_argument('--profile', default='find-x6')
    args = parser.parse_args()
    config = Config.load(args.profile)
    loop = SessionLoop(config, args.session, minutes=4, brain_factory=lambda c, s: Probe(c, s, loop))
    result = asyncio.run(loop.run()); print(json.dumps(result))
    return 0 if result['reason'] == 'UNICODE_ACCEPTANCE_DONE' else 2


if __name__ == '__main__': raise SystemExit(main())
