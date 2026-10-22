"""Offline observer-prototype contracts. No device/API/permission entry point.

These assessments cannot grant production execution, even for a valid packet.
"""
import hashlib
import json
import re

ROW_PATH='0/0/0/0/0/4/0/0/10'
TITLE_PATH=ROW_PATH+'/1'
SELECTOR_SHA256=hashlib.sha256(('com.android.settings|android:id/title|显示与亮度|'+ROW_PATH+'|'+TITLE_PATH).encode()).hexdigest()


def visibility_evidence(native, *, path=(), actual_dumper_sha256=None, reviewed_dumper_sha256=None, path_mapping_verified=False):
    """Describe a reviewed assumption; never fill Control.visible or trust XML.

    The actual OEM component/provenance must be independently verified by the
    reviewer. Equal caller strings alone are not a trust root or approval.
    """
    if native is not None and type(native)is not bool:raise ValueError('INVALID_VISIBILITY')
    if native is not None:return {'level':'NATIVE_EXPLICIT','condition_satisfied':native}
    verified=(isinstance(actual_dumper_sha256,str) and re.fullmatch('[a-f0-9]{64}',actual_dumper_sha256)
        and actual_dumper_sha256==reviewed_dumper_sha256 and path_mapping_verified is True)
    descendant=bool(path) and all(type(i)is int and i>=0 for i in path)
    return {'level':'REVIEWED_DUMPER_FILTER_CONDITIONAL' if verified and descendant else 'XML_PRESENCE_ONLY',
        'condition_satisfied':bool(verified and descendant),'native_value':None}


def validate_packet(raw, *, nonce, host_started_ns, host_received_ns, now_ns):
    def fail():raise ValueError('OBSERVER_PACKET_REJECTED')
    def unique(pairs):
        d={}
        for k,v in pairs:
            if k in d:fail()
            d[k]=v
        return d
    if not isinstance(raw,bytes) or len(raw)>32768:fail()
    packet=json.loads(raw,object_pairs_hook=unique,parse_constant=lambda _:fail())
    if set(packet)!={'schema','nonce','selector_sha256','row_path','title_path','frames','disconnected'}:fail()
    if (packet['schema']!='find-x6-target-observer/1' or not re.fullmatch('[a-f0-9]{32}',nonce)
            or packet['nonce']!=nonce or packet['selector_sha256']!=SELECTOR_SHA256 or
            packet['row_path']!=ROW_PATH or packet['title_path']!=TITLE_PATH or packet['disconnected']is not True):fail()
    if any(type(t)is not int for t in (host_started_ns,host_received_ns,now_ns)):fail()
    if not 0<=host_started_ns<=host_received_ns<=now_ns or now_ns-host_started_ns>2_000_000_000:fail()
    frames=packet['frames']
    if not isinstance(frames,list) or len(frames)!=2:fail()
    keys={'sample_started_ns','sample_finished_ns','window_id','window_layer','window_bounds','window_active','window_focused',
          'package','width','height','rotation','ancestry_visible','same_window','refreshed','row','title'}
    rect=lambda r:(isinstance(r,list) and len(r)==4 and all(type(x)is int for x in r) and
        0<=r[0]<r[2]<=1240 and 0<=r[1]<r[3]<=2772)
    for f in frames:
        if not isinstance(f,dict) or set(f)!=keys:fail()
        if (f['package']!='com.android.settings' or type(f['width'])is not int or f['width']!=1240 or
                type(f['height'])is not int or f['height']!=2772 or type(f['rotation'])is not int or f['rotation']!=0):fail()
        if any(type(f[k])is not int or f[k]<0 for k in ('sample_started_ns','sample_finished_ns','window_id','window_layer')):fail()
        if not f['sample_started_ns']<=f['sample_finished_ns']:fail()
        if any(f[k]is not True for k in ('window_active','window_focused','ancestry_visible','same_window','refreshed')):fail()
        if not rect(f['window_bounds']):fail()
        for k in ('row','title'):
            n=f[k]
            if not isinstance(n,dict) or set(n)!={'bounds','visible','editable','clickable','enabled','checkable','password','long_clickable'}:fail()
            if any(type(n[p])is not bool for p in n if p!='bounds') or not rect(n['bounds']):fail()
            if not n['visible'] or not n['enabled'] or any(n[p] for p in ('editable','checkable','password','long_clickable')):fail()
        if f['row']['clickable']is not True or f['title']['clickable']is not False:fail()
        a,b,c,d=f['row']['bounds'];x,y,r,t=f['title']['bounds']
        if not a<=x<r<=c or not b<=y<t<=d or not 2772*.08<=b<d<=2772*.92:fail()
    if frames[0]['sample_finished_ns']>frames[1]['sample_started_ns']:fail()
    remote_duration=frames[1]['sample_finished_ns']-frames[0]['sample_started_ns']
    if remote_duration>host_received_ns-host_started_ns:fail()
    stable=lambda f:{k:v for k,v in f.items() if not k.startswith('sample_')}
    if stable(frames[0])!=stable(frames[1]):fail()
    return {'native_target_sample_consistent':True,'host_interval_ns':host_received_ns-host_started_ns,
        'execution_allowed':False,'reason':'PROTOTYPE_NOT_INTEGRATED; input/identity/lease/approval/final guard still required'}


