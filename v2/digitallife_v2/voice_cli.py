"""Operator entry for silent synthesis and QQ voice sends; never plays audio."""
import argparse
import asyncio
import json
from pathlib import Path
import time
from .config import Config
from .qq_voice import QQVoiceSender
from .safety import Blocked,DeviceLock
from .speech import SpeechEngine
from .store import LifeStore,write_json


async def run(config,args):
    if args.command=='status':
        login=await QQVoiceSender(config,None,None).status()
        return {'provider':'napcat','connected':True,'account_suffix':str(login['user_id'])[-4:],
            'speaker_used':False,'microphone_used':False,'messages_sent':0}
    with DeviceLock(config.serial) as lock:
        task=asyncio.create_task(convert_or_send(config,args));deadline=time.monotonic()+240
        try:
            while not task.done():
                if (args.session and (Path(args.session)/'STOP').exists()) or lock.emergency.exists():raise Blocked('VOICE_STOP_REQUESTED')
                if time.monotonic()>=deadline:raise Blocked('VOICE_DEADLINE')
                await asyncio.wait({task},timeout=.1)
            return await task
        finally:
            if not task.done():task.cancel()
            await asyncio.gather(task,return_exceptions=True)


async def convert_or_send(config,args):
    if not args.session:raise ValueError('VOICE_SESSION_REQUIRED')
    directory=Path(args.session).resolve();directory.mkdir(parents=True,exist_ok=False)
    store=LifeStore(config.data,str(directory),config.serial,'scripted')
    engine=SpeechEngine(config,store)
    try:
        sender=QQVoiceSender(config,store,engine)
        if args.command=='reconcile':
            if not args.outbound_id or args.message_id is None:raise ValueError('VOICE_RECONCILIATION_REFERENCES_REQUIRED')
            result=await sender.reconcile(args.outbound_id,args.message_id)
            if result['trigger_id'].startswith('qq-voice:'):
                from .voice import QQVoiceInbox
                QQVoiceInbox(config,store).finish(result['trigger_id'][9:],'sent_observed',outbound_id=args.outbound_id,reconciled_read_only=True)
            write_json(directory/'VOICE_RESULT.json',result);store.finish('completed','LATE_NATIVE_VOICE_CONFIRMED_NO_RESEND');return result
        if args.command=='send':await sender.status()
        if bool(args.speech_ref)==bool(args.text):raise ValueError('EXACTLY_ONE_VOICE_TEXT_OR_SPEECH_REF_REQUIRED')
        asset=engine.get(args.speech_ref) if args.speech_ref else await engine.synthesize(args.text)
        if args.command=='synthesize':result={'outcome':'synthesized_only','asset':asset,'messages_sent':0}
        else:
            if not args.intent:raise ValueError('VOICE_SEND_INTENT_REQUIRED')
            result=await sender.send(asset['speech_ref'],target=args.target,target_kind=args.target_kind,
                intent=args.intent,kind=args.kind)
        write_json(directory/'VOICE_RESULT.json',result)
        store.finish('completed',result['outcome']);return result
    except BaseException as error:
        store.finish('stopped',str(error) if isinstance(error,(Blocked,ValueError)) else type(error).__name__)
        raise
    finally:
        await engine.cancel();store.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('status','synthesize','send','reconcile'))
    parser.add_argument('--profile',default='find-x6');parser.add_argument('--session')
    parser.add_argument('--text');parser.add_argument('--speech-ref')
    parser.add_argument('--target',default='self');parser.add_argument('--target-kind',choices=('dm','group'),default='dm')
    parser.add_argument('--kind',choices=('auto_reply','proactive_dm','group_post'),default='proactive_dm')
    parser.add_argument('--intent')
    parser.add_argument('--outbound-id');parser.add_argument('--message-id',type=int)
    args=parser.parse_args()
    try:
        result=asyncio.run(run(Config.load(args.profile),args))
        # Full local paths, account IDs and tokens stay in the private result file.
        print(json.dumps({k:v for k,v in result.items() if k!='asset'},ensure_ascii=False))
        return 0 if result.get('outcome')!='delivery_unknown' else 2
    except (Blocked,ValueError,OSError,asyncio.TimeoutError) as error:
        print(json.dumps({'outcome':'unavailable','reason':str(error) if isinstance(error,(Blocked,ValueError)) else type(error).__name__}));return 2


if __name__=='__main__':raise SystemExit(main())
