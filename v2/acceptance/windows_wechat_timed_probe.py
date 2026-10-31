"""Operator-only Windows WeChat self voice test; never used by the normal loop."""
import argparse
import asyncio
import json
from pathlib import Path

from digitallife_v2.config import Config
from digitallife_v2.speech import SpeechEngine, run_worker
from digitallife_v2.store import LifeStore, write_json


async def run(args):
    config = Config.load(args.profile)
    directory = Path(args.session).resolve(); directory.mkdir(parents=True, exist_ok=False)
    store = LifeStore(config.data, str(directory), config.serial, 'scripted')
    speech = SpeechEngine(config, store)
    try:
        asset = await speech.synthesize(args.text)
        if not .25 <= asset['seconds'] <= 20:
            raise ValueError('Windows self voice requires a source between .25 and 20 seconds')
        payload = {'source':asset['path'], 'sha256':asset['sha256'], 'directory':str(directory),
                   'data':str(config.data), 'intent':args.intent}
        write_json(directory/'PREPARED.json', {'target':'文件传输助手', 'asset':asset,
            'operator_only':True, 'maximum_outgoing':1, 'gui_executed':False})
        if args.prepare_only:
            store.finish('completed', 'WINDOWS_SELF_VOICE_PREPARED_NO_GUI')
            return {'outcome':'prepared_only', 'messages_sent':0, 'gui_executed':False}
        result = await run_worker([str(config.data/'tts-env/Scripts/python.exe'),
            '-m', 'acceptance.windows_wechat_timed_worker'], payload, timeout=asset['seconds']+25)
        write_json(directory/'WINDOWS_SELF_VOICE_RESULT.json', result)
        store.finish('completed', 'WINDOWS_SELF_VOICE_ACCEPTED' if result.get('accepted') else 'WINDOWS_SELF_VOICE_PARTIAL')
        return result
    except BaseException as error:
        write_json(directory/'FAILURE.json', {'reason':str(error)[:1000],
            'automatic_retry':False, 'delivery_may_be_unknown':not args.prepare_only})
        store.finish('stopped', 'WINDOWS_SELF_VOICE_FAILED_NO_AUTOMATIC_RETRY')
        raise
    finally:
        await speech.cancel(); store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', default='find-x6')
    parser.add_argument('--session', required=True)
    parser.add_argument('--intent', required=True)
    parser.add_argument('--text', required=True)
    parser.add_argument('--prepare-only', action='store_true')
    result = asyncio.run(run(parser.parse_args()))
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get('accepted') or result.get('outcome') == 'prepared_only' else 2


if __name__ == '__main__':raise SystemExit(main())
