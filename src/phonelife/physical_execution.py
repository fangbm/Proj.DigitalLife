"""Default-deny physical execution core; no approval issuer or auto-launch CLI.

Native XML may lack safety attributes. Missing values remain unknown. A narrowly
bound navigation TAP can tolerate missing editable, never missing visibility.
This module does not deploy the API35 helper or infer missing attributes.
"""
from __future__ import annotations
import asyncio
import ctypes
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time
from .capabilities import CapabilityRegistry,GroundingError
from .device_profiles import PhysicalDeviceProfile,ProfileBoundReadOnlyPhone,DeviceProfileError,ReadOnlyAdb,digest,FIXED_COMMANDS
from .devices import DeviceTransportError
from .life_subject import LifeBinding
from .models import ScreenObservation
from .policy import ActionPolicy,PolicyDenied
from .semantic import UIAutomatorBackend,normalize,digest as semantic_digest
from .semantic_stability import trusted

def strict_json(raw):
    if len(raw)>2_000_000:raise ValueError('PHYSICAL_CONTRACT_TOO_LARGE')
    def unique(pairs):
        result={}
        for k,v in pairs:
            if k in result:raise ValueError('DUPLICATE_CONTRACT_KEY')
            result[k]=v
        return result
    value=json.loads(raw,object_pairs_hook=unique)
    if not isinstance(value,dict):raise ValueError('PHYSICAL_CONTRACT_OBJECT_REQUIRED')
    return value

def unprotect_approval(blob):
    """Separate DPAPI purpose; never reads/decrypts existing model credentials.

    Deliberately no issuer. A reviewed operator ceremony must create this artifact
    later. This is local-account authorization, not protection from that OS owner.
    """
    if os.name!='nt' or not 1<=len(blob)<=65536:raise PermissionError('OPERATOR_APPROVAL_UNAVAILABLE')
    class Blob(ctypes.Structure):_fields_=[('size',ctypes.c_uint32),('data',ctypes.POINTER(ctypes.c_ubyte))]
    buf=ctypes.create_string_buffer(blob);entropy=ctypes.create_string_buffer(b'Proj.DigitalLife physical execution approval v1')
    src=Blob(len(blob),ctypes.cast(buf,ctypes.POINTER(ctypes.c_ubyte)))
    ent=Blob(len(entropy.raw)-1,ctypes.cast(entropy,ctypes.POINTER(ctypes.c_ubyte)));out=Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(src),None,ctypes.byref(ent),None,None,1,ctypes.byref(out)):
        raise PermissionError('OPERATOR_APPROVAL_INVALID')
    try:return ctypes.string_at(out.data,out.size)
    finally:ctypes.windll.kernel32.LocalFree(out.data)

class PhysicalContract:
    def __init__(self,config,state):
        self.config=config;self.state=state
        spec=config['physical_execution']
        self.profile=PhysicalDeviceProfile.load(spec['profile_path'])
        if self.profile.identity['ro.build.version.sdk']!='36' or self.profile.identity['ro.build.version.release']!='16':
            raise PermissionError('PHYSICAL_COLOROS16_API36_REQUIRED')
        self.manifest=strict_json(Path(spec['manifest_path']).read_bytes())
        self.binding=LifeBinding(**spec['life_binding'])
        if self.binding.execution_device_id!=config['device'] or self.binding.device_session_id!=state['session_id']:
            raise PermissionError('PHYSICAL_SESSION_BINDING_MISMATCH')
        if self.binding.execution_device_id!='physical:'+self.profile.identity_sha256:
            raise PermissionError('PHYSICAL_EXECUTION_DEVICE_MISMATCH')
        if self.binding.device_profile_id!=self.profile.identity_sha256:raise PermissionError('PHYSICAL_PROFILE_BINDING_MISMATCH')
        if self.manifest.get('schema')!='physical-app-capabilities/1' or type(self.manifest.get('enabled'))is not bool:
            raise PermissionError('INVALID_PHYSICAL_MANIFEST')
        self.grant=None
    def authorize(self):
        if not self.profile.read_only_authorized or self.manifest.get('enabled')is not True:
            raise PermissionError('PHYSICAL_EXECUTION_DISABLED')
        path=self.config['physical_execution'].get('approval_path')
        if not path:raise PermissionError('PHYSICAL_OPERATOR_APPROVAL_REQUIRED')
        with Path(path).open('rb') as stream:blob=stream.read(65537)
        grant=strict_json(unprotect_approval(blob))
        expected={'schema':'physical-execution-approval/1','session_id':self.state['session_id'],
            'config_fingerprint':self.state['fingerprint'],'identity_sha256':self.profile.identity_sha256,
            'manifest_sha256':digest(self.manifest),'life_subject_id':self.binding.life_subject_id,
            'duration_seconds':self.state['duration_seconds'],'execute_gui':True}
        if set(grant)!=set(expected)|{'expires_at','operator_review_reference'} or any(grant[k]!=v or type(grant[k])is not type(v) for k,v in expected.items()):
            raise PermissionError('PHYSICAL_OPERATOR_APPROVAL_MISMATCH')
        if (type(grant['expires_at'])not in {int,float} or not time.time()<grant['expires_at']<=time.time()+86520
                or not isinstance(grant['operator_review_reference'],str) or not grant['operator_review_reference'].strip()):
            raise PermissionError('PHYSICAL_OPERATOR_APPROVAL_EXPIRED')
        self.grant=grant
        return self

