"""Real-device direct-text reading across overlapping pages; real model summaries."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from digitallife_v2.brain import LifeBrain
from digitallife_v2.bridge import PACKAGE
from digitallife_v2.config import Config
from digitallife_v2.phone import PhoneController
from digitallife_v2.safety import DeviceLock
from digitallife_v2.session import SessionLoop, SessionStopped
from digitallife_v2.store import write_json


async def provision(config, source, remote_name):
    if remote_name not in {'article.json', 'speech.wav'}:
        raise ValueError('FIXED_MEDIA_NAME_REQUIRED')
    with DeviceLock(config.serial):
        phone = PhoneController(config, config.data / 'unused-reading-setup')
        await phone.identity()
        temporary = '/data/local/tmp/digitallife-' + remote_name
        await phone._adb('push', str(source), temporary)
        await phone._adb('shell', 'run-as', PACKAGE, 'cp', temporary, 'files/' + remote_name)
        checksum = (await phone._text('shell', 'run-as', PACKAGE, 'sha256sum', 'files/' + remote_name)).split()[0]
        if checksum != hashlib.sha256(source.read_bytes()).hexdigest():
            raise ValueError('READING_MEDIA_HASH_MISMATCH')
        await phone._adb('shell', 'rm', temporary)


class ReaderProbe:
    def __init__(self, config, store):
        self.store = store; self.real = LifeBrain(config, store)
        self.stage = 0; self.page = 0; self.repeat = False

    async def structured(self, *args):
        return await self.real.structured(*args)

    async def decide(self, observation, apps, recent):
        if self.stage == 0:
            self.stage = 1
            action = {'type': 'open_app', 'package_name': PACKAGE}
        elif self.stage == 1:
            targets = [c for c in observation.controls if c.text == '阅读长文验收材料' and c.clickable]
            if len(targets) != 1: raise SessionStopped('READER_BUTTON_UNAVAILABLE')
            self.stage = 2
            action = {'type': 'tap', 'control_id': targets[0].control_id}
        elif self.stage == 2:
            if not observation.foreground.endswith('ReaderActivity'): raise SessionStopped('READER_FOREGROUND_UNVERIFIED')
            self.stage = 3
            action = {'type': 'read_article', 'seconds': 10}
        else:
            row = self.store.db.execute("SELECT id,body,status FROM content_segments WHERE session=? AND kind='read_article' ORDER BY started DESC LIMIT 1", (self.store.session,)).fetchone()
            if not row: raise SessionStopped('READING_SOURCE_MISSING')
            body = json.loads(row[1])
            if body.get('source_mode') != 'xml_text': raise SessionStopped('READING_NOT_DIRECT_TEXT')
            if body['progress']['pending_paragraphs']:
                action = {'type': 'read_article', 'seconds': 10}
            elif self.page < 2:
                targets = [c for c in observation.controls if c.text == '下一页' and c.clickable and c.enabled]
                if len(targets) != 1: raise SessionStopped('NEXT_PAGE_UNAVAILABLE')
                self.page += 1; self.stage = 2
                action = {'type': 'tap', 'control_id': targets[0].control_id}
            elif not self.repeat:
                self.repeat = True
                action = {'type': 'read_article', 'seconds': 10}
            else:
                if row[2] != 'already_read': raise SessionStopped('READING_REPEAT_NOT_DEDUPLICATED')
                requests = [json.loads(r[0]) for r in self.store.db.execute("SELECT payload FROM events WHERE session=? AND kind='perception_request'", (self.store.session,))]
                if any(r['image_observation_ids'] for r in requests): raise SessionStopped('READING_UNEXPECTED_IMAGE_INPUT')
                write_json(self.store.directory / 'READING_READY.json', {'source': 'LIVE_DEVICE_SCRIPTED_READING_REAL_TEXT_MODEL',
                    'progress': body['progress'], 'repeated_page_outcome': row[2], 'image_inputs': 0,
                    'text_model_requests': len(requests), 'source_observation_id': observation.observation_id})
                raise SessionStopped('READING_ACCEPTANCE_DONE')
        return {'intent': 'explore', 'action': action, 'note': 'Explicit direct-text article acceptance'}


async def run(config, directory, article):
    await provision(config, article, 'article.json')
    result = await SessionLoop(config, directory, minutes=8, brain_factory=ReaderProbe).run()
    write_json(Path(directory) / 'ARTICLE_SOURCE.json', {'file': str(article), 'sha256': hashlib.sha256(article.read_bytes()).hexdigest()})
    return result


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--session', required=True); parser.add_argument('--profile', default='find-x6')
    parser.add_argument('--article', default='data/m3-reading/article.json'); args = parser.parse_args()
    result = asyncio.run(run(Config.load(args.profile), args.session, Path(args.article).resolve()))
    print(json.dumps(result)); return 0 if result['reason'] == 'READING_ACCEPTANCE_DONE' else 2

if __name__ == '__main__': raise SystemExit(main())
