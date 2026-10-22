"""Explicit SQLite roles for non-granting physical observation review.

No discovery of a substitute empty database, migrations, marker writes, ADB,
model clients or workers. A legacy coordinator is not an agent/control database.
Actual session readiness requires an explicitly bound existing control record.
"""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time

from .longrun_health import readonly_db_health, safety_admission
from .longrun_state import SessionLedger, SoakPolicy, canonical, REVIEW_STOP


@contextmanager
def _readonly(path):
    path=Path(path).resolve()
    if not path.is_file(): raise FileNotFoundError('SAFETY_SOURCE_MISSING')
    conn=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.25)
    try:
        until=time.monotonic()+.75
        conn.execute('PRAGMA query_only=ON')
        conn.set_progress_handler(lambda:int(time.monotonic()>until),500)
        if conn.execute('PRAGMA quick_check').fetchone()[0]!='ok': raise ValueError('SAFETY_SOURCE_CORRUPT')
        yield conn
        if time.monotonic()>until: raise TimeoutError('SAFETY_SOURCE_READ_BOUND')
    finally:conn.close()


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _schema(conn):
    if conn.execute('PRAGMA user_version').fetchone()[0] not in {0,11,12}:
        raise ValueError('UNSUPPORTED_CORE_SCHEMA')


def inspect_coordinator(path, *, devices=None, now=None):
    """Lease facts plus retained legacy dispatch audit, never life-control facts.

    Absence of life_controls is normal in this role. Missing audit schema is
    reported separately, never silently promoted to complete safety evidence.
    No raw owner strings, paths, task IDs or payloads enter this public projection.
    """
    result={'role':'shared_device_coordinator','role_readable':False,'historical_audit_readable':False,
            'active_leases':None,'unknown_dispatches':None,'reason_codes':[]}
    try:
        with _readonly(path) as conn:
            _schema(conn);tables=_tables(conn)
            if 'lease' not in tables or 'session' in tables: raise ValueError('WRONG_COORDINATOR_ROLE')
            now=time.time() if now is None else now
            leases=list(conn.execute('SELECT device,expires_at FROM lease'))
            if any(not isinstance(expiry,(int,float)) or not math.isfinite(expiry) for _,expiry in leases):raise ValueError('INVALID_LEASE_EVIDENCE')
            result['active_leases']=sum(1 for device,expiry in leases
                if expiry>now and (devices is None or device in set(devices)|{'autonomous-api-writer'}))
            result['life_controls_present']='life_controls' in tables
            if {'task_runs','action_attempts'} <= tables:
                unknown=0
                for device,status,payload in conn.execute('SELECT t.device,t.status,a.payload FROM action_attempts a LEFT JOIN task_runs t ON t.task_run_id=a.task_run_id'):
                    if device is None or status is None:raise ValueError('ORPHAN_LEGACY_ACTION_AUDIT')
                    value=json.loads(payload)
                    if devices is None or device in devices:
                        unknown+=value.get('execution_status') in {'dispatching','outcome_unknown','uncertain_after_transport_error'}
                pending=sum(1 for device,status in conn.execute('SELECT device,status FROM task_runs') if status=='needs_review' and (devices is None or device in devices))
                result.update(historical_audit_readable=True,unknown_dispatches=unknown,pending_review_runs=pending)
            result['role_readable']=True
    except (sqlite3.Error,OSError,ValueError,TypeError,TimeoutError) as error:
        result['reason_codes']=[str(error) if isinstance(error,ValueError) and str(error).isupper() else type(error).__name__]
        result['role_readable']=False
    return result


