"""Manually started, finite Find X6 sessions. Strict physical mode is separate.

No action approval files, helper deployment or arbitrary shell interface. Nullable
native properties stay nullable; limited XML+PNG navigation evidence is explicit.
"""
from __future__ import annotations
import argparse
import asyncio
from dataclasses import asdict,replace
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid
from .capabilities import CapabilityRegistry,GroundingError
from .device_profiles import PhysicalDeviceProfile,parse_safety,parse_size
from .devices import DeviceTransportError
from .life_subject import LifeBinding
from .models import Action,ModelResponse,ScreenObservation,VerificationResult
from .physical_execution import PhysicalContract,PhysicalPhone,LeaseNamespace
from .policy import ActionPolicy,PolicyDenied
from .semantic import UIAutomatorBackend,normalize

MODE='dedicated_device_test'
PROVIDER='dedicated_uiautomator_xml/1'
FRESH_NS=30_000_000_000 # Explicit operational bound, not the old native 2s contract.
SETTINGS='com.android.settings'

def validate_config(config):
    from .longrun_state import SoakPolicy
    expected=[] if config.get('app_scope')=='installed_launchable' else [SETTINGS]
    if (config.get('execution_mode')!=MODE or config.get('mode')!='api' or
            config.get('allowed_apps')!=expected or config.get('serial')!='profile-bound' or
            config.get('app_scope') not in {None,'installed_launchable'}):
        raise ValueError('DEDICATED_SCOPE_REQUIRED')
    SoakPolicy(**config['policy'])
    if config.get('test_controller') not in {None,'settings_smoke'}:raise ValueError('INVALID_TEST_CONTROLLER')
    spec=config['physical_execution']
    if spec.get('approval_path'):raise ValueError('DEDICATED_MODE_HAS_NO_ACTION_APPROVAL')
    if spec.get('control_path')!=str(Path(config['session_directory'])/'soak-control.sqlite3'):
        raise ValueError('DEDICATED_CONTROL_BINDING_MISMATCH')

class DedicatedContract(PhysicalContract):
    def authorize(self):
        validate_config(self.config)
        apps=[] if self.config.get('app_scope')=='installed_launchable' else [{'package':SETTINGS}]
        if not self.profile.read_only_authorized or self.manifest.get('apps')!=apps or self.manifest.get('app_scope')!=self.config.get('app_scope'):
            raise PermissionError('DEDICATED_PROFILE_OR_SCOPE_MISMATCH')
        # Consent is the explicit CLI start of this finite session, not a DPAPI
        # grant. Each dispatch still checks live control, leases and audit rows.
        return self

def dedicated_admission(value,base):
    validate_config(value['config'])
    # Status is truly host-only. Online/foreground are checked by the owned
    # executor under the lease, never guessed here or probed by a status query.
    if value['config'].get('test_controller')=='settings_smoke' and base['safety_admission_allowed'] and base['admission_reason_code'] in {'PERSISTED_WAKE','BUDGET_WAIT'}:
        base={**base,'worker_launch_allowed':True,'admission_reason_code':'MANUAL_SMOKE_NO_API'}
    return {**base,'execution_mode':MODE,'physical_device':{'bound':True,'online':None,
        'probe_status':'checked_by_owned_executor','user_authorized':True},
        'per_action_approval_required':False,'helper_required':False}