def visible_windows_safe(text,package):
    """Require complete explicit native visibility/type evidence, never infer absence.

    Tested synthetic dumpsys grammar only. Unknown ColorOS shapes reject. System
    bars need exact native type; all other visible windows must be the one app.
    """
    sections=re.split(r'(?=\bWindow #\d+ Window\{)',text)
    sections=[s for s in sections if re.match(r'Window #\d+ Window\{',s)]
    if not sections:return False
    apps=0
    for s in sections:
        vis=re.search(r'mViewVisibility=(0x[0-9a-fA-F]+)',s)
        surface=re.search(r'mHasSurface=(true|false)',s)
        if not vis or not surface:return False
        if int(vis[1],16)!=0 or surface[1]=='false':continue
        typ=re.search(r'\bty=(\d+)\b',s);pkg=re.search(r'\bpackage=([\w.]+)\b',s)
        if not typ or not pkg:return False
        if int(typ[1]) in {2000,2019} and pkg[1]=='com.android.systemui':continue
        if int(typ[1])!=1 or pkg[1]!=package:return False
        apps+=1
    return apps==1

def page_key(s):
    # Full exact native structure/properties. No API35 Uptime dynamic exception.
    return digest({'package':s.foreground_package,'activity':s.foreground_activity,'rotation':s.orientation,
        'size':[s.screen_width,s.screen_height],'controls':[c.identity() for c in s.controls]})