def observation_admission(profile, identity_before, identity_after, first, second,
                          sources, *, connected_before, connected_after,
                          started_ns, ended_ns):
    """Non-granting B2 review verdict; all raw sampling remains metadata-only."""
    from .native_windows import assess_window_pair
    pair=assess_window_pair(first,second,started_ns=started_ns,ended_ns=ended_ns)
    reasons=list(pair['reason_codes'])
    try:
        profile.verify(profile.serial,identity_before)
        profile.verify(profile.serial,identity_after)
        if identity_before!=identity_after:raise ValueError('IDENTITY_CHANGED')
    except (ValueError,AttributeError,TypeError): reasons.append('PHYSICAL_IDENTITY_OR_CONSENT_UNVERIFIED')
    if connected_before is not True or connected_after is not True: reasons.append('DEVICE_CONNECTION_UNVERIFIED')
    if not (sources.get('state')=='PASS' and sources.get('source_roles_bound') is True and
            sources.get('safety_evidence_readable') is True and sources.get('requires_manual_review') is False and
            sources.get('emergency_stopped') is False and sources.get('device_lease_free') is True and
            sources.get('base_safety_admission_allowed') is True):
        reasons.extend(sources.get('reason_codes') or ['SAFETY_SOURCE_ADMISSION_UNVERIFIED'])
    return {'state':'READY_FOR_C0_CAPTURE_REVIEW' if not reasons else 'BLOCKED',
            'reason_codes':sorted(set(reasons)),'window_pair':pair,'database_sources':sources,
            'raw_capture_authorized':False,'physical_input_allowed':False,'worker_launch_allowed':False}