class DedicatedRegistry(CapabilityRegistry):
    version='dedicated-settings-navigation/1'
    routes=CapabilityRegistry.routes | frozenset({'显示与亮度','关于本机','关于设备','电池用量','存储用量'})
    unsafe=re.compile(CapabilityRegistry.unsafe.pattern+r'|follow|comment|publish|subscribe|评论|关注|点赞|分享|收藏|授权|卸载|恢复|开发者',re.I)
    def wire(self):
        return {'version':self.version,'execution_mode':MODE,'allowed_packages':[SETTINGS],
            'low_risk_actions':['tap_control','scroll_region','navigate_back','finish'],
            'navigation_routes':sorted(self.routes),'evidence':'current PNG + single-root XML + foreground + basic lock/occlusion',
            'missing_visible':'UNKNOWN; limited dedicated-session navigation judgement',
            'per_action_approval':False,'arbitrary_shell':False,'text_entry':False}
    def evidence_for_action(self,s,action):
        return bool(s.provider==PROVIDER and s.foreground_package==SETTINGS and s.controls and
            not s.overlays and all(c.package==SETTINGS for c in s.controls) and
            s.evidence_quality in {'partial','trusted'} and s.orientation in {0,1,2,3})
    @staticmethod
    def navigation_node_allowed(node):return node.editable is not True
    def page(self,s,action=None):
        if not self.evidence_for_action(s,action):raise PolicyDenied('DEDICATED_SETTINGS_EVIDENCE_REQUIRED')
    def safe_target(self,s,target,scroll=False):
        self.page(s)
        if target not in s.controls:raise PolicyDenied('TARGET_NOT_IN_CURRENT_XML')
        a,b,c,d=target.bounds
        if not (0<=a<c<=s.screen_width and 0<=b<d<=s.screen_height and b<s.screen_height*.92 and d>s.screen_height*.08):
            raise PolicyDenied('INVALID_APPLICATION_BOUNDS')
        if min(d,int(s.screen_height*.88))<=max(b,int(s.screen_height*.12)):raise PolicyDenied('NO_USABLE_APPLICATION_BODY')
        if target.visible is False or target.enabled is not True:raise PolicyDenied('TARGET_HIDDEN_OR_DISABLED')
        nodes={n.control_id:n for n in s.controls};ancestor=target;seen=set()
        while ancestor:
            if ancestor.control_id in seen or ancestor.visible is False or ancestor.window_id!=target.window_id:
                raise PolicyDenied('TARGET_ANCESTRY_OR_WINDOW_INVALID')
            seen.add(ancestor.control_id)
            if ancestor.parent is not None and ancestor.parent not in nodes:raise PolicyDenied('UNBOUND_ANCESTRY')
            ancestor=nodes.get(ancestor.parent)
        if any(getattr(target,k)is not False for k in ('checkable','password','long_clickable')) or target.editable is True or any(t in target.class_name for t in self.state_classes):
            raise PolicyDenied('STATE_CHANGING_CONTROL_DENIED')
        if scroll:
            if target.scrollable is not True or not any(k in target.class_name for k in ('RecyclerView','ScrollView','ListView')):
                raise PolicyDenied('IDENTIFIED_VERTICAL_LIST_REQUIRED')
            return
        if target.clickable is not True or target.class_name not in {'android.widget.LinearLayout','android.widget.RelativeLayout'}:
            raise PolicyDenied('KNOWN_NAVIGATION_ROW_REQUIRED')
        children=self.subtree(s,target);labels={n.text.strip().casefold() for n in children}
        if not any(n.resource_id=='android:id/title' and n.text.strip().casefold() in self.routes for n in children) or self.unsafe.search(' '.join(labels)):
            raise PolicyDenied('READONLY_ROUTE_REQUIRED')
        if any(n.package!=SETTINGS or n.visible is False or n.editable is True or n.checkable is not False or n.password is not False or
               n.long_clickable is not False or any(k in n.class_name for k in self.state_classes) or
               not (a<=n.bounds[0]<=n.bounds[2]<=c and b<=n.bounds[1]<=n.bounds[3]<=d) for n in children):
            raise PolicyDenied('UNSAFE_NAVIGATION_DESCENDANT')
    def view(self,s):
        safe=[]
        if self.evidence_for_action(s,None):
            for target in s.controls:
                for scroll in (False,True):
                    try:self.safe_target(s,target,scroll)
                    except PolicyDenied:continue
                    safe.append({'control_id':target.control_id,'intent':'scroll_region' if scroll else 'tap_control',
                        'bounds':target.bounds,'route_labels':sorted({n.text for n in self.subtree(s,target) if n.text.strip().casefold() in self.routes}),
                        'capability':'scroll_readonly' if scroll else 'navigate_readonly'})
        return {'observation_id':s.observation_id,'screenshot_sha256':s.screenshot_sha256,'foreground_package':s.foreground_package,
            'foreground_activity':s.foreground_activity,'controls':safe,'evidence_quality':s.evidence_quality,
            'evidence_scope':'dedicated XML+PNG navigation; missing visible/editable remain UNKNOWN',
            'known_page_labels':sorted({c.text for c in s.controls if c.text.strip().casefold() in self.routes}),
            'capabilities':self.wire()}
    def ground(self,candidate,s):
        self.page(s);kind=candidate.get('type')
        if not isinstance(kind,str):raise GroundingError('INVALID_SCHEMA','Known action type required')
        if kind in {'text','home','launch','open_allowed_app'}:raise PolicyDenied('DEDICATED_ACTION_NOT_ALLOWED')
        if kind in {'back','navigate_back'} and any(c.resource_id=='android:id/title' and c.text=='设置' for c in s.controls):
            raise PolicyDenied('BACK_FROM_SETTINGS_HOME_DENIED')
        if kind in {'swipe','scroll_region'}:
            if kind=='swipe':
                action=Action.from_dict(candidate)
                from .policy import validate_coordinates
                validate_coordinates(action,type('Size',(),{'width':s.screen_width,'height':s.screen_height,'observation_id':s.observation_id})())
                if abs(action.x-action.x2)>s.screen_width*.1 or not 100<=action.duration_ms<=1500:raise PolicyDenied('VERTICAL_BOUNDED_SWIPE_REQUIRED')
                targets=[c for c in s.controls if c.scrollable is True and c.bounds[0]<=action.x<c.bounds[2] and c.bounds[1]<=action.y<c.bounds[3] and c.bounds[0]<=action.x2<c.bounds[2] and c.bounds[1]<=action.y2<c.bounds[3]]
                if len(targets)!=1:raise PolicyDenied('UNAMBIGUOUS_SCROLL_REGION_REQUIRED')
                candidate={'type':'scroll_region','reason':action.reason,'observation_id':s.observation_id,
                    'scrollable_region_id':targets[0].control_id,'direction':'toward_end' if action.y2<action.y else 'toward_start'}
            if set(candidate)!={'type','reason','observation_id','scrollable_region_id','direction'} or not isinstance(candidate['reason'],str) or len(candidate['reason'])>400 or candidate['observation_id']!=s.observation_id or not isinstance(candidate['direction'],str) or candidate['direction'] not in {'toward_end','toward_start'}:
                raise GroundingError('INVALID_SCROLL','Current list ID and bounded direction required')
            target=next((c for c in s.controls if c.control_id==candidate['scrollable_region_id']),None)
            if target is None:raise GroundingError('UNKNOWN_CONTROL','Current scroll region required')
            self.safe_target(s,target,True);a,b,c,d=target.bounds
            top,bottom=max(b,int(s.screen_height*.12)),min(d,int(s.screen_height*.88))
            if bottom-top<140:raise PolicyDenied('SCROLL_REGION_TOO_SMALL')
            x=(a+c)//2;low,high=top+(bottom-top)//4,top+3*(bottom-top)//4
            y,y2=(high,low) if candidate['direction']=='toward_end' else (low,high)
            for n in s.controls:
                if n.visible is False:continue
                l,t,r,bt=n.bounds
                if any(l<=x<r and t<=p<bt for p in (y,y2)) and (n.editable is True or n.checkable is not False or n.password is not False or any(k in n.class_name for k in self.state_classes)):
                    raise PolicyDenied('SCROLL_ENDPOINT_UNSAFE')
            action=Action('swipe',candidate['reason'],x=x,y=y,x2=x,y2=y2,duration_ms=400)
            return action,{'registry_version':self.version,'observation_id':s.observation_id,'actual_action':action.wire(),
                'target':target.identity(),'control_id':target.control_id,'capability':'scroll_readonly','bounds':target.bounds,
                'evidence_source':PROVIDER,'foreground_package':SETTINGS,'foreground_activity':s.foreground_activity}
        return super().ground(candidate,s)
    def revalidate(self,binding,original,current):
        self.page(original);self.page(current)
        if (original.foreground_activity,original.orientation,original.screen_width,original.screen_height)!=(current.foreground_activity,current.orientation,current.screen_width,current.screen_height):
            raise GroundingError('SCENE_CHANGED','Same foreground and geometry required')
        target=binding.get('target')
        if target is not None:
            matches=[n for n in current.controls if n.identity()==target]
            if len(matches)!=1:raise GroundingError('CONTROL_CHANGED','One unchanged target required')
            self.safe_target(current,matches[0],binding['actual_action']['type']=='swipe')
        return {**binding,'fresh_observation_id':current.observation_id,'fresh_screenshot_sha256':current.screenshot_sha256,
            'pre_dispatch_consistency':'verified','stability':{'mode':'dedicated_current_xml_target','explicit_visible_required':False}}
    def recovery_action(self,*args):raise PolicyDenied('NO_UNREQUESTED_RECOVERY_INPUT')

