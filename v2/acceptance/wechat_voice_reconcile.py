"""Read the latest native voice for one existing outbound; never sends."""
import argparse
import asyncio
import json
from pathlib import Path
import re
from digitallife_v2.config import Config
from digitallife_v2.safety import Blocked
from digitallife_v2.social import SocialStore
from digitallife_v2.store import LifeStore,write_json
from digitallife_v2.wechat_voice import DesktopWechatTransport,re_title


async def run(args):
    if not re.fullmatch('[a-f0-9]{32}',args.outbound_id):raise ValueError('EXISTING_OUTBOUND_ID_REQUIRED')
    config=Config.load(args.profile);directory=Path(args.session).resolve();directory.mkdir(exist_ok=False,parents=True)
    store=LifeStore(config.data,str(directory),config.serial,'scripted')
    try:
        row=store.db.execute('SELECT o.text,o.status,b.account,b.body FROM social_outbox o JOIN social_bindings b ON b.id=o.binding_id '
            'WHERE o.id=? AND o.device=? AND b.app=?',(args.outbound_id,config.serial,'com.tencent.mm')).fetchone()
        if not row or row[1] not in {'sent_observed','delivery_unknown'}:raise Blocked('WECHAT_OBSERVED_OR_UNKNOWN_OUTBOUND_REQUIRED')
        result=await DesktopWechatTransport(config,store).call({'operation':'readback','account':row[2].split('@')[0],
            'title':re_title(json.loads(row[3])['route_hint'])})
        normalize=lambda text:''.join(c for c in text.casefold() if c.isalnum())
        match=normalize(result.get('outgoing',{}).get('text',''))==normalize(row[0])
        evidence={**result,'outbound_id':args.outbound_id,'expected_text':row[0],'full_text_match':match,
            'original_outbox_status':row[1],'reconciled_read_only':True,'additional_messages_sent':0,
            'human_listening_verified':False,'automatic_replay':False}
        write_json(directory/'READBACK.json',evidence)
        if not match:raise Blocked('WECHAT_OUTGOING_TRANSCRIPT_MISMATCH')
        if row[1]=='delivery_unknown':SocialStore(store).reconcile_wechat_record(args.outbound_id,evidence)
        store.finish('completed','WECHAT_NATIVE_VOICE_READ_ONLY_CONFIRMED');print(json.dumps(evidence,ensure_ascii=False));return 0
    except BaseException as error:
        write_json(directory/'FAILURE.json',{'reason':str(error) or type(error).__name__,'additional_messages_sent':0})
        store.finish('stopped','WECHAT_READ_ONLY_CONFIRMATION_FAILED');raise
    finally:store.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile',default='find-x6');parser.add_argument('--session',required=True)
    parser.add_argument('--outbound-id',required=True)
    return asyncio.run(run(parser.parse_args()))


if __name__=='__main__':raise SystemExit(main())
