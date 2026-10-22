"""One human-supervised diagnostic, never an Agent capture permission.

No CLI, model hook, DPAPI issuer, GUI input or automatic retry. The caller must
record an explicit human approval of the exact prepared request digest. This is
an audited operator workflow, not cryptographic proof against the OS owner.
"""
from __future__ import annotations
import asyncio
import os
from pathlib import Path
import re
import time
import uuid
from .device_profiles import PhysicalDeviceProfile, IDENTITY_PROPERTIES, check_serial
from .diagnostic_capture import DiagnosticLease, DiagnosticSafetySources, diagnostic_scene
from .diagnostic_evidence import DiagnosticDenied, PrivateEvidence, canonical, sha, validate_payload
from .diagnostic_authorization import strict_json
from .longrun_state import runtime_manifest

PNG = ('exec-out','screencap','-p')
XML = ('exec-out','uiautomator','dump','/proc/self/fd/1')
METADATA = {'power':('shell','dumpsys','power'),'policy':('shell','dumpsys','window','policy'),
            'windows':('shell','dumpsys','window','windows'),'displays':('shell','dumpsys','window','displays'),
            'input':('shell','dumpsys','input')}
ALLOWED = frozenset([PNG,XML,('get-state',),*(('shell','getprop',k) for k in IDENTITY_PROPERTIES),*METADATA.values()])
STEP_SECONDS = 3
TOTAL_SECONDS = 15


def runtime_sha(): return sha(canonical(runtime_manifest()))


def prepare_request(profile, executable, evidence, sources):
    if type(profile) is not PhysicalDeviceProfile or type(evidence) is not PrivateEvidence or type(sources) is not DiagnosticSafetySources:
        raise DiagnosticDenied('MANUAL_DIAGNOSTIC_BINDINGS_REQUIRED')
    if profile.identity['ro.build.version.sdk']!='36' or profile.identity['ro.build.version.release']!='16':
        raise DiagnosticDenied('MANUAL_API36_ANDROID16_REQUIRED')
    path=Path(executable)
    if not path.is_absolute() or path.is_symlink() or not path.is_file() or path.name.lower()!='adb.exe':
        raise DiagnosticDenied('EXPLICIT_TRUSTED_ADB_REQUIRED')
    evidence.check()
    return {'schema':'manual-settings-diagnostic/1','identity_sha256':profile.identity_sha256,
            'runtime_sha256':runtime_sha(),'adb_path':str(path),'adb_sha256':sha(path.read_bytes()),
            'output_directory':str(evidence.path),'coordinator':str(sources.coordinator.resolve()),
            'safety_source_sha256':sha(canonical({'controls':[str(p.resolve()) for p in sources.control_paths],
                                                'audits':[str(p.resolve()) for p in sources.audit_paths]})),
            'scope':'settings_home','png':1,'xml':1,'nonce':uuid.uuid4().hex,
            'step_seconds':STEP_SECONDS,'total_seconds':TOTAL_SECONDS,'created_at':time.time(),
            'input_allowed':False,'model_allowed':False,'worker_allowed':False}


def request_sha(request): return sha(canonical(request))


def _write_json(evidence,name,value):
    evidence.check()
    if not re.fullmatch(r'[a-z0-9-]+[.]json',name): raise DiagnosticDenied('PRIVATE_FILENAME_INVALID')
    fd=os.open(evidence.path/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'wb') as f:f.write(canonical(value));f.flush();os.fsync(f.fileno())


def extract_xml(raw):
    """Only pure XML or the exact AOSP completion trailer, never arbitrary slicing."""
    if not isinstance(raw,bytes) or len(raw)>2_000_000: raise DiagnosticDenied('MANUAL_XML_OUTPUT_LIMIT')
    raw=raw.strip()
    end=raw.find(b'</hierarchy>')
    if end<0: raise DiagnosticDenied('MANUAL_XML_UNAVAILABLE')
    end+=len(b'</hierarchy>')
    trailer=raw[end:].strip()
    if trailer not in {b'',b'UI hierchary dumped to: /proc/self/fd/1'}:
        raise DiagnosticDenied('MANUAL_XML_UNEXPECTED_OUTPUT')
    result=raw[:end]
    validate_payload('CAPTURE_UI_XML',result)
    return result


