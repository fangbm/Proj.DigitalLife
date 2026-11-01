"""Operator-only real recording cancellation/parent-loss probe, self chat only."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

from digitallife_v2.config import Config
from digitallife_v2.social import SocialPolicy
from digitallife_v2.speech import SpeechEngine
from digitallife_v2.store import LifeStore,write_json
from digitallife_v2.wechat_voice import DesktopWechatTransport


async def child(directory,profile):
    config=Config.load(profile)
    payload=json.loads((directory/'PAYLOAD.json').read_text(encoding='utf8'))
    async def ready():write_json(directory/'READY.json',{'go_authorized':True,'target':'文件传输助手'})
    result=await DesktopWechatTransport(config,SimpleNamespace(session=directory)).call(payload,ready)
    write_json(directory/'UNEXPECTED_SEND_RESULT.json',result)


async def run(args):
    config=Config.load(args.profile);directory=Path(args.session).resolve();directory.mkdir(exist_ok=False,parents=True)
    store=LifeStore(config.data,str(directory),config.serial,'scripted');speech=SpeechEngine(config,store)
    account=SocialPolicy(config).read()['accounts']['com.tencent.mm'].split('@')[0]
    transport=DesktopWechatTransport(config,store)
    base={'account':account,'title':'文件传输助手'}
    task=None;process=None
    try:
        baseline=await transport.call({**base,'operation':'inspect'})
        asset=await speech.synthesize('你好，这是录音中途停止的测试。接下来会主动取消，不应发出这条消息，也不应使用实体扬声器和实体麦克风。')
        if not 6<=asset['seconds']<=20:raise ValueError('CANCEL_PROBE_REQUIRES_6_TO_20_SECOND_SOURCE')
        payload={**base,'operation':'self_send','format':'voice','text':'cancel probe',
                 'source':asset['path'],'sha256':asset['sha256'],'seconds':asset['seconds'],'intent':args.intent}
        intent_path=config.data/'windows_wechat_intents'/(hashlib.sha256(args.intent.encode()).hexdigest()+'.json')
        if intent_path.exists():raise ValueError('PROBE_INTENT_ALREADY_EXISTS_NO_REPLAY')
        write_json(directory/'PAYLOAD.json',payload)
        if args.mode=='cancel':
            async def ready():write_json(directory/'READY.json',{'go_authorized':True,'target':'文件传输助手'})
            task=asyncio.create_task(transport.call(payload,ready))
        else:
            # Only terminate this owned driver. Its worker observes stdin EOF.
            process=await asyncio.create_subprocess_exec(sys.executable,'-u','-m',__spec__.name,
                '--child',str(directory),'--profile',args.profile,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW)
        until=time.monotonic()+65;observed=None
        while time.monotonic()<until:
            if intent_path.exists():
                try:observed=json.loads(intent_path.read_text(encoding='utf8'))
                except (OSError,ValueError):pass
                if observed and observed['state']=='recording':break
                if observed and observed['state'] not in {'prepared','recording'}:raise RuntimeError('CANCEL_PROBE_MISSED_RECORDING')
            if task and task.done():await task;raise RuntimeError('CANCEL_PROBE_RETURNED_BEFORE_STOP')
            if process and process.returncode is not None:raise RuntimeError('CANCEL_PROBE_DRIVER_EXITED')
            await asyncio.sleep(.05)
        else:raise TimeoutError('RECORDING_START_NOT_OBSERVED')
        # Permit the actual virtual stream to run before interrupting.
        await asyncio.sleep(.8)
        write_json(directory/'STOP_TRIGGER.json',{'mode':args.mode,'observed_intent':observed,'at':time.time()})
        if task:
            task.cancel()
            try:await task
            except asyncio.CancelledError:pass
        else:
            process.terminate();await asyncio.wait_for(process.wait(),5)
        until=time.monotonic()+20
        while time.monotonic()<until:
            final=json.loads(intent_path.read_text(encoding='utf8'))
            leases=[json.loads(p.read_text(encoding='utf8')) for p in directory.glob('wechat-worker/*/AUDIO_LEASE.json')]
            if (final['state']=='failed_before_send' and leases
                    and all(v.get('defaults_restored') and v.get('mute_restored') and v.get('watchdog_finished') for v in leases)):break
            await asyncio.sleep(.1)
        else:raise TimeoutError('CANCEL_OR_AUDIO_RESTORATION_UNCONFIRMED')
        after=await transport.call({**base,'operation':'inspect'})
        keys=lambda result:[r['message_key'] for r in result.get('messages',[]) if r['direction']=='outgoing']
        same=keys(baseline)==keys(after)
        result={'mode':args.mode,'target':'文件传输助手','recording_really_started':True,
                'intent':final,'audio_leases':leases,'outgoing_messages_unchanged':same,
                'additional_messages_observed':0 if same else None,'passed':same and final.get('cancelled_before_send') is True,
                'scope':'real_desktop_recording_and_virtual_audio_not_contact_delivery'}
        write_json(directory/'CANCEL_RESULT.json',result)
        store.finish('completed' if result['passed'] else 'stopped','WECHAT_CANCEL_PASSED' if result['passed'] else 'WECHAT_CANCEL_PARTIAL')
        print(json.dumps(result,ensure_ascii=False));return 0 if result['passed'] else 2
    except BaseException as error:
        write_json(directory/'FAILURE.json',{'reason':str(error) or type(error).__name__,'automatic_retry':False})
        store.finish('stopped','WECHAT_CANCEL_PROBE_FAILED');raise
    finally:
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
        if process and process.returncode is None:
            process.terminate();await process.wait()
        await speech.cancel();store.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--child',type=Path)
    parser.add_argument('--session');parser.add_argument('--intent')
    parser.add_argument('--profile',default='find-x6');parser.add_argument('--mode',choices=['cancel','parent_exit'],default='cancel')
    args=parser.parse_args()
    if args.child:asyncio.run(child(args.child,args.profile));return 0
    if not args.session or not args.intent:parser.error('--session and --intent required')
    return asyncio.run(run(args))


if __name__=='__main__':raise SystemExit(main())
