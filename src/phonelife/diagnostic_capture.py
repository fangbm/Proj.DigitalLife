"""Independent C0 Prep diagnostic route. Real device dispatch HARD DISABLED.

Only the exact synthetic backend and offline verifier are accepted. No switch,
CLI argument, execution approval or profile enables real capture in this build.
No imports of Brain, GUI, Reflection, worker or execution permission machinery.
"""
from __future__ import annotations
import asyncio
from contextlib import closing
from copy import deepcopy
from enum import Enum
import json
from pathlib import Path
import re
import sqlite3
import time
import uuid
from .device_profiles import PhysicalDeviceProfile, IDENTITY_PROPERTIES, check_serial
from .diagnostic_authorization import DiagnosticLedger, OfflinePermitVerifier, validate_permit
from .diagnostic_evidence import DiagnosticDenied, PrivateEvidence, canonical, sha, validate_payload
from . import native_windows as native
from .readonly_sources import inspect_coordinator


class CaptureOperation(Enum):
    CAPTURE_SCREEN_PNG = 'CAPTURE_SCREEN_PNG'
    CAPTURE_UI_XML = 'CAPTURE_UI_XML'


FIXED_CAPTURE_ARGV = {
    CaptureOperation.CAPTURE_SCREEN_PNG: ('exec-out','screencap','-p'),
    CaptureOperation.CAPTURE_UI_XML: ('exec-out','uiautomator','dump','/dev/tty'),
}
FIXED_METADATA_ARGV = tuple(('shell','getprop',key) for key in IDENTITY_PROPERTIES) + (
    ('get-state',),('shell','wm','size'),('shell','dumpsys','power'),
    ('shell','dumpsys','window','policy'),('shell','dumpsys','window','windows'),
    ('shell','dumpsys','window','displays'),('shell','dumpsys','input'))
MAX_SESSION_SECONDS = 15
STEP_TIMEOUT_SECONDS = 3


def fixed_capture_argv(serial, operation):
    check_serial(serial, physical=True)
    if type(operation) is not CaptureOperation: raise DiagnosticDenied('FIXED_OPERATION_REQUIRED')
    return ('-s',serial,*FIXED_CAPTURE_ARGV[operation])


class RealAdbDiagnosticBackend:
    """Deliberate non-dispatching stub; no subprocess or adb dependency."""
    async def capture(self, *args, **kwargs):
        raise DiagnosticDenied('PREP_REAL_DISPATCH_DISABLED')


class MockAdbDiagnosticBackend:
    """Synthetic data ONLY. Identity fence prevents using real profiles here."""
    def __init__(self, profile, metadata, payloads):
        if type(profile) is not PhysicalDeviceProfile or profile.identity['ro.build.fingerprint'] != 'offline_fixture':
            raise DiagnosticDenied('SYNTHETIC_PROFILE_REQUIRED')
        self.profile = profile
        self.snapshots = [{'serial':profile.serial,'identity':dict(profile.identity),'devices':[(profile.serial,'device')],
                           'metadata':deepcopy(metadata)}]
        self.payloads = dict(payloads)
        self.calls = []
        self.capture_error = None
        self.delay_seconds = 0
        self.snapshot_index = 0

    async def snapshot(self):
        value = deepcopy(self.snapshots[min(self.snapshot_index,len(self.snapshots)-1)])
        self.snapshot_index += 1
        self.calls.append('METADATA_SNAPSHOT')
        return value

    async def capture(self, serial, operation):
        fixed_capture_argv(serial,operation)
        self.calls.append(operation.value)
        if self.delay_seconds: await asyncio.sleep(self.delay_seconds)
        if self.capture_error:
            code = self.capture_error if self.capture_error in {'DIAGNOSTIC_ADB_NONZERO','DIAGNOSTIC_ADB_OUTCOME_UNKNOWN'} else 'DIAGNOSTIC_BACKEND_FAILED'
            raise DiagnosticDenied(code)
        return self.payloads[operation.value]