class ManualDiagnosticTransport:
    """Fixed subprocess vectors; no inherited ADB target/server environment."""
    def __init__(self, request, profile, lease, evidence):
        self.request=request;self.profile=profile;self.lease=lease;self.evidence=evidence
        self.calls=[];self.remaining={PNG:1,XML:1};self.active=False

    def guard(self,args):
        if not self.active: raise DiagnosticDenied('EXPLICIT_MANUAL_APPROVAL_REQUIRED')
        marker=self.evidence.path/'manual-session-started.json'
        if not marker.is_file() or (self.evidence.path/'manual-result-private.json').exists():
            raise DiagnosticDenied('EXPLICIT_MANUAL_APPROVAL_REQUIRED')
        with marker.open('rb') as f:approval=strict_json(f.read(32769))
        if (request_sha(approval.get('request'))!=request_sha(self.request) or not approval.get('approval_reference') or
            not 0<=time.monotonic_ns()-approval.get('started_monotonic_ns',-1)<=TOTAL_SECONDS*1_000_000_000 or
            not 0<=time.time()-self.request['created_at']<=600):
            raise DiagnosticDenied('EXPLICIT_MANUAL_APPROVAL_REQUIRED')
        if type(args) is not tuple or args not in ALLOWED|{('devices','-l')}:
            raise DiagnosticDenied('MANUAL_FIXED_COMMAND_REQUIRED')
        self.evidence.check();self.lease.check()
        if runtime_sha()!=self.request['runtime_sha256']:raise DiagnosticDenied('MANUAL_RUNTIME_CHANGED')
        if self.profile.identity_sha256!=self.request['identity_sha256']:raise DiagnosticDenied('MANUAL_IDENTITY_CHANGED')
        if sha(Path(self.request['adb_path']).read_bytes())!=self.request['adb_sha256']:raise DiagnosticDenied('MANUAL_ADB_CHANGED')
        if args in self.remaining:
            if self.remaining[args]<=0:raise DiagnosticDenied('MANUAL_CAPTURE_ALREADY_SPENT')
            # Durable quota reservation before process creation; never refund.
            _write_json(self.evidence,('png' if args==PNG else 'xml')+'-spent.json',
                        {'request_sha256':request_sha(self.request),'nonce':self.request['nonce'],'at':time.time()})
            self.remaining[args]-=1

    async def run(self,args):
        self.guard(args);check_serial(self.profile.serial,physical=True)
        argv=args if args==('devices','-l') else ('-s',self.profile.serial,*args)
        limit=16*1024**2 if args==PNG else 2_000_000
        entry={'argv_sha256':sha(canonical(argv)),'started_ns':time.monotonic_ns(),
               'input_dispatched':False,'returncode':None,'status':'not_started'}
        self.calls.append(entry);proc=None;tasks=[]
        async def bounded(stream,maximum):
            chunks=[];size=0
            while True:
                part=await stream.read(min(65536,maximum-size+1))
                if not part:return b''.join(chunks)
                chunks.append(part);size+=len(part)
                if size>maximum:raise DiagnosticDenied('MANUAL_OUTPUT_LIMIT')
        try:
            async with asyncio.timeout(STEP_SECONDS):
                # Require an existing compatible local server; never start/kill it.
                reader,writer=await asyncio.open_connection('127.0.0.1',5037)
                try:
                    writer.write(b'000chost:version');await writer.drain()
                    if await reader.readexactly(4)!=b'OKAY':raise DiagnosticDenied('ADB_SERVER_UNAVAILABLE')
                    length=int(await reader.readexactly(4),16)
                    if length!=4 or await reader.readexactly(4)!=b'0029':raise DiagnosticDenied('ADB_SERVER_VERSION_MISMATCH')
                finally:
                    writer.close();await asyncio.wait_for(writer.wait_closed(),.25)
                self.lease.check()
                env={k:v for k,v in os.environ.items() if not k.startswith(('ADB_','ANDROID_')) and not any(t in k.upper() for t in ('API_KEY','TOKEN','PASSWORD','COOKIE'))}
                env['ADB_SERVER_SOCKET']='tcp:localhost:5037'
                proc=await asyncio.create_subprocess_exec(self.request['adb_path'],*argv,
                    stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,env=env,
                    **({'creationflags':0x08000000} if os.name=='nt' else {}))
                entry.update(pid=proc.pid,status='dispatched_unknown')
                tasks=[asyncio.create_task(bounded(proc.stdout,limit)),asyncio.create_task(bounded(proc.stderr,4096)),asyncio.create_task(proc.wait())]
                output,errors,_=await asyncio.gather(*tasks)
                entry.update(returncode=proc.returncode,stdout_sha256=sha(output),stderr_sha256=sha(errors))
                if proc.returncode:raise DiagnosticDenied('MANUAL_ADB_NONZERO_NO_RETRY')
                entry['status']='transport_complete'
                return output
        except BaseException as e:
            entry['status']='failed_or_unknown_no_retry'
            if isinstance(e,(asyncio.CancelledError,KeyboardInterrupt,SystemExit)):raise
            raise DiagnosticDenied(e.code if isinstance(e,DiagnosticDenied) else 'MANUAL_TRANSPORT_UNKNOWN_NO_RETRY') from None
        finally:
            for task in tasks:
                if not task.done():task.cancel()
            if proc and proc.returncode is None:
                try:proc.kill()
                except ProcessLookupError:pass
                try:await asyncio.wait_for(proc.wait(),1)
                except TimeoutError:entry['cleanup']='OWNED_CLIENT_EXIT_UNCONFIRMED'
            if tasks:await asyncio.gather(*tasks,return_exceptions=True)
            entry['ended_ns']=time.monotonic_ns()

    async def snapshot(self):
        text=(await self.run(('devices','-l'))).decode('utf8','strict')
        devices=[tuple(line.split()[:2]) for line in text.splitlines() if line.strip() and not line.startswith('List of devices')]
        if devices!=[(self.profile.serial,'device')]:raise DiagnosticDenied('MANUAL_USB_TARGET_UNVERIFIED')
        if (await self.run(('get-state',))).strip()!=b'device':raise DiagnosticDenied('MANUAL_USB_TARGET_UNVERIFIED')
        identity={k:(await self.run(('shell','getprop',k))).decode('utf8','strict').strip() for k in IDENTITY_PROPERTIES}
        if identity!=dict(self.profile.identity):raise DiagnosticDenied('MANUAL_EXACT_IDENTITY_MISMATCH')
        metadata={k:(await self.run(v)).decode('utf8','strict') for k,v in METADATA.items()}
        scene=diagnostic_scene(metadata)
        # diagnostic_scene requires WMS display focus+obscuring and Input focus
        # consensus. ColorOS omits this field from `window windows`; that missing
        # duplicate is not missing WMS focus evidence and grants no touch trust.
        return {'identity':identity,'metadata':metadata,'scene':scene,'observed_ns':time.monotonic_ns()}


