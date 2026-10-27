"""Load an actual observed group receipt; no fabricated notification or UI data."""
from dataclasses import fields
from datetime import datetime
import hashlib
import json
from pathlib import Path
import time
from digitallife_v2.perception import Observation,Control
from digitallife_v2.groups import WX,wechat_metadata
from digitallife_v2.social_ui import AndroidSocialUI
from digitallife_v2.safety import Blocked


def load(loop,path):
    if Path(path).stat().st_size>8192:raise Blocked('GROUP_RECEIPT_SPEC_INVALID')
    spec=json.loads(Path(path).read_text(encoding='utf-8'))
    if set(spec)!={'account_observation','group_observation','message_observation'}:
        raise Blocked('GROUP_RECEIPT_SPEC_INVALID')
    def observed(oid):
        row=loop.store.db.execute("SELECT e.payload FROM events e JOIN sessions s ON s.id=e.session WHERE e.kind='observed' AND s.serial=? AND s.run_kind IN ('autonomous','scripted') AND json_extract(e.payload,'$.observation_id')=?",(loop.config.serial,oid)).fetchone()
        if not row:raise Blocked('GROUP_RECEIPT_NOT_A_REAL_DEVICE_OBSERVATION')
        value=json.loads(row[0]);names={f.name for f in fields(Observation)}
        args={k:v for k,v in value.items() if k in names};args['controls']=[Control(**c) for c in value['controls']]
        args['monotonic']=time.monotonic()-max(0,time.time()-datetime.fromisoformat(value['captured_at']).timestamp())
        obs=Observation(**args)
        if obs.serial!=loop.config.serial or obs.foreground.split('/')[0]!=WX or hashlib.sha256(Path(obs.png_path).read_bytes()).hexdigest()!=obs.png_sha256:
            raise Blocked('GROUP_RECEIPT_IMAGE_OR_DEVICE_MISMATCH')
        return obs
    account=observed(spec['account_observation']);group=observed(spec['group_observation']);message=observed(spec['message_observation'])
    if not group.foreground.endswith('chatroom.ui.ChatroomInfoUI') or len({c.text for c in account.controls}&{'服务','收藏','表情','设置'})<2:
        raise Blocked('GROUP_RECEIPT_IDENTITY_ROLE_INVALID')
    age=time.time()-datetime.fromisoformat(message.captured_at).timestamp()
    if not 0<=age<=3600:raise Blocked('GROUP_RECEIPT_EXPIRED')
    ui=AndroidSocialUI(loop,WX);aid=ui.wx_identity(account);peer,_=wechat_metadata(group)
    loop.store.event('group_receipt_provenance_checked',{'proof_observations':list(spec.values()),
        'message_observation_id':message.observation_id,'original_captured_at':message.captured_at,
        'source':'existing_observed_events_and_intact_PNGs','used_for_model_context_only':True})
    return {'identity':(WX,aid,peer,'group'),'observation':message,'proof_observations':list(spec.values())}