def diagnostic_scene(metadata):
    """Content/privacy gate, NOT autonomous touch admission.

    Hidden OEM windows/unknown input consumers do not alone prove displayed
    content. Unknown *displayed* scope still rejects ordinary Settings capture.
    Conservative native grammar; no inferred image semantics or vision model.
    """
    try:
        if type(metadata) is not dict or set(metadata) != {'power','policy','windows','displays','input'}:
            raise ValueError()
        if any(type(v) is not str or len(v.encode()) > 2_000_000 for v in metadata.values()): raise ValueError()
        for k,header in [('power','POWER MANAGER (dumpsys power)'),('policy','WINDOW MANAGER POLICY STATE (dumpsys window policy)'),('displays','WINDOW MANAGER DISPLAY CONTENTS (dumpsys window displays)')]:
            if header not in metadata[k]: raise ValueError()
        locked = native.lock_state(metadata['policy'],metadata['displays'])
        if locked['locked'] is not False or locked['conflict'] or locked['invalid'] or native.interactive_state(metadata['power']) is not True:
            raise ValueError()
        rows = native._window_graph_rows(metadata['windows'])
        focus, inputs, _ = native._input_consensus(metadata['input'])
        focus_rows = [r for r in rows if r['id'] == focus]
        if len(focus_rows) != 1 or focus_rows[0]['package'] != 'com.android.settings': raise ValueError()
        wms_focus=re.findall(r'mCurrentFocus=Window\{([0-9a-f]+) u\d+ [^}\n]+\}',metadata['windows'])
        if wms_focus and wms_focus != [focus]: raise ValueError()
        displays = metadata['displays']
        focus_evidence = re.findall(r'm(?:CurrentFocus|ObscuringWindow)=Window\{([0-9a-f]+) u\d+ ([^}\s]+)\}',displays)
        if len(focus_evidence) != 2 or any(i != focus or a not in {'com.android.settings/.Settings','com.android.settings/com.android.settings.Settings'} for i,a in focus_evidence):
            raise ValueError()
        size = re.findall(r'\bcur=(\d+)x(\d+)',displays)
        rotation = re.findall(r'Viewport INTERNAL:[^\n]*displayId=0,[^\n]*orientation=([0-3])\b',metadata['input'])
        if len(size) != 1 or not rotation or len(set(rotation)) != 1: raise ValueError()
        width,height = map(int,size[0])
        if not 1 <= width <= 16384 or not 1 <= height <= 16384: raise ValueError()
        for row in rows:
            if row['display'] != 0: raise ValueError()
            nondisplayed = not any(row[k] for k in ('surface','shown','ready','on_screen','visible')) and row['view'] in {4,8}
            if row['id'] != focus and nondisplayed: continue
            visible = any(row[k] for k in ('shown','ready','on_screen','requested'))
            if row['id'] == focus:
                if row['type'] not in {1,2} or not all(row[k] for k in ('surface','shown','ready','on_screen','visible','requested')) or row['last_hidden'] or row['obscured'] or row['frame'] != (0,0,width,height): raise ValueError()
            elif visible:
                x0,y0,x1,y1 = row['frame']
                if row['package'] != 'com.android.systemui' or row['type'] not in {2000,2019} or not (0 <= x0 <= x1 <= width and 0 <= y0 <= y1 <= height and (y0 == 0 or y1 == height) and y1-y0 <= height//4): raise ValueError()
            elif not row['last_hidden'] or row['view'] not in {4,8} and row['surface'] is False:
                raise ValueError()
        # Ignore dump timestamps/counters, retain every parsed native safety fact.
        projection={'windows':rows,'inputs':inputs,'focus':focus,'locked':locked,'size':size,'rotation':rotation}
        return {'scope':'settings_home','width':width,'height':height,'rotation':int(rotation[0]),
                'scene_sha256':sha(canonical(projection)), 'physical_input_allowed':False}
    except (ValueError,TypeError,KeyError,IndexError,native.NativeMetadataError):
        raise DiagnosticDenied('DIAGNOSTIC_CONTENT_SCOPE_UNVERIFIED') from None


class DiagnosticSafetySources:
    """Explicit coordinator plus existing control/audit sources, no life creation.

    Mandatory source inventory is operator-owned for future real activation.
    Current Prep calls use temporary databases only. No writes/migrations here.
    """
    def __init__(self, coordinator, *, control_paths=(), audit_paths=()):
        self.coordinator = Path(coordinator)
        self.control_paths = tuple(Path(p) for p in control_paths)
        self.audit_paths = tuple(Path(p) for p in audit_paths)

    def check(self):
        # Historical physical attempts also use physical:<identity> device IDs.
        # Read all retained audit namespaces conservatively; lease still uses real.
        facts = inspect_coordinator(self.coordinator)
        if not facts['role_readable'] or not facts['historical_audit_readable']:
            raise DiagnosticDenied('DIAGNOSTIC_SAFETY_SOURCE_UNREADABLE')
        if facts['unknown_dispatches'] or facts.get('pending_review_runs'):
            raise DiagnosticDenied('UNKNOWN_DISPATCH_NEEDS_REVIEW')
        # A lease acquired by this diagnostic is checked separately by owner.
        for p in self.audit_paths:
            other = inspect_coordinator(p)
            if not other['role_readable'] or not other['historical_audit_readable']:
                raise DiagnosticDenied('DIAGNOSTIC_SAFETY_SOURCE_UNREADABLE')
            if other['unknown_dispatches'] or other.get('pending_review_runs'):
                raise DiagnosticDenied('UNKNOWN_DISPATCH_NEEDS_REVIEW')
        for p in self.control_paths:
            import os
            if os.path.lexists(str(p)+'.review-stop.json'): raise DiagnosticDenied('UNKNOWN_DISPATCH_NEEDS_REVIEW')
            if not p.is_file(): raise DiagnosticDenied('DIAGNOSTIC_SAFETY_SOURCE_UNREADABLE')
            try:
                with closing(sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True,timeout=.25)) as c:
                    c.execute('PRAGMA query_only=ON')
                    state = json.loads(c.execute('SELECT payload FROM session WHERE id=1').fetchone()[0])
                if not isinstance(state,dict) or state.get('state') not in {'running','sleeping','budget_wait','recovering','paused','emergency_stopped','failed','completed_duration','awaiting_approval','prepared','interrupted','fault_backoff'}:
                    raise DiagnosticDenied('DIAGNOSTIC_SAFETY_SOURCE_UNREADABLE')
                if state.get('state') == 'emergency_stopped' or state.get('operator') == 'emergency_stopped':
                    raise DiagnosticDenied('EMERGENCY_STOP')
                if state.get('safety_stop') or state.get('requires_manual_review') or state.get('reason') == 'UNKNOWN_DISPATCH_NEEDS_REVIEW':
                    raise DiagnosticDenied('UNKNOWN_DISPATCH_NEEDS_REVIEW')
            except (sqlite3.Error,ValueError,TypeError,AttributeError,IndexError):
                raise DiagnosticDenied('DIAGNOSTIC_SAFETY_SOURCE_UNREADABLE') from None
        return facts