class DedicatedActionPolicy(ActionPolicy):
    def __init__(self,contract):super().__init__('dedicated_device_test',frozenset({SETTINGS}));self.contract=contract
    def check(self,action,observation=None):
        self.contract.authorize()
        if action.type not in {'tap','swipe','back','finish'}:raise PolicyDenied('DEDICATED_ACTION_NOT_ALLOWED')
        if action.type!='finish' and (observation is None or not action.observation_id):raise PolicyDenied('CURRENT_OBSERVATION_REQUIRED')
        super().check(action,observation)

class DedicatedSemanticProvider:
    async def collect(self,phone,obs):
        from .semantic import SemanticObservation
        try:
            raw=await UIAutomatorBackend().read(phone,obs)
            result=normalize(raw,obs,await phone.foreground(),PROVIDER)
            if result.orientation!=phone._scene_rotation:raise ValueError('XML_SCENE_ROTATION_CHANGED')
            if not 0<=time.monotonic_ns()-obs.captured_monotonic_ns<=FRESH_NS:raise ValueError('DEDICATED_OBSERVATION_EXPIRED')
            phone.last_semantic=result
            return replace(result,attempts=({'source':'dedicated_png_xml','visible_inference':'none',
                'window_binding':'single XML root + matching Settings foreground + per-node package',
                'maximum_observation_age_ns':FRESH_NS},))
        except (ValueError,PolicyDenied) as e:
            return SemanticObservation(obs.observation_id,obs.sha256,obs.width,obs.height,None,'','',PROVIDER,'',failure_reason=type(e).__name__)