async def capture_manually_approved(request, *, approved_sha256=None, operator_reference=None,
                                    profile=None,evidence=None,sources=None):
    """Called only after human approval in the supervising conversation.

    The same account can forge function arguments; no claim of an independent
    security principal. Agent/GUI execution paths never import or call this API.
    """
    if approved_sha256!=request_sha(request) or not isinstance(operator_reference,str) or not operator_reference.strip():
        raise DiagnosticDenied('EXPLICIT_MANUAL_APPROVAL_REQUIRED')
    expected=prepare_request(profile,request['adb_path'],evidence,sources)
    if any(request.get(k)!=expected[k] for k in expected if k not in {'created_at','nonce'}):raise DiagnosticDenied('MANUAL_REQUEST_CHANGED')
    if set(request)!=set(expected) or not re.fullmatch(r'[0-9a-f]{32}',request['nonce']) or not 0<=time.time()-request['created_at']<=600:
        raise DiagnosticDenied('MANUAL_REQUEST_EXPIRED_OR_INVALID')
    _write_json(evidence,'manual-session-started.json',{'request':request,'approval_reference':operator_reference,'started_monotonic_ns':time.monotonic_ns()})
    transport=None;record={'request_sha256':request_sha(request),'category':'human_supervised_diagnostic',
        'source':'native_device','input_dispatched':False,'old_actions_replayed':0,'model_api_calls':0,
        'GUI_EXECUTION_SEMANTICS_TRUSTED':False,'result':'FAILED_OR_UNKNOWN','files':[]}
    try:
        with DiagnosticLease(sources) as lease:
            transport=ManualDiagnosticTransport(request,profile,lease,evidence);transport.active=True
            async with asyncio.timeout(TOTAL_SECONDS):
                before=await transport.snapshot();_write_json(evidence,'before-private.json',before)
                for operation,args in [('CAPTURE_SCREEN_PNG',PNG),('CAPTURE_UI_XML',XML)]:
                    raw=await transport.run(args)
                    # Retain complete bounded native bytes privately before postchecks.
                    rid=uuid.uuid4().hex;name=evidence.save(rid,operation,raw)
                    item={'operation':operation,'file':name,'raw_sha256':sha(raw),'bytes':len(raw),'scope_verified':False}
                    record['files'].append(item)
                    payload=extract_xml(raw) if args==XML else raw
                    summary=validate_payload(operation,payload)
                    after=await transport.snapshot();_write_json(evidence,('after-png' if args==PNG else 'after-xml')+'-private.json',after)
                    if before['scene']!=after['scene']:raise DiagnosticDenied('MANUAL_SCENE_CHANGED_PRIVATE_QUARANTINE')
                    if args==PNG and (summary['width'],summary['height'])!=(after['scene']['width'],after['scene']['height']):raise DiagnosticDenied('MANUAL_PNG_GEOMETRY_MISMATCH')
                    if args==XML and (not summary['all_settings'] or summary['rotation']!=after['scene']['rotation']):raise DiagnosticDenied('MANUAL_XML_SCOPE_MISMATCH')
                    item.update(scope_verified=True,summary=summary,payload_sha256=sha(payload))
                    if payload!=raw:item['normalized_file']=evidence.save(uuid.uuid4().hex,operation,payload)
                    before=after
                lease.check();record.update(result='REAL_SCREEN_CAPTURE_VERIFIED',reason_code='OK')
    except BaseException as e:
        record['reason_code']=e.code if isinstance(e,DiagnosticDenied) else 'MANUAL_CAPTURE_FAILED_OR_UNKNOWN'
        if isinstance(e,(asyncio.CancelledError,KeyboardInterrupt,SystemExit)):raise
    finally:
        if transport:transport.active=False;record['commands']=transport.calls
        _write_json(evidence,'manual-result-private.json',record)
    return {k:record[k] for k in ('request_sha256','category','input_dispatched','old_actions_replayed','model_api_calls','GUI_EXECUTION_SEMANTICS_TRUSTED','result','reason_code')}
