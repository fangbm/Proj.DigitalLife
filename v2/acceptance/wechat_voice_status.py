"""Read current account and native UI readiness; never send or record."""
import argparse
import asyncio
import json
from pathlib import Path
from digitallife_v2.config import Config
from digitallife_v2.social import SocialPolicy
from digitallife_v2.store import LifeStore,write_json
from digitallife_v2.wechat_voice import DesktopWechatTransport


async def run(args):
    config=Config.load(args.profile);directory=Path(args.session).resolve();directory.mkdir(parents=True,exist_ok=False)
    store=LifeStore(config.data,str(directory),config.serial,'scripted')
    account=SocialPolicy(config).read().get('accounts',{}).get('com.tencent.mm','').split('@')[0]
    try:
        result=await DesktopWechatTransport(config,store).call({'operation':'status','account':account,'title':'文件传输助手'})
        write_json(directory/'WECHAT_STATUS.json',{**result,'messages_sent':0,'recording_started':False})
        store.finish('completed','WECHAT_DESKTOP_NATIVE_UI_READY');return result
    except BaseException as error:
        write_json(directory/'WECHAT_STATUS.json',{'outcome':'unavailable','reason':str(error)[:300],
            'messages_sent':0,'recording_started':False})
        store.finish('stopped','WECHAT_DESKTOP_NATIVE_UI_UNAVAILABLE');raise
    finally:store.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--session',required=True)
    parser.add_argument('--profile',default='find-x6');args=parser.parse_args()
    print(json.dumps(asyncio.run(run(args)),ensure_ascii=False));return 0


if __name__=='__main__':raise SystemExit(main())