def read_bound_sources(control_path, *, expected_session_id, agent_path, coordinator_path,
                       physical_identity_sha256=None):
    """Read the same configured roles as the four execution entry points.

    This is a database-only safety review, not physical_session_admission and
    not an execution/capture approval. It deliberately cannot trigger the latter's
    optional live ADB probe. Existing marker overlay and safety_admission are reused.
    """
    result={'state':'BLOCKED','safety_evidence_readable':False,'requires_manual_review':None,
            'emergency_stopped':None,'device_lease_free':None,'reason_codes':[],
            'physical_input_allowed':False,'worker_launch_allowed':False,'raw_capture_authorized':False}
    ledger=None
    try:
        if control_path is None or agent_path is None or not expected_session_id:
            raise ValueError('CURRENT_CONTROL_OR_AGENT_SOURCE_UNBOUND')
        paths=[Path(p).resolve() for p in (control_path,agent_path,coordinator_path)]
        if len(set(paths)) != 3: raise ValueError('DATABASE_ROLE_PATH_COLLISION')
        identities=[(p.stat().st_dev,p.stat().st_ino) for p in paths]
        if len(set(identities)) != 3: raise ValueError('DATABASE_FILE_IDENTITY_COLLISION')
        with _readonly(paths[0]) as conn:
            if conn.execute('PRAGMA user_version').fetchone()[0]!=0: raise ValueError('UNSUPPORTED_CONTROL_SCHEMA')
            tables=_tables(conn)
            if 'session' not in tables: raise ValueError('WRONG_SESSION_CONTROL_ROLE')
            raw=json.loads(conn.execute('SELECT payload FROM session WHERE id=1').fetchone()[0])
        if raw['session_id'] != expected_session_id: raise ValueError('SESSION_IDENTITY_MISMATCH')
        cfg=raw['config']
        if hashlib.sha256(canonical(cfg).encode()).hexdigest() != raw['fingerprint']: raise ValueError('CONFIG_FREEZE_MISMATCH')
        if Path(cfg['agent_db']).resolve()!=paths[1] or Path(cfg['lease_path']).resolve()!=paths[2]: raise ValueError('SAFETY_SOURCE_PATH_MISMATCH')
        binding=cfg.get('physical_execution',{}).get('life_binding')
        if cfg.get('physical_execution') and Path(cfg['physical_execution']['control_path']).resolve()!=paths[0]:
            raise ValueError('PHYSICAL_CONTROL_PATH_MISMATCH')
        if physical_identity_sha256 is not None and (not binding or binding.get('device_profile_id')!=physical_identity_sha256 or binding.get('device_session_id')!=expected_session_id):
            raise ValueError('PHYSICAL_SAFETY_SOURCE_IDENTITY_MISMATCH')
        with _readonly(paths[1]) as conn:
            _schema(conn);tables=_tables(conn)
            if 'session' in tables or not {'life_controls','life_schema_migrations','task_runs','action_attempts','life_requests'} <= tables:
                raise ValueError('AGENT_LIFE_SCHEMA_INCOMPLETE')
            if conn.execute('SELECT MAX(version) FROM life_schema_migrations').fetchone()[0]!=1:
                raise ValueError('UNSUPPORTED_LIFE_SCHEMA')
            mode=conn.execute('SELECT mode FROM life_controls WHERE device=?',(cfg['device'],)).fetchone()
            if mode and mode[0] not in {'running','paused','stopped'}: raise ValueError('UNSUPPORTED_AGENT_CONTROL_STATE')
        coordinator=inspect_coordinator(paths[2],devices=[cfg['device'],'real','subject:'+binding['life_subject_id']] if binding else [cfg['device']])
        result['coordinator']=coordinator
        if not coordinator['role_readable'] or not coordinator['historical_audit_readable']:
            raise ValueError('COORDINATOR_OR_LEGACY_AUDIT_UNREADABLE')
        health=readonly_db_health(paths[1],paths[2],cfg['device'],worker_alive=False,
            run_kind='autonomous' if cfg['mode']=='api' else 'mock_test',policy=SoakPolicy(**cfg['policy']),
            life_binding=binding,lease_key='real' if binding else None)
        if not health['database_readable']: raise ValueError('AGENT_SAFETY_EVIDENCE_UNAVAILABLE')
        if coordinator['unknown_dispatches'] or coordinator.get('pending_review_runs'):
            health={**health,'needs_review':True,'safety_review_required':True}
        ledger=SessionLedger(paths[0],readonly=True)
        latest=json.loads(ledger.conn.execute('SELECT payload FROM session WHERE id=1').fetchone()[0])
        if latest!=raw or identities!=[(p.stat().st_dev,p.stat().st_ino) for p in paths]:
            raise ValueError('SAFETY_SOURCES_CHANGED_DURING_READ')
        marker=ledger._marker_review(raw['session_id'])
        value=ledger.load()
        sql_stop=bool(raw.get('safety_stop') or raw.get('requires_manual_review') or raw.get('reason')==REVIEW_STOP)
        config_error=None
        try:
            from .longrun import verify_freeze
            verify_freeze(cfg)
        except (ValueError,OSError,KeyError) as error:
            config_error=str(error) if isinstance(error,ValueError) else 'CONFIG_EVIDENCE_UNAVAILABLE'
        base=safety_admission(value,health,control_state=raw['state'],persisted_stop=sql_stop or (marker is not None and not marker.get('evidence_unreadable')),
            barrier_present=sql_stop or marker is not None,marker_state='absent' if marker is None else 'invalid_or_unreadable' if marker.get('evidence_unreadable') else 'valid',
            worker_alive=False,expired=ledger.expired(raw),config_error=config_error)
        result.update(safety_evidence_readable=base['safety_evidence_readable'],requires_manual_review=base['requires_manual_review'],
            emergency_stopped=base['effective_state']=='emergency_stopped',device_lease_free=coordinator['active_leases']==0,
            source_roles_bound=True,base_safety_admission_allowed=base['safety_admission_allowed'],
            base_resume_allowed=base['resume_allowed'],base_worker_launch_allowed=base['worker_launch_allowed'],
            effective_state=base['effective_state'],marker_state=base['admission_evidence']['review_marker'])
        if base['safety_admission_allowed'] and result['device_lease_free']:
            result['state']='PASS'
        else:result['reason_codes']=[base['admission_reason_code'] if not base['safety_admission_allowed'] else 'DEVICE_LEASE_BUSY']
    except (sqlite3.Error,OSError,ValueError,TypeError,KeyError,IndexError,TimeoutError) as error:
        result['reason_codes']=[str(error) if isinstance(error,ValueError) and str(error).isupper() else type(error).__name__]
    finally:
        if ledger:ledger.close()
    return result