class PhysicalRegistry(CapabilityRegistry):
    version='physical-capabilities/1'
    routes=CapabilityRegistry.routes | frozenset({'显示与亮度'})
    def __init__(self,contract):
        super().__init__();self.contract=contract
    def evidence_for_action(self,s,action):
        if trusted(s):return True
        # Action-specific eligibility, not a mutation of the observation's global
        # trust or nullable attributes. Window/input/lease/approval checks still run.
        return bool(action=='tap' and s.provider=='coloros16_native_xml/1' and
            s.evidence_quality=='partial' and s.failure_reason=='INCOMPLETE_ATTRIBUTES_OR_OVERLAY' and
            not s.overlays and not s.stability and s.controls and
            all(type(c.visible)is bool and c.package=='com.android.settings' for c in s.controls) and
            s.tree_sha256==semantic_digest([c.identity() for c in s.controls]))
    def navigation_node_allowed(self,node):
        if node.editable is False:return True
        # Unknown stays unknown. This only permits navigation through an exact
        # approved page/row; it grants no text or editing capability. Custom
        # widgets, editable=True and independently clickable title children deny.
        return node.editable is None and (node.class_name in {
            'android.widget.LinearLayout','android.widget.RelativeLayout'} or
            node.class_name in {'android.widget.TextView','android.widget.ImageView'} and node.clickable is False)
    def page(self,s,action=None):
        if not self.evidence_for_action(s,action) or s.provider!='coloros16_native_xml/1' or s.stability:
            raise PolicyDenied('PHYSICAL_TRUSTED_NATIVE_SEMANTICS_REQUIRED')
        self.contract.authorize()
        packages=self.contract.manifest.get('apps',[])
        app=next((a for a in packages if a.get('package')==s.foreground_package),None)
        if not app or s.foreground_package!='com.android.settings':raise PolicyDenied('PHYSICAL_APP_NOT_APPROVED')
        page=next((p for p in app.get('pages',[]) if p.get('page_sha256')==page_key(s)),None)
        if not page:raise PolicyDenied('PHYSICAL_PAGE_NOT_APPROVED')
        return page
    def safe_target(self,s,target,scroll=False):
        page=self.page(s,'swipe' if scroll else 'tap')
        if not any(c==target for c in s.controls):raise PolicyDenied('PHYSICAL_TARGET_NOT_IN_OBSERVATION')
        a,b,c,d=target.bounds
        if not 0<=a<c<=s.screen_width or not 0<=b<d<=s.screen_height:
            raise PolicyDenied('PHYSICAL_TARGET_BOUNDS_INVALID')
        nodes={c.control_id:c for c in s.controls}
        ancestor=target;seen=set()
        while ancestor:
            if ancestor.control_id in seen or ancestor.visible is not True:
                raise PolicyDenied('PHYSICAL_TARGET_ANCESTRY_NOT_VISIBLE')
            seen.add(ancestor.control_id)
            if ancestor.parent is not None and ancestor.parent not in nodes:
                raise PolicyDenied('PHYSICAL_TARGET_ANCESTRY_UNBOUND')
            ancestor=nodes.get(ancestor.parent)
        if not scroll and any(n.visible is not True or not
                a<=n.bounds[0]<n.bounds[2]<=c or not b<=n.bounds[1]<n.bounds[3]<=d
                for n in self.subtree(s,target)):
            raise PolicyDenied('PHYSICAL_ROW_DESCENDANT_NOT_VISIBLE_OR_CONTAINED')
        if not any(r.get('resource_id')==target.resource_id and r.get('class_name')==target.class_name
            and r.get('action')==('swipe' if scroll else 'tap') for r in page.get('controls',[])):
            raise PolicyDenied('PHYSICAL_CONTROL_NOT_APPROVED')
        super().safe_target(s,target,scroll)
        if scroll:
            for node in self.subtree(s,target):
                if (node.package!=s.foreground_package or any(getattr(node,k)is not False for k in ('editable','password','checkable','long_clickable'))
                        or any(c in node.class_name for c in self.state_classes)):
                    raise PolicyDenied('PHYSICAL_SCROLL_REGION_CONTAINS_UNSAFE_CONTROLS')
    def ground(self,candidate,s):
        kind=candidate.get('type')
        page=self.page(s,'tap' if kind in {'tap','tap_control'} else 'swipe' if kind in {'swipe','scroll_region'} else kind)
        if candidate.get('type') in {'home','launch','open_allowed_app','text'}:raise PolicyDenied('PHYSICAL_ACTION_NOT_REGISTERED')
        if candidate.get('type') in {'back','navigate_back'} and page.get('back_allowed')is not True:raise PolicyDenied('PHYSICAL_BACK_NOT_APPROVED')
        return super().ground(candidate,s)
    def revalidate(self,binding,original,current):
        action=binding.get('actual_action',{}).get('type')
        self.page(original,action);self.page(current,action)
        if page_key(original)!=page_key(current) or original.content_sha256!=current.content_sha256:raise GroundingError('PHYSICAL_SCENE_CHANGED','Exact ColorOS scene unchanged')
        if action=='tap' and (not trusted(original) or not trusted(current)):
            if binding.get('target') is None:raise GroundingError('UNKNOWN_CONTROL','Bound navigation row required')
            matches=[c for c in current.controls if c.identity()==binding['target']]
            if len(matches)!=1:raise GroundingError('CONTROL_CHANGED','Exactly one unchanged target required')
            self.safe_target(current,matches[0])
            return {**binding,'fresh_observation_id':current.observation_id,'fresh_screenshot_sha256':current.screenshot_sha256,
                'pre_dispatch_consistency':'verified','stability':{'mode':'exact_native_tap'}}
        return super().revalidate(binding,original,current)
    def recovery_action(self,*args):raise PolicyDenied('PHYSICAL_RECOVERY_INPUT_NOT_APPROVED')