class DiagnosticLease:
    """Same shared 'real' mutex. Existing schema only, bounded TTL, no force."""
    def __init__(self, sources):
        self.sources = sources; self.owner = 'diagnostic:'+uuid.uuid4().hex; self.conn = None

    def __enter__(self):
        self.sources.check()
        self.conn = sqlite3.connect(self.sources.coordinator.resolve().as_uri()+'?mode=rw',uri=True,timeout=.25,isolation_level=None)
        try:
            self.conn.execute('BEGIN IMMEDIATE')
            now = time.time()
            row = self.conn.execute('SELECT expires_at FROM lease WHERE device=?',('real',)).fetchone()
            if row and row[0] > now: raise DiagnosticDenied('DIAGNOSTIC_DEVICE_BUSY')
            self.conn.execute('INSERT INTO lease VALUES(?,?,?) ON CONFLICT(device) DO UPDATE SET owner=excluded.owner,expires_at=excluded.expires_at',('real',self.owner,now+MAX_SESSION_SECONDS+5))
            self.conn.execute('COMMIT')
            return self
        except BaseException:
            if self.conn.in_transaction: self.conn.execute('ROLLBACK')
            self.conn.close(); self.conn = None
            raise

    def check(self):
        self.sources.check()
        row = self.conn.execute('SELECT owner,expires_at FROM lease WHERE device=?',('real',)).fetchone()
        if not row or row[0] != self.owner or row[1] <= time.time(): raise DiagnosticDenied('DIAGNOSTIC_LEASE_LOST')

    def __exit__(self,*args):
        try: self.conn.execute('DELETE FROM lease WHERE device=? AND owner=?',('real',self.owner))
        finally: self.conn.close()


def diagnostic_capture_admission(profile, snapshot, grant, output_directory, now):
    if snapshot.get('devices') != [(profile.serial,'device')] or snapshot.get('serial') != profile.serial:
        raise DiagnosticDenied('DIAGNOSTIC_DEVICE_AMBIGUOUS_OR_OFFLINE')
    if snapshot.get('identity') != dict(profile.identity): raise DiagnosticDenied('DIAGNOSTIC_IDENTITY_MISMATCH')
    validate_permit(grant,identity_sha256=profile.identity_sha256,output_directory=output_directory,now=now)
    return diagnostic_scene(snapshot.get('metadata'))