class DedicatedPhone(PhysicalPhone):
    execution_mode=MODE
    def foreground_allowed(self,package):return package==SETTINGS
    deny_sensitive_scene=True
    def __init__(self,contract,transport=None):
        super().__init__(contract,transport);self.capabilities=DedicatedRegistry();self.last_semantic=None
        self.post_observation=None;self.capture_dir=Path(contract.config['session_directory'])/'screens'
    async def _scene(self):
        # Basic positive lock/focus/overlay evidence. An unparsed OEM input layer
        # alone is not a veto, and absence of a veto is not native visibility.
        m={k:await self.probe.text(v) for k,v in (
            ('power',('shell','dumpsys','power')),('policy',('shell','dumpsys','window','policy')),
            ('windows',('shell','dumpsys','window','windows')),('displays',('shell','dumpsys','window','displays')),
            ('input',('shell','dumpsys','input')))}
        windows=m['windows']+'\n'+m['displays'];s=parse_safety(m['power'],m['policy'],windows,m['input'])
        focuses=re.findall(r'mCurrentFocus=Window\{[^}\r\n]*?\s([\w.]+/[\w.$]+)',windows)
        if not focuses or len(set(focuses))!=1 or not self.foreground_allowed(focuses[0].split('/')[0]):raise PolicyDenied('FOREGROUND_UNVERIFIED')
        if s['locked']is not False or s['interactive']is not True or s['orientation']is None:raise PolicyDenied('SCREEN_LOCK_OR_POWER_UNSAFE')
        # Reject explicitly visible foreign application/overlay windows, not
        # unknown system input handles. System bars are outside action body.
        for block in re.split(r'(?=\bWindow #\d+ Window\{)',m['windows']):
            if re.search(r'mViewVisibility=0x0\b',block) and 'mHasSurface=true' in block:
                pkg=re.search(r'\bpackage=([\w.]+)',block)
                if self.deny_sensitive_scene and re.search(r'permissioncontroller|securitypermission|biometric|credential|\bty=(?:TYPE_)?(?:KEYGUARD|KEYGUARD_DIALOG|2004|2009)\b',block,re.I):
                    raise PolicyDenied('VISIBLE_SECURITY_OVERLAY')
        self._window_metadata=m
        self._observed_package=focuses[0].split('/')[0]
        self._scene_rotation=s['orientation']
        return {'locked':False,'interactive':True,'orientation':s['orientation'],'foreground':focuses[0]}
    async def observe(self):
        if self._unknown:raise PolicyDenied('PHYSICAL_UNKNOWN_DISPATCH_NEEDS_REVIEW')
        await self._identity_online();before=await self._scene()
        png=await self.probe.transport.run(self.serial,('exec-out','screencap','-p'))
        obs=ScreenObservation.from_png(png,self.serial)
        await self._identity_online()
        if await self._scene()!=before:raise PolicyDenied('SCENE_CHANGED_DURING_PNG')
        self.capture_dir.mkdir(exist_ok=True)
        with (self.capture_dir/(obs.observation_id+'.png')).open('xb') as f:f.write(png)
        self._last_png=obs
        return obs
    async def foreground(self):return (await self._scene())['foreground']
    async def hierarchy(self):
        await self._identity_online();before=await self._scene()
        raw=await self.probe.text(('exec-out','uiautomator','dump','/proc/self/fd/1'))
        start,end=raw.find('<?xml'),raw.rfind('</hierarchy>')
        if start<0 or end<0:raise PolicyDenied('UIAUTOMATOR_XML_UNAVAILABLE')
        if await self._scene()!=before:raise PolicyDenied('SCENE_CHANGED_DURING_XML')
        xml=raw[start:end+len('</hierarchy>')]
        if len(xml.encode())>2_000_000:raise PolicyDenied('XML_OUTPUT_LIMIT')
        self.capture_dir.mkdir(exist_ok=True);path=self.capture_dir/(hashlib.sha256(xml.encode()).hexdigest()+'.xml')
        if not path.exists():
            with path.open('x',encoding='utf8') as f:f.write(xml)
        return xml
    def prepare_dispatch(self,action,semantic,binding,store,leases,run_id,owner,attempt_id,limit):
        # Reuse strict core's lease, control, binding and budget checks. The
        # in-memory tuple is an audit handoff, not a human-issued action token.
        super().prepare_dispatch(action,semantic,binding,store,leases,run_id,owner,attempt_id,limit)
    def _final_guard(self,store,leases,run_id,owner,attempt_id):
        # Strict core additionally insists on run_kind=autonomous. For an
        # explicitly selected smoke test, only its manual-targeted row differs.
        if not self.contract.config.get('test_controller'):return super()._final_guard(store,leases,run_id,owner,attempt_id)
        from .longrun_state import SessionLedger,TERMINAL
        ledger=SessionLedger(self.contract.config['physical_execution']['control_path'],readonly=True)
        try:
            v=ledger.load()
            if v['state']in TERMINAL or v.get('safety_stop') or v['operator']!='running' or v['worker_signal']=='stop' or v['active_pid']!=os.getpid() or ledger.remaining()<=0:
                raise PolicyDenied('PHYSICAL_CONTROL_STOPPED')
            if v['fingerprint']!=self.contract.state['fingerprint']:raise PolicyDenied('PHYSICAL_CONFIG_CHANGED')
        finally:ledger.close()
        for key in ('real','autonomous-api-writer','subject:'+self.contract.binding.life_subject_id):
            row=leases.conn.execute('SELECT owner,expires_at FROM lease WHERE device=?',(key,)).fetchone()
            if not row or row[0]!=owner or row[1]<=time.time():raise PolicyDenied('PHYSICAL_OWNED_LEASE_REQUIRED')
        row=store.conn.execute('SELECT a.payload,t.device,t.run_kind,t.status FROM action_attempts a JOIN task_runs t USING(task_run_id) WHERE a.attempt_id=? AND a.task_run_id=?',(attempt_id,run_id)).fetchone()
        if not row or tuple(row)[1:]!=(self.contract.binding.execution_device_id,'manual_targeted','running') or json.loads(row[0]).get('execution_status')!='uncertain_after_transport_error':
            raise PolicyDenied('PHYSICAL_PREWRITE_AUDIT_REQUIRED')
    async def act(self,action):
        handoff,self._permit=self._permit,None
        if not handoff or handoff[0]!=action or self._unknown:raise PolicyDenied('AUDITED_DISPATCH_HANDOFF_REQUIRED')
        _,semantic,binding,store,leases,run_id,owner,attempt_id,limit,_=handoff
        self.contract.authorize();await self._identity_online()
        fresh=await self.observe();current=await DedicatedSemanticProvider().collect(self,fresh)
        self.capabilities.revalidate(binding,semantic,current)
        DedicatedActionPolicy(self.contract).check(action,replace(fresh,observation_id=action.observation_id))
        # Evidence of a touch blocker at these points is a veto; unknown native
        # input types by themselves are not. Checks never grant broader actions.
        await self._identity_online();scene=await self._scene()
        if scene['orientation']!=current.orientation or scene['foreground']!=current.foreground_package+'/'+current.foreground_activity:
            raise PolicyDenied('DEDICATED_FINAL_SCENE_CHANGED')
        self._check_touch_blockers(action)
        if not 0<=time.monotonic_ns()-fresh.captured_monotonic_ns<=FRESH_NS:raise PolicyDenied('DEDICATED_OBSERVATION_EXPIRED')
        self.prepare_dispatch(action,current,binding,store,leases,run_id,owner,attempt_id,limit);self._permit=None
        self.pre_action_semantic=current
        store.update_attempt(attempt_id,dedicated_execution={'mode':MODE,'pre_png_sha256':fresh.sha256,
            'pre_observation_id':fresh.observation_id,'pre_tree_sha256':current.tree_sha256,
            'observed_monotonic_ns':fresh.captured_monotonic_ns,'visible_inferred':False,'per_action_approval':False})
        self._final_guard(store,leases,run_id,owner,attempt_id)
        args={'tap':('tap',str(action.x),str(action.y)),
            'swipe':('swipe',str(action.x),str(action.y),str(action.x2),str(action.y2),str(action.duration_ms)),
            'back':('keyevent','4')}.get(action.type)
        if args is None:raise PolicyDenied('DEDICATED_ACTION_NOT_ALLOWED')
        self._dispatched+=1
        try:
            await self._send_input(args)
            self.post_observation=await self.observe()
            post=await DedicatedSemanticProvider().collect(self,self.post_observation)
            self.capabilities.page(post)
            store.update_attempt(attempt_id,dedicated_post_observation={'observation_id':self.post_observation.observation_id,
                'png_sha256':self.post_observation.sha256,'tree_sha256':post.tree_sha256,
                'body_changed':post.content_sha256!=current.content_sha256,'window_binding':'XML+foreground+package'})
        except BaseException as e:
            self._unknown=True
            try:store.update_attempt(attempt_id,transport_error='PHYSICAL_ADB_OUTCOME_UNKNOWN_NO_RESEND')
            except Exception:pass
            if isinstance(e,asyncio.CancelledError):raise
            raise DeviceTransportError('PHYSICAL_ADB_OUTCOME_UNKNOWN_NO_RESEND') from None
        return 'dedicated_input_confirmed_by_post_observation; goal independently verified'
    def _check_touch_blockers(self,action):
        if action.type not in {'tap','swipe'}:return
        points=[(action.x,action.y)]+([(action.x2,action.y2)] if action.type=='swipe' else [])
        for block in re.split(r'(?=\bWindow #\d+ Window\{)',self._window_metadata.get('windows','')):
            if not (re.search(r'mViewVisibility=0x0\b',block) and 'mHasSurface=true' in block):continue
            pkg=re.search(r'\bpackage=([\w.]+)',block)
            foreign=pkg and pkg[1] not in {getattr(self,'_observed_package',SETTINGS),'com.android.systemui'}
            overlay=bool(re.search(r'\bty=(?:TYPE_)?(?:APPLICATION_OVERLAY|SYSTEM_ALERT|2038|2003)\b',block))
            if not foreign and not overlay:continue
            rect=re.search(r'(?:mFrame|frame)=\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]',block)
            if not rect:raise PolicyDenied('VISIBLE_FOREIGN_OVERLAY_UNBOUNDED')
            l,t,r,b=map(int,rect.groups())
            if any(l<=x<r and t<=y<b for x,y in points):raise PolicyDenied('TARGET_OCCLUSION_OBSERVED')
        for line in self._window_metadata.get('input','').splitlines():
            if not any(k in line for k in ('DROP_INPUT','DROP_INPUT_IF_OBSCURED','pilferingPointers=true')):continue
            region=re.search(r'touchableRegion=\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]',line)
            if region:
                l,t,r,b=map(int,region.groups())
                if any(l<=x<r and t<=y<b for x,y in points):raise PolicyDenied('TARGET_TOUCH_BLOCKER_OBSERVED')

