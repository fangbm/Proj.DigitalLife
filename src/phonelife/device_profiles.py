"""Opt-in physical-device diagnostics. No physical GUI/autonomous execution is enabled.

This module never relaxes ActionPolicy, deploys a helper, grants permissions, or
starts a worker. A device profile is an operator-owned identity binding, not a
model-provided permission and not hardware attestation.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
import re
import time
from types import MappingProxyType
import xml.etree.ElementTree as ET

from .models import ScreenObservation
from .policy import PolicyDenied


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


class DeviceProfileError(ValueError):
    def __init__(self,code):
        self.code=code
        super().__init__(code)  # Never expose raw serial, shell output or UI text in errors.


def check_serial(serial,physical=False):
    if not isinstance(serial,str) or serial.startswith('-') or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',serial):
        raise DeviceProfileError('EXPLICIT_DEVICE_SERIAL_REQUIRED')
    if physical and (serial.startswith('emulator-') or '_adb-' in serial or '.' in serial):
        raise DeviceProfileError('PHYSICAL_USB_SERIAL_REQUIRED')


IDENTITY_PROPERTIES=('ro.product.manufacturer','ro.product.brand','ro.product.model',
    'ro.product.device','ro.build.version.release','ro.build.version.sdk','ro.build.fingerprint','ro.kernel.qemu')
COLOROS_PROPERTIES=('ro.build.version.oplusrom','ro.build.version.opporom','ro.rom.version')
GETPROPS=tuple(('shell','getprop',p) for p in IDENTITY_PROPERTIES+COLOROS_PROPERTIES)
FIXED_COMMANDS=frozenset(GETPROPS+(
    ('get-state',),('shell','wm','size'),('shell','wm','density'),
    ('shell','dumpsys','input'),('shell','dumpsys','power'),('shell','dumpsys','window','policy'),
    ('shell','dumpsys','window','windows'),('shell','dumpsys','battery'),('shell','df','-k','/data'),
    ('shell','dumpsys','window','displays'),
    ('shell','pm','list','packages','--show-versioncode'),('exec-out','screencap','-p'),
    ('exec-out','uiautomator','dump','/dev/tty')))


@dataclass(frozen=True)
class EmulatorDeviceProfile:
    serial:str
    sdk:str='35'

    def __post_init__(self):
        check_serial(self.serial)
        if not self.serial.startswith('emulator-'):raise DeviceProfileError('EMULATOR_SERIAL_REQUIRED')

    def verify(self,serial,identity):
        if serial!=self.serial or identity.get('ro.kernel.qemu')!='1' or identity.get('ro.build.version.sdk')!=self.sdk:
            raise DeviceProfileError('EMULATOR_IDENTITY_MISMATCH')


@dataclass(frozen=True)
class PhysicalDeviceProfile:
    serial:str
    identity:dict
    read_only_authorized:bool=False
    input_enabled:bool=False
    schema:str='physical-device-profile/1'

    def __post_init__(self):
        check_serial(self.serial,physical=True)
        if type(self.read_only_authorized)is not bool or self.input_enabled is not False or self.schema!='physical-device-profile/1':
            raise DeviceProfileError('PHYSICAL_INPUT_NOT_AUTHORIZED')
        if not isinstance(self.identity,Mapping) or set(self.identity)!=set(IDENTITY_PROPERTIES):raise DeviceProfileError('INCOMPLETE_DEVICE_IDENTITY')
        if any(not isinstance(v,str) or (not v.strip() and k!='ro.kernel.qemu') or len(v)>1024 for k,v in self.identity.items()):
            raise DeviceProfileError('INCOMPLETE_DEVICE_IDENTITY')
        # Physical ROMs may not define ro.kernel.qemu. Absence alone grants no trust:
        # exact USB serial, manufacturer/model/device/build allowlist still applies.
        if self.identity['ro.kernel.qemu'] not in {'','0','false'}:raise DeviceProfileError('PHYSICAL_QEMU_MISMATCH')
        if self.identity['ro.product.manufacturer'].casefold()!='oppo' or self.identity['ro.product.model']!='PGFM10':
            raise DeviceProfileError('FIND_X6_IDENTITY_MISMATCH')
        if not self.identity['ro.build.version.sdk'].isdigit():raise DeviceProfileError('INVALID_ANDROID_API_LEVEL')
        object.__setattr__(self,'identity',MappingProxyType(dict(self.identity)))

    @property
    def identity_sha256(self):return digest({'serial':self.serial,'identity':dict(self.identity)})

    def wire(self):
        return {'serial':self.serial,'identity':dict(self.identity),'read_only_authorized':self.read_only_authorized,
            'input_enabled':self.input_enabled,'schema':self.schema}

    @property
    def lease_key(self):
        # Preserve the legacy real-device coordinator namespace. A future per-device
        # lease-key migration must update every cooperating executor together.
        return 'real'

    def verify(self,serial,identity):
        if not self.read_only_authorized:raise DeviceProfileError('READONLY_DEVICE_CONSENT_REQUIRED')
        if serial!=self.serial or identity!=self.identity:raise DeviceProfileError('PHYSICAL_IDENTITY_MISMATCH')

    @classmethod
    def load(cls,path):
        def unique(pairs):
            result={}
            for k,v in pairs:
                if k in result:raise DeviceProfileError('DUPLICATE_PROFILE_KEY')
                result[k]=v
            return result
        data=Path(path).read_bytes()
        if len(data)>32768:raise DeviceProfileError('PROFILE_TOO_LARGE')
        v=json.loads(data,object_pairs_hook=unique)
        if not isinstance(v,dict) or set(v)!={'serial','identity','read_only_authorized','input_enabled','schema'}:
            raise DeviceProfileError('INVALID_PROFILE_FIELDS')
        return cls(**v)


class ReadOnlyAdb:
    """Only enumerated metadata/capture commands; always an explicit -s target."""
    def __init__(self,executable='adb'):
        self.executable=executable
        self.journal=[]

    async def _run(self,argv,limit=2_000_000):
        started=time.monotonic_ns()
        proc=await asyncio.create_subprocess_exec(self.executable,*argv,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        async def bounded(stream,maximum):
            chunks=[];total=0
            while True:
                part=await stream.read(min(65536,maximum-total+1))
                if not part:return b''.join(chunks)
                chunks.append(part);total+=len(part)
                if total>maximum:raise DeviceProfileError('READONLY_OUTPUT_LIMIT')
        try:
            output,errors,_=await asyncio.wait_for(asyncio.gather(bounded(proc.stdout,limit),bounded(proc.stderr,4096),proc.wait()),15)
            if proc.returncode:raise DeviceProfileError('READONLY_ADB_FAILED')
            return output
        except BaseException:
            if proc.returncode is None:proc.kill()
            await proc.wait()
            raise
        finally:
            self.journal.append({'command_sha256':digest(argv),'started_monotonic_ns':started,
                'finished_monotonic_ns':time.monotonic_ns(),'returncode':proc.returncode,'input_dispatched':False})

    async def devices(self):
        text=(await self._run(('devices','-l'))).decode('utf8','replace')
        rows=[]
        for line in text.splitlines():
            if not line.strip() or line.startswith('List of devices') or line.startswith('*'):continue
            fields=line.split()
            if len(fields)<2:raise DeviceProfileError('INVALID_ADB_DEVICE_LIST')
            rows.append({'serial':fields[0],'state':fields[1],'metadata':fields[2:]})
        return rows

    async def run(self,serial,args):
        check_serial(serial,physical=True)
        if not isinstance(args,tuple) or args not in FIXED_COMMANDS:raise DeviceProfileError('READONLY_COMMAND_NOT_ALLOWLISTED')
        return await self._run(('-s',serial,*args),16*1024**2 if args==('exec-out','screencap','-p') else 2_000_000)


def parse_size(text):
    values=re.findall(r'(?:Physical|Override) size:\s*(\d+)x(\d+)',text)
    if not values:raise DeviceProfileError('DISPLAY_SIZE_UNAVAILABLE')
    size=tuple(map(int,values[-1]))
    if any(n<1 or n>16384 for n in size):raise DeviceProfileError('INVALID_DISPLAY_SIZE')
    return size


def parse_safety(power,policy,windows,display_input):
    from .native_windows import lock_state, interactive_state
    lock=lock_state(policy,windows)
    orientation=re.search(r'(?:SurfaceOrientation|surfaceOrientation)\s*[:=]\s*([0-3])',display_input)
    rotations={int(n) for n in re.findall(r'Viewport INTERNAL:[^\r\n]*\bdisplayId=0,[^\r\n]*\borientation=([0-3])\b',display_input)}
    focused=re.search(r'mCurrentFocus=.*?\s([\w.]+)/',windows)
    # Explicit dangerous window types remain independent of lock state. Render
    # completion fields do not assert that a Keyguard window is visible.
    suspicious=bool(re.search(r'permissioncontroller|securitypermission|TYPE_APPLICATION_OVERLAY|TYPE_SYSTEM_ALERT|biometric|credential|\bty=(?:TYPE_)?(?:KEYGUARD|KEYGUARD_DIALOG|2004|2009)\b',windows,re.I))
    return {'locked':lock['locked'],'interactive':interactive_state(power),
        'orientation':int(orientation[1]) if orientation else next(iter(rotations)) if len(rotations)==1 else None,'foreground_package':focused[1] if focused else None,
        'permission_or_unknown_overlay':suspicious,'overlay_absence_proven':False}


class PhysicalDeviceProbe:
    def __init__(self,serial,transport=None):
        check_serial(serial,physical=True)
        self.serial=serial;self.transport=transport or ReadOnlyAdb()

    async def text(self,args):return (await self.transport.run(self.serial,args)).decode('utf8','replace').strip()

    async def identity(self):
        return {prop:await self.text(('shell','getprop',prop)) for prop in IDENTITY_PROPERTIES}

    async def safety(self):
        metadata = {key: await self.text(args) for key, args in (
            ('power', ('shell','dumpsys','power')), ('policy', ('shell','dumpsys','window','policy')),
            ('windows', ('shell','dumpsys','window','windows')), ('displays', ('shell','dumpsys','window','displays')),
            ('input', ('shell','dumpsys','input')))}
        result = parse_safety(metadata['power'], metadata['policy'], metadata['windows']+'\n'+metadata['displays'], metadata['input'])
        # Native output must satisfy the complete metadata grammar. The legacy
        # summary parser never claims native overlay absence or capture consent.
        if 'WINDOW MANAGER WINDOWS' in metadata['windows']:
            from .native_windows import assess_native
            assessment = assess_native(metadata)
            result['native_window_assessment'] = assessment
            result['overlay_absence_proven'] = assessment['classification'] == 'SAFE_FOR_READONLY_CAPTURE'
            result['permission_or_unknown_overlay'] = not result['overlay_absence_proven']
        return result

    async def inventory(self,*,consent=False):
        if consent is not True:raise DeviceProfileError('READONLY_DEVICE_CONSENT_REQUIRED')
        rows=await self.transport.devices()
        matched=[d for d in rows if d['serial']==self.serial]
        if len(matched)!=1 or matched[0]['state']!='device':raise DeviceProfileError('EXPLICIT_DEVICE_NOT_AUTHORIZED_OR_ONLINE')
        before=await self.identity()
        # Validate expected device class before collecting any potentially private UI metadata.
        PhysicalDeviceProfile(self.serial,before)
        coloros={prop:await self.text(('shell','getprop',prop)) for prop in COLOROS_PROPERTIES}
        size=await self.text(('shell','wm','size'));density=await self.text(('shell','wm','density'))
        power=await self.text(('shell','dumpsys','power'));lock=await self.text(('shell','dumpsys','window','policy'))
        windows=await self.text(('shell','dumpsys','window','windows'));displays=await self.text(('shell','dumpsys','window','displays'))
        windows+='\n'+displays;inp=await self.text(('shell','dumpsys','input'))
        battery=await self.text(('shell','dumpsys','battery'));storage=await self.text(('shell','df','-k','/data'))
        packages=await self.text(('shell','pm','list','packages','--show-versioncode'))
        states=[await self.text(('get-state',)) for _ in range(3)]
        after=await self.identity()
        if before!=after:raise DeviceProfileError('IDENTITY_CHANGED_DURING_PROBE')
        if any(s!='device' for s in states):raise DeviceProfileError('READONLY_CONNECTION_UNSTABLE')
        installed=[]
        for line in packages.splitlines():
            match=re.fullmatch(r'package:([\w.]+)(?:\s+versionCode:(\d+))?',line.strip())
            if match:installed.append({'package':match[1],'version_code':match[2]})
        return {'schema':'physical-inventory/1','serial':self.serial,'identity':before,'identity_sha256':digest({'serial':self.serial,'identity':before}),
            'coloros':coloros,'adb_device_count':len(rows),'stable_state_samples':states,'screen_size':parse_size(size),
            'wm_size':size,'wm_density':density,'safety':parse_safety(power,lock,windows,inp),'battery_raw':battery,
            'storage_raw':storage,'installed_packages':installed,'raw_safety_evidence':{'power':power,'lock':lock,'windows':windows,'input':inp},
            'model_api_calls':0,'adb_inputs':0,'physical_autonomy_ready':False}


class ProfileBoundReadOnlyPhone:
    """Diagnostic adapter. Even a valid profile cannot send home/back/launch/tap."""
    kind='real'
    def __init__(self,profile,transport=None):
        if not isinstance(profile,PhysicalDeviceProfile):raise DeviceProfileError('PHYSICAL_PROFILE_REQUIRED')
        self.profile=profile;self.serial=profile.serial;self.probe=PhysicalDeviceProbe(self.serial,transport)

    async def observe(self):
        p=self.profile
        if not p.read_only_authorized:raise DeviceProfileError('READONLY_DEVICE_CONSENT_REQUIRED')
        p.verify(self.serial,await self.probe.identity())
        size=parse_size(await self.probe.text(('shell','wm','size')))
        safety=await self.probe.safety()
        if safety['locked'] is not False or safety['interactive'] is not True:raise DeviceProfileError('LOCKED_OR_INTERACTIVE_STATE_UNVERIFIED')
        if safety['permission_or_unknown_overlay'] or safety['foreground_package']!='com.android.settings':raise DeviceProfileError('UNAPPROVED_OR_OVERLAID_SCREEN')
        if safety['orientation'] is None:raise DeviceProfileError('ORIENTATION_UNVERIFIED')
        png=await self.probe.transport.run(self.serial,('exec-out','screencap','-p'))
        obs=ScreenObservation.from_png(png,self.serial)
        expected=size if safety['orientation'] in {0,2} else size[::-1]
        if (obs.width,obs.height)!=expected:raise DeviceProfileError('SCREEN_GEOMETRY_CHANGED')
        p.verify(self.serial,await self.probe.identity())
        if parse_size(await self.probe.text(('shell','wm','size')))!=size:raise DeviceProfileError('SCREEN_GEOMETRY_CHANGED')
        if await self.probe.safety()!=safety:raise DeviceProfileError('SCREEN_SAFETY_CHANGED')
        return obs

    async def act(self,action):raise PolicyDenied('PHYSICAL_GUI_INPUT_DISABLED: independent contract review required')
    async def reopen_settings(self):raise PolicyDenied('PHYSICAL_RECOVERY_LAUNCH_DISABLED')


class PhysicalCapabilityPolicy:
    """A non-granting draft registry: app approval alone never permits interaction."""
    version='physical-capabilities/draft-1'
    def wire(self):
        return {'version':self.version,'default_enabled':False,'autonomous_execution_enabled':False,
            'observation_only_packages':['com.android.settings'],'approved_navigation_packages':[],
            'pending_review_categories':['launcher','browser','bilibili','reader'],
            'separate_authorization_required':['send_message','add_contact','publish','upload','like','follow','favorite','edit_account','install','grant_permission','change_settings'],
            'prohibited':['payments','banking','delete_account_or_data','private_communications','phone_call','sms','credential_export'],
            'existing_registry_unchanged':True,'app_allowlist_is_not_an_app_sandbox':True}
    def check(self,action):raise PolicyDenied('PHYSICAL_CAPABILITY_NOT_GRANTED')


def physical_admission(base_status,*,identity_matches=False,screen_safe=False):
    """Read-only intersection with existing persistent admission; never grants execution."""
    reason='PHYSICAL_AUTONOMY_NOT_IMPLEMENTED'
    if base_status.get('effective_state')=='emergency_stopped':reason='OPERATOR_STOP'
    elif not base_status.get('safety_evidence_readable'):reason='SAFETY_EVIDENCE_UNAVAILABLE'
    elif base_status.get('requires_manual_review'):reason='UNKNOWN_DISPATCH_NEEDS_REVIEW'
    elif not identity_matches:reason='PHYSICAL_IDENTITY_MISMATCH'
    elif not screen_safe:reason='PHYSICAL_SCREEN_NOT_VERIFIED'
    return {**base_status,'worker_launch_allowed':False,'effective_resume_allowed':False,'resume_allowed':False,
        'initial_start_allowed_without_approval':False,'physical_input_allowed':False,'physical_admission_reason':reason,
        'base_admission_reason_code':base_status.get('admission_reason_code'),'admission_reason_code':reason,
        'effective_state':base_status.get('effective_state') if base_status.get('effective_state') in {'failed','emergency_stopped','completed_duration'} else 'blocked',
        'safety_terminated':base_status.get('safety_terminated',False)}


def xml_compatibility(xml):
    if len(xml)>2_000_000 or '<!DOCTYPE' in xml or '<!ENTITY' in xml:raise DeviceProfileError('UNSAFE_UI_XML')
    root=ET.fromstring(xml);nodes=list(root.iter('node'))
    return {'nodes':len(nodes),'rotation':root.get('rotation'),'required_missing':{k:sum(k not in n.attrib for n in nodes) for k in ['visible','editable','enabled','password','checkable','bounds']},
        'tree_sha256':hashlib.sha256(xml.encode()).hexdigest(),'trusted_for_physical_input':False}


async def diagnose(args):
    if not args.consent_read_only:raise DeviceProfileError('READONLY_DEVICE_CONSENT_REQUIRED')
    from .longrun import secure_directory
    out=args.output.resolve();secure_directory(out)
    transport=ReadOnlyAdb();probe=PhysicalDeviceProbe(args.serial,transport)
    try:
        inventory=await probe.inventory(consent=True)
        with (out/'inventory.private.json').open('x',encoding='utf8') as f:json.dump(inventory,f,ensure_ascii=False,indent=2)
        profile=PhysicalDeviceProfile(args.serial,inventory['identity']) # stays disabled until separate operator review
        with (out/'pending-profile.private.json').open('x',encoding='utf8') as f:json.dump(profile.wire(),f,ensure_ascii=False,indent=2)
        summary={'identity_sha256':profile.identity_sha256,'device_class':'physical','model_matches_find_x6':True,
            'inventory_recorded':True,'model_api_calls':0,'adb_inputs':0,'autonomous_execution_enabled':False,
            'snapshot':'NOT_OBSERVED','uiautomator':'NOT_OBSERVED'}
        if args.capture_settings:
            # Metadata consent is not C0 raw PNG/XML consent. Keep the option
            # recognizable but fail closed until the separately reviewed stage.
            summary.update(snapshot='NOT_OBSERVED',uiautomator='NOT_OBSERVED',
                           capture_reason='RAW_CAPTURE_REQUIRES_C0_APPROVAL')
        with (out/'summary.json').open('x',encoding='utf8') as f:json.dump(summary,f,ensure_ascii=False,indent=2)
        print(json.dumps(summary,ensure_ascii=False))
        return 0
    finally:
        with (out/'readonly-commands.private.json').open('x',encoding='utf8') as f:json.dump(transport.journal,f,indent=2)


def main(argv=None):
    parser=argparse.ArgumentParser(description='Opt-in read-only physical metadata probe; cannot run an Agent or send GUI input')
    parser.add_argument('--serial',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--consent-read-only',action='store_true')
    parser.add_argument('--capture-settings',action='store_true',help='Reserved for separately approved C0; currently reports RAW_CAPTURE_REQUIRES_C0_APPROVAL without capture')
    args=parser.parse_args(argv)
    try:return asyncio.run(diagnose(args))
    except (ValueError,RuntimeError,OSError) as error:
        print(json.dumps({'result':'BLOCKED','reason_code':getattr(error,'code',type(error).__name__),'adb_inputs':0,'model_api_calls':0}))
        return 2


if __name__=='__main__':raise SystemExit(main())