class PhysicalActionPolicy(ActionPolicy):
    def __init__(self,contract):
        super().__init__('physical',frozenset({'com.android.settings'}));self.contract=contract
    def check(self,action,observation=None):
        self.contract.authorize()
        if action.type not in {'tap','swipe','back','finish'}:raise PolicyDenied('PHYSICAL_ACTION_NOT_REGISTERED')
        if action.type!='finish' and (observation is None or not action.observation_id):raise PolicyDenied('PHYSICAL_OBSERVATION_REQUIRED')
        super().check(action,observation)

class ColorOSSemanticProvider:
    async def collect(self,phone,obs):
        from .semantic import SemanticObservation
        try:
            # Native XML only. Two exact frames, no helper, no inferred properties.
            started=time.monotonic_ns()
            if not 0<=started-obs.captured_monotonic_ns<=2_000_000_000:
                raise ValueError('COLOROS_OBSERVATION_STALE_OR_FUTURE')
            raw=await UIAutomatorBackend().read(phone,obs)
            first_end=time.monotonic_ns()
            first=normalize(raw,obs,await phone.foreground(),'coloros16_native_xml/1')
            fresh=await phone.observe()
            second_start=time.monotonic_ns()
            second_raw=await UIAutomatorBackend().read(phone,fresh)
            second_end=time.monotonic_ns()
            second=normalize(second_raw,fresh,await phone.foreground(),'coloros16_native_xml/1')
            registry=PhysicalRegistry(None)
            if not registry.evidence_for_action(first,'tap') or not registry.evidence_for_action(second,'tap') or page_key(first)!=page_key(second) or first.content_sha256!=second.content_sha256:
                raise ValueError('COLOROS_SEMANTICS_NOT_EXACT_AND_COMPLETE')
            finished=time.monotonic_ns()
            if not (obs.captured_monotonic_ns<=started<=first_end<=fresh.captured_monotonic_ns<=second_start<=second_end<=finished
                    and finished-obs.captured_monotonic_ns<=2_000_000_000):
                raise ValueError('COLOROS_OBSERVATION_STALE_OR_INCONSISTENT')
            return replace(first,attempts=({'source':'host_monotonic_acquisition',
                'png_received_ns':obs.captured_monotonic_ns,'xml_started_ns':started,'xml_finished_ns':first_end,
                'second_png_received_ns':fresh.captured_monotonic_ns,'second_xml_started_ns':second_start,
                'second_xml_finished_ns':second_end,'validation_finished_ns':finished},))
        except Exception as e:
            code=str(e)
            return SemanticObservation(obs.observation_id,obs.sha256,obs.width,obs.height,None,'','',
                'coloros16_native_xml/1','',failure_reason=code if re.fullmatch('[A-Z][A-Z0-9_]{1,100}',code) else type(e).__name__)