class SettingsSmokeGui:
    model='manual-settings-smoke-no-model'
    calls=0
    async def propose(self,goal,observation,*args,**kwargs):
        view=self.semantic_context_provider();self.calls+=1
        if self.calls==1:
            target=next((c for c in view['controls'] if c['intent']=='tap_control' and '显示与亮度' in c['route_labels']),None)
            value=({'type':'tap_control','reason':'Manual Settings navigation smoke','observation_id':view['observation_id'],'control_id':target['control_id']}
                if target else {'type':'finish','reason':'Display row not currently visible','outcome':'cannot_proceed'})
        else:value={'type':'finish','reason':'Independent post-click check required','outcome':'achieved'}
        return ModelResponse(json.dumps(value,ensure_ascii=False),provider='manual_test_controller',model=self.model)

async def run_settings_smoke(life,phone,store,cfg):
    class Verifier:
        async def verify(self,goal,final,history):
            post=phone.last_semantic
            attempts=[r for r in history if r.execution_status=='executed' and r.action.type=='tap']
            before=getattr(phone,'pre_action_semantic',None)
            changed=bool(len(attempts)==1 and post and before and
                any(c.text=='显示与亮度' and c.bounds[1]<post.screen_height*.3 and c.clickable is False and
                    any(k in c.resource_id.lower() for k in ('title','toolbar','action_bar')) for c in post.controls) and
                post.tree_sha256!=before.tree_sha256 and post.content_sha256!=before.content_sha256)
            return VerificationResult('verified' if changed else 'not_met',['Settings toolbar + changed screenshot'] if changed else [],
                'Independent read after manual navigation','dedicated_settings_navigation',final.sha256)
    life.verifier=Verifier()
    result=await life.execute_goal('打开显示与亮度页面（人工导航测试）',run_kind='manual_targeted')
    payload=asdict(result)
    store.event(cfg['device'],'dedicated_settings_smoke',{'task_run_id':result.task_run_id,'status':result.status,
        'identity_sha256':phone.profile.identity_sha256,'gui_inputs':phone._dispatched,'model_api_calls':0,
        'independent_page_change':result.status=='succeeded','old_actions_replayed':0})
    return payload