def touch_point_assessment(handles, point, settings_id, *, compositor_verified=False):
    """Offline, point-scoped model of the pinned AOSP finger-input rules.

    Full native flag parsing/source ownership must be established outside this
    toy model. This is not a ColorOS allowlist or a production input policy.
    """
    unresolved=[];observers=[];receiver=None
    for h in handles:
        a,b,c,d=h['region'];x,y=point
        if not a<=x<c or not b<=y<d:continue
        flags=set(h['flags'])
        if flags & {'NOT_VISIBLE','NOT_TOUCHABLE','NO_INPUT_CHANNEL'}:continue
        if not h['channel_present']:unresolved.append('CHANNEL_STATE_CONFLICT');continue
        if 'SPY'in flags:
            observers.append(h['id'])
            if h.get('owner_and_takeover_policy_verified')is not True:unresolved.append('SPY_TAKEOVER_POLICY_UNVERIFIED')
            continue
        receiver=h['id']
        if h.get('owner_verified')is not True:unresolved.append('INPUT_OWNER_UNVERIFIED')
        elif receiver!=settings_id:
            return {'classification':'TARGET_INPUT_BLOCKED','receiver':receiver,'observers':observers,'execution_allowed':False}
        break
    if receiver!=settings_id:unresolved.append('SETTINGS_NOT_PROVEN_RECEIVER')
    if not compositor_verified:unresolved.append('DISPLAY_OCCLUSION_UNVERIFIED')
    return {'classification':'INSUFFICIENT_TARGET_EVIDENCE' if unresolved else 'TARGET_INPUT_SAFE',
        'receiver':receiver,'observers':observers,'reasons':sorted(set(unresolved)),'execution_allowed':False}


def validate_diagnostic_result(raw, *, nonce, host_started_ns, host_received_ns, now_ns):
    """Stage-v2 diagnostic envelope; success still passes the unchanged v1 guard.

    Failure reports are diagnostics only: never observations or freshness resets.
    No device, model, executor, storage or permission issuance is reachable here.
    """
    def fail():raise ValueError('OBSERVER_STAGE_PACKET_REJECTED')
    def unique(pairs):
        result={}
        for k,v in pairs:
            if k in result:fail()
            result[k]=v
        return result
    if not isinstance(raw,bytes) or len(raw)>32768:fail()
    p=json.loads(raw,object_pairs_hook=unique,parse_constant=lambda _:fail())
    if not isinstance(p,dict) or p.get('schema')!='find-x6-observer-stage/2':fail()
    if type(p.get('duration_ns'))is not int or p['duration_ns']<0:fail()
    c=p.get('cleanup')
    types={'NONE','REJECTED','SECURITY_EXCEPTION','TIMEOUT_EXCEPTION','INTERRUPTED_EXCEPTION',
           'ILLEGAL_STATE_EXCEPTION','REFLECTION_EXCEPTION','LINKAGE_ERROR','OTHER'}
    stages={'PROCESS','ENVIRONMENT','AUTOMATION','CONNECT','CONFIGURE','WINDOW','ROOT','TARGET',
            'ATTRIBUTES','RELEASE','CLEANUP','SERIALIZE'}
    codes={'INIT_FAILED','CONNECT_FAILED','CONFIGURE_FAILED','WINDOW_UNAVAILABLE','WINDOW_AMBIGUOUS',
           'ROOT_UNAVAILABLE','TARGET_NOT_FOUND','TARGET_AMBIGUOUS','TARGET_REJECTED','TARGET_LOOKUP_FAILED',
           'ATTRIBUTE_READ_FAILED','ATTRIBUTE_UNAVAILABLE','DISCONNECT_FAILED','OBSERVATION_TIMEOUT',
           'PERMISSION_DENIED','INTERNAL_UNKNOWN','INVALID_NONCE'}
    if (not isinstance(c,dict) or set(c)!={'status','exception_type'} or
            c['status'] not in ('OK','FAILED','UNKNOWN','NOT_STARTED') or c['exception_type'] not in types):fail()
    if p.get('status')=='SUCCESS':
        if (set(p)!={'schema','status','stage','duration_ns','cleanup','packet'} or p['stage']!='CLEANUP'
                or c!={'status':'OK','exception_type':'NONE'}):fail()
        result=validate_packet(json.dumps(p['packet'],ensure_ascii=False).encode(),nonce=nonce,
            host_started_ns=host_started_ns,host_received_ns=host_received_ns,now_ns=now_ns)
        span=p['packet']['frames'][1]['sample_finished_ns']-p['packet']['frames'][0]['sample_started_ns']
        if not span<=p['duration_ns']<=host_received_ns-host_started_ns:fail()
        return result|{'diagnostic_status':'SUCCESS'}
    if (p.get('status') not in ('FAILED','PARTIAL') or
            set(p)!={'schema','status','code','stage','last_completed_stage','exception_type','duration_ns','cleanup'} or
            p['code'] not in codes or p['stage'] not in stages or p['last_completed_stage'] not in stages or
            p['exception_type'] not in types):fail()
    return {'diagnostic_status':p['status'],'failure':p,'native_target_sample_consistent':False,'execution_allowed':False}