class PhysicalDiagnosticCapture:
    def __init__(self, *, backend=None, verifier=None, evidence=None, sources=None):
        if type(backend) is not MockAdbDiagnosticBackend or type(verifier) is not OfflinePermitVerifier:
            raise DiagnosticDenied('PREP_REAL_DISPATCH_DISABLED')
        if type(evidence) is not PrivateEvidence or type(sources) is not DiagnosticSafetySources:
            raise DiagnosticDenied('DIAGNOSTIC_DEPENDENCIES_REQUIRED')
        self.backend,self.verifier,self.evidence,self.sources = backend,verifier,evidence,sources

    async def capture(self, blob, *, request_id, operation):
        if not isinstance(request_id,str) or not re.fullmatch(r'[0-9a-f]{32}',request_id) or type(operation) is not CaptureOperation:
            raise DiagnosticDenied('FIXED_REQUEST_REQUIRED')
        if type(self.backend) is not MockAdbDiagnosticBackend or type(self.verifier) is not OfflinePermitVerifier:
            raise DiagnosticDenied('PREP_REAL_DISPATCH_DISABLED')
        record = {'schema':'diagnostic-capture/1','category':'offline_fixture','request_id':request_id,
                  'operation':operation.value,'started_wall':time.time(),'started_monotonic_ns':time.monotonic_ns(),
                  'input_dispatched':False,'old_actions_replayed':0,'model_api_calls':0,
                  'GUI_EXECUTION_SEMANTICS_TRUSTED':False}
        ledger = None; spent = False
        try:
            grant = self.verifier.verify(blob)
            validate_permit(grant,identity_sha256=self.backend.profile.identity_sha256,output_directory=self.evidence.path,now=time.time())
            self.evidence.check()
            record.update(permit_sha256=sha(canonical(grant)),audit_id=grant['audit_id'],identity_sha256=grant['identity_sha256'])
            with DiagnosticLease(self.sources) as lease:
                async with asyncio.timeout(MAX_SESSION_SECONDS):
                    before = await asyncio.wait_for(self.backend.snapshot(),STEP_TIMEOUT_SECONDS)
                    scene = diagnostic_capture_admission(self.backend.profile,before,grant,self.evidence.path,time.time())
                    record['before_metadata_sha256']=sha(canonical(before['metadata']))
                    lease.check(); ledger = DiagnosticLedger(self.evidence)
                    ledger.consume(grant,request_id,operation.value,record); spent = True
                    record['command_sha256'] = sha(canonical(fixed_capture_argv(self.backend.profile.serial,operation)))
                    raw = await asyncio.wait_for(self.backend.capture(self.backend.profile.serial,operation),STEP_TIMEOUT_SECONDS)
                    record['raw_sha256'] = sha(raw); record['bytes'] = len(raw)
                    summary = validate_payload(operation.value,raw)
                    after = await asyncio.wait_for(self.backend.snapshot(),STEP_TIMEOUT_SECONDS)
                    fresh = diagnostic_capture_admission(self.backend.profile,after,grant,self.evidence.path,time.time())
                    record['after_metadata_sha256']=sha(canonical(after['metadata']))
                    if scene != fresh: raise DiagnosticDenied('DIAGNOSTIC_SCENE_CHANGED')
                    if operation is CaptureOperation.CAPTURE_SCREEN_PNG and (summary['width'],summary['height']) != (scene['width'],scene['height']):
                        raise DiagnosticDenied('DIAGNOSTIC_GEOMETRY_MISMATCH')
                    if operation is CaptureOperation.CAPTURE_UI_XML and (summary['rotation'] != scene['rotation'] or not summary['all_settings']):
                        raise DiagnosticDenied('DIAGNOSTIC_XML_SCOPE_MISMATCH')
                    lease.check(); record['private_file'] = self.evidence.save(request_id,operation.value,raw)
                    lease.check(); record.update(summary=summary,result='DIAGNOSTIC_CAPTURE_COMPLETED',reason_code='OK')
                    record['ended_monotonic_ns'] = time.monotonic_ns()
                    ledger.finish(request_id,'completed',record)
            # White list: no file paths, serial, raw text, prompts or credentials.
            return {k:record[k] for k in ('schema','category','request_id','operation','audit_id','permit_sha256','identity_sha256','raw_sha256','bytes','command_sha256','started_wall','started_monotonic_ns','ended_monotonic_ns','summary','result','reason_code','input_dispatched','old_actions_replayed','model_api_calls','GUI_EXECUTION_SEMANTICS_TRUSTED')}
        except BaseException as error:
            code = error.code if isinstance(error,DiagnosticDenied) else 'DIAGNOSTIC_TIMEOUT_UNKNOWN' if isinstance(error,TimeoutError) else 'DIAGNOSTIC_CANCELLED_UNKNOWN' if isinstance(error,asyncio.CancelledError) else 'DIAGNOSTIC_IO_OR_AUDIT_FAILURE'
            if ledger and spent:
                record.update(result='FAILED_OR_UNKNOWN',reason_code=code,ended_monotonic_ns=time.monotonic_ns())
                try: ledger.finish(request_id,'spent_failed_or_unknown',record)
                except sqlite3.Error: pass  # Durable started remains spent; never replay.
            if isinstance(error,(KeyboardInterrupt,SystemExit,asyncio.CancelledError)): raise
            raise DiagnosticDenied(code) from None
        finally:
            if ledger: ledger.close()