def _backup(source,destination):
    with Path(destination).open('xb'):pass
    src=sqlite3.connect(Path(source).resolve().as_uri()+'?mode=ro',uri=True)
    dst=sqlite3.connect(destination)
    try:
        src.backup(dst)
        if dst.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('LIFE_DATABASE_INTEGRITY_FAILED')
    finally:src.close();dst.close()

def create_session(directory,*,profile_path,life_db,source_db=None,source_device='emulator',life_subject=None,
                   lease_path,seconds=3600,smoke=False,previous_hour=None,app_scope=None):
    """Called by the human start command, never a candidate/approval issuer.

    First use copies an explicit historical life DB once. Later sessions use the
    SAME canonical DB, preserving memories, interests, wakes and request budgets.
    """
    from .config import Config
    from .longrun import secure_directory
    from .longrun_state import SessionLedger,SoakPolicy,canonical,sha
    from .life_subject import migrate_subject
    from .storage import Store
    from .life_storage import enable_life
    if (smoke and seconds!=180) or (not smoke and seconds not in {3600,86400}):raise ValueError('USE_SMOKE_180S_OR_1H_OR_24H')
    directory=Path(directory).resolve();life_db=Path(life_db).resolve();lease_path=Path(lease_path).resolve()
    if directory.exists():raise FileExistsError('Use a new session directory; old stops and evidence are immutable')
    if not lease_path.is_file():raise FileNotFoundError('Existing shared lease coordinator required')
    profile=PhysicalDeviceProfile.load(profile_path)
    cfg=Config.from_env()
    if not smoke:cfg.require_api()
    if life_db.exists() and source_db is not None:raise ValueError('Canonical life exists; omit source-db, never recopy budgets')
    if not life_db.exists() and (source_db is None or not Path(source_db).is_file()):raise FileNotFoundError('First start requires an existing source-db')
    if app_scope not in {None,'installed_launchable'}:raise ValueError('UNKNOWN_APP_SCOPE')
    if seconds==86400:verify_one_hour(previous_hour,life_db,profile.identity_sha256,app_scope=app_scope)
    # Setup holds the same writer/device locks as workers. It cannot race another
    # Agent's life migration or mutate a busy device coordinator's lease owner.
    leases=Store(lease_path);owner=str(uuid.uuid4());held=[];store=None;ledger=None
    try:
        for key in ('autonomous-api-writer','real'):
            if not leases.acquire(key,owner,120):raise PermissionError('DEVICE_OR_LIFE_BUSY')
            held.append(key)
        secure_directory(directory)
        if not life_db.exists():
            if not life_db.parent.exists():secure_directory(life_db.parent)
            _backup(source_db,life_db)
            if os.name=='nt':
                # The canonical life DB lives in a private directory just like
                # all session evidence; the launcher uses data/dedicated-life/.
                import subprocess
                from getpass import getuser
                r=subprocess.run(['icacls',str(life_db),'/inheritance:r','/grant:r',getuser()+':F'],capture_output=True,timeout=15)
                if r.returncode:raise PermissionError('LIFE_DATABASE_ACL_FAILED')
            else:life_db.chmod(0o600)
        backup=directory/'life-before.sqlite3';_backup(life_db,backup)
        source_sha=sha(backup)
        # Enable consent on a session-local copy, never edit the historical profile.
        session_profile=directory/'device-profile.private.json'
        session_profile.write_text(json.dumps(replace(profile,read_only_authorized=True).wire(),ensure_ascii=False),'utf8')
        manifest=directory/('app-scope.json' if app_scope else 'settings-scope.json')
        manifest.write_text(json.dumps({'schema':'physical-app-capabilities/1','enabled':True,
            'apps':[] if app_scope else [{'package':SETTINGS}],**({'app_scope':app_scope} if app_scope else {})}),'utf8')
        config={'version':'0.2.3','mode':'api','execution_mode':MODE,
            'source_kind':'live_device_manual_test' if smoke else 'live_api_natural_occurrence',
            'serial':'profile-bound','device':'physical:'+profile.identity_sha256,'session_directory':str(directory),
            'lease_path':str(lease_path),'agent_db':str(life_db),'allowed_apps':[] if app_scope else [SETTINGS],
            'private_evidence':False,'private_acl_restricted':True,'policy':asdict(SoakPolicy()),
            'api_base':cfg.api_base,'gui_api_base':cfg.gui_api_base,'brain_model':cfg.brain_model,'gui_model':cfg.gui_model,
            'persona_path':str((cfg.persona_path or Path('persona.life.example.md')).resolve()),
            'approved_formal_preflight_required':False,'test_controller':'settings_smoke' if smoke else None}
        if app_scope:config['app_scope']=app_scope
        ledger=SessionLedger(directory/'soak-control.sqlite3');value=ledger.create(config,seconds)
        # Until binding is complete, no independently invoked worker can run.
        value.update(state='failed',reason='DEDICATED_SETUP_INCOMPLETE');ledger.save(value)
        store=Store(life_db);enable_life(store)
        tables={r[0] for r in store.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        old=(store.conn.execute('SELECT life_subject_id FROM life_subjects WHERE legacy_source=?',(source_device,)).fetchone() if 'life_subjects' in tables else None)
        subject=life_subject or (old[0] if old else 'digital-life-main')
        binding=LifeBinding(subject,config['device'],profile.identity_sha256,value['session_id'])
        migrated=(store.conn.execute('SELECT source_sha256,source_device FROM life_subject_migrations WHERE life_subject_id=?',(subject,)).fetchone() if 'life_subject_migrations' in tables else None)
        if migrated and migrated[1]!=source_device:raise ValueError('EXPLICIT_SOURCE_DEVICE_MISMATCH')
        migrate_subject(store,binding,source_device=source_device,source_sha256=migrated[0] if migrated else source_sha)
        unknown=store.conn.execute('SELECT a.payload FROM action_attempts a').fetchall()
        if any(json.loads(r[0]).get('execution_status') in {'dispatching','outcome_unknown','uncertain_after_transport_error'} for r in unknown) or store.conn.execute("SELECT 1 FROM task_runs WHERE status='needs_review' LIMIT 1").fetchone():
            raise PermissionError('UNKNOWN_DISPATCH_NEEDS_REVIEW')
        if not smoke and not app_scope and not successful_smoke(store,config['device'],profile.identity_sha256):
            raise ValueError('Run the manual Settings smoke once before starting autonomous GUI work')
        from .life_storage import control
        control(store,config['device'],'running','Explicit human start of a new finite dedicated session; prior session stops remain intact')
        config['physical_execution']={'profile_path':str(session_profile),'manifest_path':str(manifest),'approval_path':None,
            'life_binding':asdict(binding),'control_path':str(ledger.path),'profile_sha256':sha(session_profile),'manifest_sha256':sha(manifest)}
        validate_config(config)
        value.update(config=config,fingerprint=hashlib.sha256(canonical(config).encode()).hexdigest(),state='prepared',reason='MANUAL_TEST_SESSION')
        ledger.save(value);ledger.event('dedicated_manual_start',{'execution_mode':MODE,'source_backup_sha256':source_sha,
            'per_action_approval':False,'helper_required':False,'old_actions_replayed':0})
        return value
    finally:
        if store:store.close()
        if ledger:ledger.close()
        for key in held:leases.release(key,owner)
        leases.close()

def successful_smoke(store,device,identity):
    for r in store.conn.execute("SELECT payload FROM events WHERE device=? AND event_type='dedicated_settings_smoke' ORDER BY id DESC",(device,)):
        p=json.loads(r[0])
        if p.get('status')!='succeeded' or p.get('identity_sha256')!=identity or p.get('gui_inputs')!=1 or p.get('independent_page_change')is not True:continue
        run=store.conn.execute('SELECT status,run_kind FROM task_runs WHERE task_run_id=?',(p.get('task_run_id'),)).fetchone()
        attempts=store.conn.execute('SELECT payload FROM action_attempts WHERE task_run_id=?',(p.get('task_run_id'),)).fetchall()
        executed=[json.loads(a[0]) for a in attempts if json.loads(a[0]).get('execution_status')=='executed']
        if run and tuple(run)==('succeeded','manual_targeted') and len(executed)==1:return True
    return False

def verify_one_hour(directory,life_db,identity,*,app_scope=None):
    if directory is None:raise ValueError('24h requires --after-one-hour pointing to the completed 1h session')
    from .longrun_state import SessionLedger
    status=SessionLedger.read_status(Path(directory)/'soak-control.sqlite3')
    cfg=status.get('config',{})
    if (status.get('state')!='completed_duration' or status.get('duration_seconds')!=3600 or
            cfg.get('execution_mode')!=MODE or cfg.get('agent_db')!=str(life_db) or cfg.get('device')!='physical:'+identity or
            cfg.get('app_scope')!=app_scope or
            status.get('requires_manual_review') or status.get('active_pid')):
        raise ValueError('A safely completed same-device 1h session is required; 24h never auto-continues')

async def run_manual_smoke(directory):
    from .longrun import run_worker,LongRunSoakController
    from .longrun_state import SessionLedger,SupervisorLock
    # Reuse the existing owned-worker stop watcher and all audit/lease components,
    # running one manual controller in the user's foreground process, without API.
    with SupervisorLock(Path(directory)/'.supervisor.lock'):
        ledger=SessionLedger(Path(directory)/'soak-control.sqlite3')
        try:
            status=ledger.status(worker_alive=False)
            if not status['safety_admission_allowed']:raise PermissionError(status['admission_reason_code'])
            ledger.start();ledger.update(active_pid=os.getpid())
            try:await run_worker(directory,os.getppid())
            except BaseException:
                ledger.tick('failed',reason='MANUAL_SMOKE_ABORTED',allow_completion=False)
                raise
            finally:ledger.update(active_pid=None)
            # Do not disguise the test ending early as completed_duration.
            health=LongRunSoakController(directory)
            try:
                h=health._health(quick=True)
                if h['needs_review'] or h.get('safety_review_required'):await health._review_stop(h)
                else:ledger.operator('paused');ledger.tick('paused',reason='MANUAL_SMOKE_FINISHED',allow_completion=False)
            finally:health.close()
            result=ledger.status(worker_alive=False)
            from .storage import Store
            s=Store.open_readonly(Path(result['config']['agent_db']))
            try:
                row=s.conn.execute("SELECT payload FROM events WHERE event_type='dedicated_settings_smoke' AND device=? ORDER BY id DESC LIMIT 1",(result['config']['device'],)).fetchone()
                result['settings_smoke']=json.loads(row[0]) if row else {'status':'NOT_OBSERVED'}
            finally:s.close()
            return result
        finally:ledger.close()

def main(argv=None):
    from .longrun import LongRunSoakController,control_session
    from .longrun_state import SessionLedger
    p=argparse.ArgumentParser(description='Manually start/stop a finite dedicated Find X6 test. No per-action approval.')
    p.add_argument('command',choices=('smoke','start','status','stop','pause'))
    p.add_argument('--session',required=True,type=Path)
    p.add_argument('--profile',type=Path)
    p.add_argument('--life-db',type=Path,default=Path('data/dedicated-life/agent.sqlite3'))
    p.add_argument('--source-db',type=Path)
    p.add_argument('--source-device',default='emulator')
    p.add_argument('--life-subject')
    p.add_argument('--lease-db',type=Path,default=Path('data/phone-life.sqlite3'))
    p.add_argument('--hours',type=int,choices=(1,24),default=1)
    p.add_argument('--after-one-hour',type=Path)
    a=p.parse_args(argv)
    if a.command=='status':result=SessionLedger.read_status(a.session/'soak-control.sqlite3')
    elif a.command in {'stop','pause'}:
        control_session(a.session,'emergency_stopped' if a.command=='stop' else 'paused');result={'operator_request':a.command}
    else:
        if not a.profile:p.error('--profile is required to start the bound device')
        create_session(a.session,profile_path=a.profile,life_db=a.life_db,source_db=a.source_db,
            source_device=a.source_device,life_subject=a.life_subject,lease_path=a.lease_db,
            seconds=180 if a.command=='smoke' else a.hours*3600,smoke=a.command=='smoke',previous_hour=a.after_one_hour,
            app_scope=None if a.command=='smoke' else 'installed_launchable')
        if a.command=='smoke':result=asyncio.run(run_manual_smoke(a.session))
        else:
            controller=LongRunSoakController(a.session)
            try:result=asyncio.run(controller.run())
            finally:controller.close()
    # Whitelist terminal output. Full UI/serial/config stays in private files.
    print(json.dumps({k:result.get(k) for k in ('state','effective_state','reason','requires_manual_review',
        'elapsed_seconds','operator_request','worker_launch_allowed','settings_smoke') if k in result},ensure_ascii=False))
    return 2 if result.get('state')=='failed' or ('settings_smoke' in result and result['settings_smoke'].get('status')!='succeeded') else 0

if __name__=='__main__':raise SystemExit(main())