class PhysicalPhone(ProfileBoundReadOnlyPhone):
    kind='real'
    def __init__(self,contract,transport=None):
        super().__init__(contract.profile,transport)
        self.contract=contract;self.capabilities=PhysicalRegistry(contract);self._permit=None;self._unknown=False
        self._dispatched=0
    async def _identity_online(self):
        matched=[x for x in await self.probe.transport.devices() if x['serial']==self.serial]
        if len(matched)!=1 or matched[0]['state']!='device':raise PolicyDenied('PHYSICAL_DEVICE_OFFLINE')
        self.profile.verify(self.serial,await self.probe.identity())
    async def _scene(self):
        safety=await self.probe.safety()
        windows=await self.probe.text(('shell','dumpsys','window','windows'))
        if safety['locked']is not False or safety['interactive']is not True or safety['orientation']is None:
            raise PolicyDenied('PHYSICAL_SCREEN_UNSAFE')
        if safety['permission_or_unknown_overlay'] or safety['foreground_package']!='com.android.settings' or not visible_windows_safe(windows,'com.android.settings'):
            raise PolicyDenied('PHYSICAL_WINDOW_NOT_VERIFIED')
        return safety
    async def observe(self):
        if self._unknown:raise PolicyDenied('PHYSICAL_UNKNOWN_DISPATCH_NEEDS_REVIEW')
        await self._identity_online();before=await self._scene()
        obs=await super().observe()
        await self._identity_online()
        if await self._scene()!=before:raise PolicyDenied('PHYSICAL_SCENE_CHANGED')
        return obs
    async def foreground(self):
        await self._identity_online();s=await self._scene()
        raw=await self.probe.text(('shell','dumpsys','window','windows'))
        focus=re.search(r'mCurrentFocus=.*?\s([\w.]+/[\w.$]+)',raw)
        if not focus:raise PolicyDenied('PHYSICAL_ACTIVITY_UNVERIFIED')
        return focus[1]
    async def describe(self):return 'Profile-bound dedicated physical Android; identity verified separately'
    async def hierarchy(self):
        await self._identity_online();await self._scene()
        raw=await self.probe.text(('exec-out','uiautomator','dump','/dev/tty'))
        start,end=raw.find('<?xml'),raw.rfind('</hierarchy>')
        if start<0 or end<0:raise PolicyDenied('COLOROS_XML_UNAVAILABLE')
        await self._identity_online();await self._scene()
        return raw[start:end+len('</hierarchy>')]
    async def read_fact(self,name):
        await self._identity_online();await self._scene()
        if name in {'model','android_version'}:
            return await self.probe.text(('shell','getprop',{'model':'ro.product.model','android_version':'ro.build.version.release'}[name]))
        if name=='battery_percent':
            raw=await self.probe.text(('shell','dumpsys','battery'));m=re.search(r'^\s*level:\s*(\d+)\s*$',raw,re.M)
            if m and 0<=int(m[1])<=100:return m[1]
        if name=='storage_free_kb':
            raw=await self.probe.text(('shell','df','-k','/data'));parts=raw.splitlines()[-1].split()
            if len(parts)>3 and parts[3].isdigit():return parts[3]
        raise PolicyDenied('PHYSICAL_FACT_UNAVAILABLE')
    def prepare_dispatch(self,action,semantic,binding,store,leases,run_id,owner,attempt_id,limit):
        if self._unknown:raise PolicyDenied('PHYSICAL_UNKNOWN_DISPATCH_NEEDS_REVIEW')
        self.contract.authorize();self.contract.binding.verify(store.conn)
        if type(limit)is not int or not 1<=limit<=self.contract.config['policy']['max_actions'] or self._dispatched>=limit:raise PolicyDenied('PHYSICAL_ACTION_BUDGET')
        self.capabilities.page(semantic,action.type)
        if binding.get('actual_action')!=action.wire():raise PolicyDenied('PHYSICAL_ACTION_BINDING_MISMATCH')
        key=getattr(leases,'lease_key',self.contract.binding.execution_device_id)
        row=leases.conn.execute('SELECT owner,expires_at FROM lease WHERE device=?',(key,)).fetchone()
        if not row or row[0]!=owner or row[1]<=time.time():raise PolicyDenied('PHYSICAL_DEVICE_LEASE_REQUIRED')
        from .longrun_state import SessionLedger
        status=SessionLedger.read_status(Path(self.contract.config['physical_execution']['control_path']))
        if not status.get('safety_admission_allowed') or status.get('requires_manual_review') or status.get('operator')!='running':raise PolicyDenied('PHYSICAL_CONTROL_STOPPED')
        self._permit=(action,semantic,binding,store,leases,run_id,owner,attempt_id,limit,time.monotonic())
    def _final_guard(self,store,leases,run_id,owner,attempt_id):
        from .longrun_state import SessionLedger,TERMINAL
        ledger=SessionLedger(Path(self.contract.config['physical_execution']['control_path']),readonly=True)
        try:
            v=ledger.load()
            if (v['state'] in TERMINAL or v['operator']!='running' or v.get('requires_manual_review') or
                v.get('worker_signal')=='stop' or v.get('active_pid')!=os.getpid() or ledger.remaining()<=0 or
                v['fingerprint']!=self.contract.state['fingerprint']):raise PolicyDenied('PHYSICAL_CONTROL_STOPPED')
        finally:ledger.close()
        for key in ('real','autonomous-api-writer','subject:'+self.contract.binding.life_subject_id):
            row=leases.conn.execute('SELECT owner,expires_at FROM lease WHERE device=?',(key,)).fetchone()
            if not row or row[0]!=owner or row[1]<=time.time():raise PolicyDenied('PHYSICAL_OWNED_LEASE_REQUIRED')
        row=store.conn.execute('SELECT a.payload,t.device,t.run_kind,t.status FROM action_attempts a JOIN task_runs t USING(task_run_id) WHERE a.attempt_id=? AND a.task_run_id=?',(attempt_id,run_id)).fetchone()
        if (not row or tuple(row)[1:]!=(self.contract.binding.execution_device_id,'autonomous','running') or
                json.loads(row[0]).get('execution_status')!='uncertain_after_transport_error'):
            raise PolicyDenied('PHYSICAL_PREWRITE_AUDIT_REQUIRED')
    async def _send_input(self,args):
        proc=await asyncio.create_subprocess_exec(self.probe.transport.executable,'-s',self.serial,'shell','input',*args,
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        try:
            _,_=await asyncio.wait_for(proc.communicate(),15)
            if proc.returncode:raise RuntimeError('PHYSICAL_INPUT_FAILED')
        except BaseException:
            if proc.returncode is None:proc.kill();await proc.wait()
            raise
    async def act(self,action):
        permit,self._permit=self._permit,None
        if not permit or permit[0]!=action or not 0<=time.monotonic()-permit[-1]<=2 or self._unknown:
            raise PolicyDenied('PHYSICAL_SINGLE_USE_DISPATCH_PERMIT_REQUIRED')
        _,semantic,binding,store,leases,run_id,owner,attempt_id,limit,_=permit
        try:
            self.contract.authorize();await self._identity_online();await self._scene()
            fresh=await self.observe();current=await ColorOSSemanticProvider().collect(self,fresh)
            self.capabilities.revalidate(binding,semantic,current)
            PhysicalActionPolicy(self.contract).check(action,replace(fresh,observation_id=action.observation_id))
            # Recheck lease/control/budget after all async reads, immediately before the only input call.
            self.prepare_dispatch(action,current,binding,store,leases,run_id,owner,attempt_id,limit);self._permit=None
            # Last transport identity sample follows semantic collection. Any await
            # can race hot-plug, so stop if it no longer matches before input.
            await self._identity_online()
            self._final_guard(store,leases,run_id,owner,attempt_id)
            # The original deadline survives all awaited reads and the second
            # prepare_dispatch. Neither WMS reads nor permit renewal refresh XML.
            if not 0<=time.monotonic()-permit[-1]<=2:
                raise PolicyDenied('PHYSICAL_FRESH_OBSERVATION_DEADLINE_EXCEEDED')
        except Exception:
            raise PolicyDenied('PHYSICAL_PRE_DISPATCH_RECHECK_FAILED_NO_INPUT') from None
        args=({'tap':lambda:('tap',str(action.x),str(action.y)),
            'swipe':lambda:('swipe',str(action.x),str(action.y),str(action.x2),str(action.y2),str(action.duration_ms)),
            'back':lambda:('keyevent','4')}.get(action.type))
        if args is None:raise PolicyDenied('PHYSICAL_ACTION_NOT_REGISTERED')
        self._dispatched+=1
        try:
            await self._send_input(args())
            await self._identity_online();await self._scene()
            # Do not confirm a dispatch on ADB exit code alone. If USB/observation
            # fails before fresh visual+semantic evidence, retain the prewrite as
            # unknown and require review; reconnect must not silently resume it.
            post=await self.observe()
            post_semantic=await ColorOSSemanticProvider().collect(self,post)
            self.capabilities.page(post_semantic,action.type)
        except BaseException as e:
            self._unknown=True
            # Make a concurrent Status see transport uncertainty even while this
            # owned worker is still alive. The executor loop repeats the audit;
            # a failed update never erases the conservative pre-dispatch record.
            try:store.update_attempt(attempt_id,transport_error='PHYSICAL_ADB_OUTCOME_UNKNOWN_NO_RESEND')
            except Exception:pass
            if isinstance(e,asyncio.CancelledError):raise
            raise DeviceTransportError('PHYSICAL_ADB_OUTCOME_UNKNOWN_NO_RESEND') from None
        return 'physical_input_confirmed_transport; goal remains independently unverified'

class LeaseNamespace:
    """All physical executors share legacy real-device coordinator ownership."""
    lease_key='real'
    def __init__(self,store,device):self.store=store;self.device=device;self.conn=store.conn
    def _key(self,device):
        if device!=self.device:raise ValueError('LEASE_DEVICE_BINDING_MISMATCH')
        return self.lease_key
    def acquire(self,device,*a):return self.store.acquire(self._key(device),*a)
    def renew(self,device,*a):return self.store.renew(self._key(device),*a)
    def release(self,device,*a):return self.store.release(self._key(device),*a)

def physical_session_admission(value,base):
    """Used by Status, Control, supervisor and worker; never creates an API client.

    Disabled/unapproved contracts return before ADB. A reviewed grant enables only
    bounded fixed read checks here, never inputs. No stale persisted online flag.
    """
    if not value.get('config',{}).get('physical_execution'):return base
    verdict={**base,'physical_device':{'bound':True,'online':None,'probe_status':'not_checked','identity_verified':False,'observation_allowed':False,'execution_allowed':False,'user_authorized':False},
        'worker_launch_allowed':False,'resume_allowed':False,'effective_resume_allowed':False,'initial_start_allowed_without_approval':False}
    if not base.get('safety_admission_allowed'):return verdict
    reason='PHYSICAL_ADMISSION_UNVERIFIABLE'
    try:
        contract=PhysicalContract(value['config'],value).authorize();cfg=value['config'];p=contract.profile
        from .longrun_state import Clock
        if value['boot_id']!=Clock.boot_id() or time.monotonic()<value['last_monotonic']:
            raise PermissionError('MONOTONIC_CLOCK_DISCONTINUITY')
        c=sqlite3.connect(Path(cfg['agent_db']).resolve().as_uri()+'?mode=ro',uri=True)
        try:contract.binding.verify(c)
        finally:c.close()
        evidence=physical_read_check(contract)
        verdict['physical_device']=evidence
        if not all(evidence[k] for k in ['online','identity_verified','observation_allowed','execution_allowed','user_authorized']):raise PermissionError('PHYSICAL_DEVICE_UNAVAILABLE_OR_UNSAFE')
        return {**base,'physical_device':evidence}
    except (OSError,ValueError,TypeError,KeyError,AttributeError,PermissionError,sqlite3.Error,DeviceProfileError) as e:
        code=str(e)
        reason=code if isinstance(e,(PermissionError,DeviceProfileError)) and re.fullmatch('[A-Z][A-Z0-9_]{1,100}',code) else 'PHYSICAL_CONTRACT_UNVERIFIABLE'
    return {**verdict,'safety_admission_allowed':False,'admission_reason_code':reason,
        'effective_state':base['effective_state'] if base['effective_state'] in {'failed','emergency_stopped','completed_duration'} else 'blocked'}

def physical_read_check(contract):
    """Fixed, bounded read-only native checks. Unknown window grammar denies.

    Kept synchronous for the existing synchronous Status API. Total subprocess
    timeout <=8s; physical inputs never run here. No helper installation.
    """
    p=contract.profile;deadline=time.monotonic()+8
    def run(args):
        if args not in FIXED_COMMANDS:raise PermissionError('READONLY_COMMAND_NOT_ALLOWLISTED')
        remaining=deadline-time.monotonic()
        if remaining<=0:raise PermissionError('PHYSICAL_READ_CHECK_TIMEOUT')
        try:r=subprocess.run(['adb','-s',p.serial,*args],capture_output=True,timeout=min(1.5,remaining))
        except (OSError,subprocess.TimeoutExpired):raise PermissionError('PHYSICAL_DEVICE_UNAVAILABLE') from None
        if r.returncode or len(r.stdout)>2_000_000:raise PermissionError('PHYSICAL_READ_CHECK_FAILED')
        return r.stdout.decode('utf8','replace').strip()
    if run(('get-state',))!='device':raise PermissionError('PHYSICAL_DEVICE_OFFLINE')
    identity={k:run(('shell','getprop',k)) for k in p.identity};p.verify(p.serial,identity)
    from .device_profiles import parse_safety
    safety=parse_safety(run(('shell','dumpsys','power')),run(('shell','dumpsys','window','policy')),
        run(('shell','dumpsys','window','windows')),run(('shell','dumpsys','input')))
    windows=run(('shell','dumpsys','window','windows'))
    packages=run(('shell','pm','list','packages','--show-versioncode'))
    app=next((a for a in contract.manifest.get('apps',[]) if a.get('package')=='com.android.settings'),{})
    version_ok=bool(app.get('version_code')) and f"package:com.android.settings versionCode:{app['version_code']}" in packages.splitlines()
    safe=safety['locked']is False and safety['interactive']is True and safety['orientation']is not None and not safety['permission_or_unknown_overlay'] and safety['foreground_package']=='com.android.settings' and visible_windows_safe(windows,'com.android.settings')
    return {'bound':True,'online':True,'identity_verified':True,'observation_allowed':safe,'execution_allowed':bool(safe and version_ok and app.get('pages')),'user_authorized':True}

def prepare_physical_session(directory,*,source_copy,source_sha256,source_device,life_subject_id,
                             profile_path,manifest_path,lease_path,seconds,approval_path=None):
    """Future reviewed preparation API, deliberately not exposed as an enable CLI.

    A new finite unapproved session only. Requires an explicitly selected consistent
    source copy and existing shared coordinator; no production migrations/discovery.
    It cannot mint the DPAPI execution approval, start a worker or contact a model.
    This round invokes it only with isolated synthetic databases in offline tests.
    """
    from .longrun import prepare_session
    from .longrun_state import SessionLedger,sha,canonical
    from .life_subject import migrate_subject
    from .storage import Store
    source_copy=Path(source_copy).resolve();profile_path=Path(profile_path).resolve();manifest_path=Path(manifest_path).resolve()
    lease_path=Path(lease_path).resolve()
    if sha(source_copy)!=source_sha256 or (source_copy.with_name(source_copy.name+'-wal').exists() and source_copy.with_name(source_copy.name+'-wal').stat().st_size):
        raise ValueError('CONSISTENT_SOURCE_COPY_REQUIRED')
    if not lease_path.is_file():raise FileNotFoundError('Existing shared coordinator required')
    profile=PhysicalDeviceProfile.load(profile_path);manifest=strict_json(manifest_path.read_bytes())
    if manifest.get('schema')!='physical-app-capabilities/1':raise ValueError('INVALID_PHYSICAL_MANIFEST')
    value=prepare_session(directory,mode='mock',seconds=seconds,lease_path=lease_path)
    directory=Path(directory).resolve();ledger=SessionLedger(directory/'soak-control.sqlite3')
    binding=LifeBinding(life_subject_id,'physical:'+profile.identity_sha256,profile.identity_sha256,value['session_id'])
    try:
        # No partially copied/migrated proposal is launchable after a crash.
        value.update(state='failed',reason='PHYSICAL_PREPARATION_INCOMPLETE');ledger.save(value)
        source=sqlite3.connect(source_copy.as_uri()+'?mode=ro',uri=True)
        try:
            target=sqlite3.connect(value['config']['agent_db'])
            try:source.backup(target)
            finally:target.close()
        finally:source.close()
        store=Store(Path(value['config']['agent_db']))
        try:migrate_subject(store,binding,source_device=source_device,source_sha256=source_sha256)
        finally:store.close()
        config=value['config'];config.update(mode='api',source_kind='live_api_preflight' if seconds<=300 else 'live_api_natural_occurrence',
            serial='profile-bound',device=binding.execution_device_id,approved_formal_preflight_required=True,
            physical_execution={'profile_path':str(profile_path),'profile_sha256':sha(profile_path),
                'manifest_path':str(manifest_path),'manifest_sha256':sha(manifest_path),
                'approval_path':str(Path(approval_path).resolve()) if approval_path else None,
                'life_binding':binding.__dict__,'control_path':str(ledger.path),'source_copy_sha256':source_sha256})
        value.update(config=config,fingerprint=hashlib.sha256(canonical(config).encode()).hexdigest(),state='awaiting_approval')
        ledger.save(value);ledger.event('physical_session_prepared',{'source_copy_sha256':source_sha256,'approval_issued':False})
        return ledger.status(worker_alive=False)
    finally:ledger.close()
