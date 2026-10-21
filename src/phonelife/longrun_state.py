"""Durable finite-session clock and policy. No Android or model operations."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid
from dataclasses import asdict,dataclass
from .models import utc_now

TERMINAL={'completed_duration','emergency_stopped','failed'}
REVIEW_STOP='UNKNOWN_DISPATCH_NEEDS_REVIEW'
STATES={'running','sleeping','budget_wait','recovering','paused','emergency_stopped',
        'interrupted','completed_duration','failed','fault_backoff','awaiting_approval','prepared'}

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def canonical(value):return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'))

def runtime_manifest():
    base=Path(__file__).parent
    return {p.relative_to(base).as_posix():sha(p) for p in sorted(base.rglob('*'))
        if p.is_file() and p.suffix in {'.py','.jar'} and '__pycache__' not in p.parts}

class Clock:
    monotonic=staticmethod(time.monotonic)
    wall=staticmethod(time.time)
    @staticmethod
    def boot_id():
        if os.name=='nt':
            import ctypes
            # SYSTEM_BOOT_ENVIRONMENT_INFORMATION starts with a per-boot GUID.
            buffer=ctypes.create_string_buffer(32)
            status=ctypes.windll.ntdll.NtQuerySystemInformation(90,buffer,32,None)
            if status!=0:raise RuntimeError('Cannot establish a trustworthy boot identity')
            return buffer.raw[:16].hex()
        p=Path('/proc/sys/kernel/random/boot_id')
        return p.read_text().strip() if p.exists() else str(round((time.time()-time.monotonic())/60))

@dataclass(frozen=True)
class SoakPolicy:
    hourly_requests:int=120
    daily_requests:int=500
    max_goal_requests:int=32
    max_actions:int=15
    task_timeout_seconds:int=300
    evidence_bytes:int=4*1024**3
    min_free_bytes:int=5*1024**3
    wal_bytes:int=128*1024**2
    database_bytes:int=512*1024**2
    audit_bytes:int=64*1024**2
    rss_bytes:int=512*1024**2
    max_restarts:int=3
    health_interval_seconds:float=5
    device_probe_seconds:float=30

    def __post_init__(self):
        caps={'hourly_requests':120,'daily_requests':500,'max_goal_requests':32,
              'max_actions':15,'task_timeout_seconds':300}
        for field,limit in caps.items():
            value=getattr(self,field)
            if type(value)is not int or not 1<=value<=limit:raise ValueError('Budget exceeds hard cap: '+field)
        for field in ('evidence_bytes','min_free_bytes','wal_bytes','database_bytes','audit_bytes','rss_bytes'):
            if type(getattr(self,field))is not int or getattr(self,field)<1:raise ValueError('Invalid storage/resource policy')
        if not 0<=self.max_restarts<=3:raise ValueError('Restart cap must be at most three')
        if not 1<=self.health_interval_seconds<=60 or not 1<=self.device_probe_seconds<=60:raise ValueError('Invalid sample interval')

class SessionLedger:
    def __init__(self,path,clock=None,readonly=False):
        self.path=Path(path);self.clock=clock or Clock();self.readonly=readonly
        self._pending_review=None;self.review_persistence={}
        if readonly:
            self.conn=sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True,timeout=1)
            self.conn.execute('PRAGMA query_only=ON')
        else:
            self.conn=sqlite3.connect(self.path,timeout=2,isolation_level=None)
            self.conn.executescript('CREATE TABLE IF NOT EXISTS session(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL);'
                'CREATE TABLE IF NOT EXISTS health_events(id INTEGER PRIMARY KEY,utc TEXT,monotonic REAL,kind TEXT,payload TEXT);'
                'CREATE TABLE IF NOT EXISTS exported_cycles(task_run_id TEXT PRIMARY KEY,payload TEXT NOT NULL);')
    def close(self):self.conn.close()
    def load(self):
        row=self.conn.execute('SELECT payload FROM session WHERE id=1').fetchone()
        if row is None:raise ValueError('Session is not initialized')
        return self._review_overlay(json.loads(row[0]))
    @property
    def review_path(self):return self.path.with_name(self.path.name+'.review-stop.json')
    def _marker_review(self,session_id):
        try:
            with self.review_path.open('r',encoding='utf8') as stream:raw=stream.read(1024*1024+1)
            if len(raw)>1024*1024:raise ValueError('Review marker exceeds read bound')
            barrier=json.loads(raw)
            if not (isinstance(barrier,dict) and barrier.get('session_id')==session_id
                    and barrier.get('reason_code')==REVIEW_STOP and barrier.get('requires_manual_review')is True):
                raise ValueError('Invalid review marker')
            return barrier
        except FileNotFoundError:return None
        except (ValueError,OSError) as error:
            return {'session_id':session_id,'reason_code':REVIEW_STOP,'requires_manual_review':True,
                'evidence_unreadable':True,'marker_read_error_type':type(error).__name__}
    def _review_overlay(self,value):
        # Either persistent medium is sufficient. SQLite's first event wins a
        # conflict; an unreadable/partial marker still blocks a fresh process.
        barrier=value.get('safety_stop')
        if not barrier and (value.get('requires_manual_review') or value.get('reason')==REVIEW_STOP):
            barrier={'session_id':value['session_id'],'reason_code':REVIEW_STOP,'requires_manual_review':True}
        barrier=barrier or self._pending_review or self._marker_review(value['session_id'])
        if barrier:
            value.update(safety_stop=barrier,requires_manual_review=True,worker_signal='stop')
            if value['operator']=='emergency_stopped' or value['state']=='emergency_stopped':
                value.update(state='emergency_stopped',reason='OPERATOR_STOP')
            else:value.update(state='failed',reason=REVIEW_STOP)
        return value
    def save(self,value):
        value=self._review_overlay(value)
        self.conn.execute('INSERT INTO session VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',(canonical(value),))
    def latch_review(self,payload):
        value=self.load()
        barrier=dict(value.get('safety_stop') or {**payload,'session_id':value['session_id'],'reason_code':REVIEW_STOP,'triggered_utc':utc_now(),
            'triggered_monotonic':self.clock.monotonic(),'requires_manual_review':True,
            'old_actions_replayed':0})
        self._pending_review=barrier # stops this controller even if BOTH writes fail
        operation='create'
        try:
            # Exclusive creation is append-once on Windows too. Never replace a
            # partial file: its very presence remains a conservative stop barrier.
            with self.review_path.open('x',encoding='utf8') as stream:
                operation='write';stream.write(canonical(barrier))
                operation='flush';stream.flush()
                operation='fsync';os.fsync(stream.fileno());operation='close'
            marker={'status':'created'}
        except FileExistsError:
            existing=self._marker_review(value['session_id'])
            marker={'status':'existing_invalid' if not existing or existing.get('evidence_unreadable') else 'existing_valid'}
        except OSError as error:
            marker={'status':'failed','operation':operation,'error_type':type(error).__name__,
                'errno':error.errno,'winerror':getattr(error,'winerror',None)}
        self.review_persistence={'marker':marker,'sqlite':{'status':'attempting'}}
        barrier={**barrier,'persistence':self.review_persistence}
        self._pending_review=barrier
        try:
            # Marker errors must NEVER skip the independent SQLite attempt.
            self.conn.execute('BEGIN IMMEDIATE')
            raw=json.loads(self.conn.execute('SELECT payload FROM session WHERE id=1').fetchone()[0])
            # Another stop writer may have committed since our initial load.
            # Re-read under the write transaction; never replace its first event.
            if raw.get('safety_stop'):
                barrier={**raw['safety_stop'],'persistence':self.review_persistence}
                self._pending_review=barrier
            now=self.clock.monotonic()
            if raw['state']not in TERMINAL and raw['started_monotonic']is not None and now>=raw['last_monotonic'] and raw['boot_id']==self.clock.boot_id():
                delta=now-raw['last_monotonic'];phase=raw['state']
                raw['phase_seconds'][phase]=raw['phase_seconds'].get(phase,0.)+delta
                raw['elapsed_seconds']=max(raw['elapsed_seconds'],now-raw['started_monotonic'])
                if raw['device_unavailable']:raw['device_unavailable_seconds']+=delta
                if raw['process_unavailable']:raw['process_unavailable_seconds']+=delta
                if phase=='sleeping' and raw['sleep_class']=='model_rest':raw['autonomous_rest_seconds']+=delta
                if phase=='sleeping' and raw['sleep_class']=='forced_rest':raw['forced_rest_seconds']+=delta
                raw.update(last_monotonic=now,last_wall=self.clock.wall())
            self.review_persistence['sqlite']={'status':'committed'}
            raw['safety_stop']=barrier
            raw=self._review_overlay(raw);self.save(raw);self.conn.execute('COMMIT')
        except BaseException as error:
            self.review_persistence['sqlite']={'status':'failed','error_type':type(error).__name__,
                'sqlite_errorcode':getattr(error,'sqlite_errorcode',None)}
            if self.conn.in_transaction:self.conn.execute('ROLLBACK')
            raise
        self.event('safety_review_stop',self.load()['safety_stop'])
        return self.load()
    @staticmethod
    def _unavailable_status(error):
        return {'state':'unknown','control_state':'unavailable','effective_state':'blocked',
            'safety_terminated':False,'persistent_barrier_present':False,'requires_manual_review':False,
            'session_terminated':False,'resume_allowed':False,'effective_resume_allowed':False,
            'worker_launch_allowed':False,'initial_start_allowed_without_approval':False,
            'formal_approval_required':None,'safety_evidence_readable':False,'safety_admission_allowed':False,
            'history_requires_manual_review':False,'admission_reason_code':'SAFETY_EVIDENCE_UNAVAILABLE',
            'admission_evidence':{'control_sqlite':'unavailable','review_marker':'not_verified',
                'agent_history':'not_verified','control_error_type':type(error).__name__}}
    @classmethod
    def read_status(cls,path):
        try:ledger=cls(path,readonly=True)
        except (sqlite3.Error,OSError,ValueError) as error:return cls._unavailable_status(error)
        try:return ledger.status()
        finally:ledger.close()
    def status(self,*,health=None,worker_alive=None):
        from .longrun_health import readonly_db_health,safety_admission,process_alive
        conn=None;deadline=time.monotonic()+.75
        try:
            # Even a writable owner reports the physical control state through RO.
            conn=sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.25)
            conn.execute('PRAGMA query_only=ON');conn.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
            row=conn.execute('SELECT payload FROM session WHERE id=1').fetchone()
            if row is None:raise ValueError('Session is not initialized')
            raw=json.loads(row[0]);value=self._review_overlay(dict(raw));cfg=raw['config']
            marker=self._marker_review(raw['session_id'])
            marker_valid=marker is not None and not marker.get('evidence_unreadable')
            sql_stop=bool(raw.get('safety_stop') or raw.get('requires_manual_review') or raw.get('reason')==REVIEW_STOP)
            if worker_alive is None:worker_alive=process_alive(raw.get('active_pid'))
            if health is None:
                health=readonly_db_health(cfg['agent_db'],cfg['lease_path'],cfg['device'],
                    run_kind='autonomous' if cfg['mode']=='api' else 'mock_test',policy=SoakPolicy(**cfg['policy']),worker_alive=worker_alive,
                    life_binding=cfg.get('physical_execution',{}).get('life_binding'),lease_key='real' if cfg.get('physical_execution') else None)
            from .longrun import verify_freeze
            config_error=None
            try:
                verify_freeze(cfg)
                if hashlib.sha256(canonical(cfg).encode()).hexdigest()!=raw['fingerprint']:config_error='CONFIG_FREEZE_MISMATCH'
            except (ValueError,OSError,KeyError) as error:config_error=str(error) if isinstance(error,ValueError) else 'CONFIG_EVIDENCE_UNAVAILABLE'
            verdict=safety_admission(value,health,control_state=raw['state'],persisted_stop=sql_stop or marker_valid,
                barrier_present=sql_stop or marker is not None,marker_state='absent' if marker is None else 'valid' if marker_valid else 'invalid_or_unreadable',
                worker_alive=worker_alive,wall=self.clock.wall(),expired=self.expired(raw),config_error=config_error)
            verdict['admission_evidence']['control_sqlite']='readable_persisted_barrier' if sql_stop else 'readable_no_persisted_barrier'
            result={**value,**verdict,'session_terminated':value['state']in TERMINAL}
            if cfg.get('physical_execution'):
                from .physical_execution import physical_session_admission
                result=physical_session_admission(value,result)
            return result
        except (sqlite3.Error,OSError,ValueError,KeyError,TypeError) as error:return self._unavailable_status(error)
        finally:
            if conn:conn.close()
    def update(self,**fields):
        if not set(fields)<={'active_pid','restarts','worker_signal','reason','last_worker_exit'}:raise ValueError('Immutable session field')
        self.conn.execute('BEGIN IMMEDIATE')
        try:
            value=self.load();value.update(fields);self.save(value);self.conn.execute('COMMIT');return value
        except BaseException:
            if self.conn.in_transaction:self.conn.execute('ROLLBACK')
            raise
    def event(self,kind,payload):
        self.conn.execute('INSERT INTO health_events(utc,monotonic,kind,payload) VALUES(?,?,?,?)',
            (utc_now(),self.clock.monotonic(),kind,canonical(payload)))
    def create(self,config,seconds):
        if self.conn.execute('SELECT 1 FROM session').fetchone():raise FileExistsError('Session already initialized')
        if not 1<=seconds<=86400:raise ValueError('Finite duration must be 1..86400 seconds')
        now=self.clock.monotonic();wall=self.clock.wall()
        fingerprint=hashlib.sha256(canonical(config).encode()).hexdigest()
        value={'session_id':str(uuid.uuid4()),'config':config,'fingerprint':fingerprint,
            'state':'awaiting_approval' if config['source_kind']=='live_api_natural_occurrence' else 'prepared','operator':'running','reason':'PREPARED','boot_id':self.clock.boot_id(),
            'started_monotonic':None,'started_wall':None,'started_utc':None,'duration_seconds':seconds,
            'last_monotonic':now,'last_wall':wall,'elapsed_seconds':0.,'phase_seconds':{},
            'device_unavailable_seconds':0.,'process_unavailable_seconds':0.,
            'autonomous_rest_seconds':0.,'forced_rest_seconds':0.,'sleep_class':None,
            'restarts':0,'active_pid':None,'worker_signal':None,'device_unavailable':False,'process_unavailable':False}
        self.save(value);self.event('session_prepared',{'fingerprint':fingerprint,'source_kind':config['source_kind']});return value
    def start(self,approval=None,*,defer_completion=False):
        self.conn.execute('BEGIN IMMEDIATE')
        try:
            value=self.load()
            if value['state'] in TERMINAL or value['operator']=='emergency_stopped':raise ValueError('Terminal session cannot resume automatically')
            if value['started_monotonic']is None and value['boot_id']!=self.clock.boot_id():raise ValueError('Prepared session boot identity changed; repeat preflight')
            if value['config']['source_kind']=='live_api_natural_occurrence' and approval!=value['fingerprint']:
                raise PermissionError('Formal 24h API soak requires explicit approval of this fingerprint')
            now=self.clock.monotonic();initial=value['started_monotonic'] is None
            if initial:
                value.update(started_monotonic=now,started_wall=self.clock.wall(),started_utc=utc_now(),
                    deadline_monotonic=now+value['duration_seconds'],last_monotonic=now,last_wall=self.clock.wall(),state='running',reason='STARTED')
                self.save(value)
            self.conn.execute('COMMIT')
        except BaseException:
            if self.conn.in_transaction:self.conn.execute('ROLLBACK')
            raise
        if not initial:
            self.tick('interrupted',process_unavailable=True,reason='SUPERVISOR_RESTART',allow_completion=not defer_completion)
        self.event('supervisor_started',{'elapsed_seconds':self.load()['elapsed_seconds']})
        return self.load()
    @staticmethod
    def deadline(value):
        # Old sessions have the same frozen operands, so derive the identical sum.
        return value.get('deadline_monotonic',value['started_monotonic']+value['duration_seconds'])
    def expired(self,value=None):
        v=value if value is not None else self.load()
        return v['started_monotonic']is not None and self.clock.monotonic()>=self.deadline(v)
    def remaining(self):
        v=self.load()
        return v['duration_seconds'] if v['started_monotonic']is None else max(0.,self.deadline(v)-self.clock.monotonic())
    def tick(self,state,*,reason='',device_unavailable=False,process_unavailable=False,sleep_class=None,allow_completion=True):
        if state not in STATES:raise ValueError('Unknown state')
        self.conn.execute('BEGIN IMMEDIATE')
        try:
            v=self.load();now=self.clock.monotonic();wall=self.clock.wall();previous=v['state']
            if previous in TERMINAL:
                self.conn.execute('COMMIT');return v
            if v['started_monotonic']is not None:
                if self.clock.boot_id()!=v['boot_id'] or now<v['last_monotonic']:
                    v.update(state='failed',reason='MONOTONIC_CLOCK_DISCONTINUITY',worker_signal='stop')
                    self.save(v);self.conn.execute('COMMIT');return v
                delta=max(0.,now-v['last_monotonic'])
                # On supervisor restart the whole unseen gap is process unavailability.
                credited='interrupted' if process_unavailable else previous
                v['phase_seconds'][credited]=v['phase_seconds'].get(credited,0.)+delta
                if v['device_unavailable']:v['device_unavailable_seconds']+=delta
                if process_unavailable or v['process_unavailable']:v['process_unavailable_seconds']+=delta
                if previous=='sleeping' and v['sleep_class']=='model_rest':v['autonomous_rest_seconds']+=delta
                if previous=='sleeping' and v['sleep_class']=='forced_rest':v['forced_rest_seconds']+=delta
                v['elapsed_seconds']=max(v['elapsed_seconds'],now-v['started_monotonic'])
            # Terminal states and emergency operator control are sticky.
            if previous in TERMINAL:state=previous
            elif v['operator']=='emergency_stopped':state='emergency_stopped'
            elif allow_completion and state!='failed' and self.expired(v):state='completed_duration'
            v.update(state=state,reason=reason or v['reason'],last_monotonic=now,last_wall=wall,
                device_unavailable=device_unavailable,process_unavailable=process_unavailable,sleep_class=sleep_class)
            if state in TERMINAL or state=='paused':v['worker_signal']='stop'
            self.save(v);self.conn.execute('COMMIT')
        except BaseException:
            if self.conn.in_transaction:self.conn.execute('ROLLBACK')
            raise
        if previous!=v['state']:self.event('state_transition',{'from':previous,'to':v['state'],'reason':v['reason']})
        return v
    def operator(self,mode):
        if mode not in {'running','paused','emergency_stopped'}:raise ValueError('Invalid operator mode')
        self.conn.execute('BEGIN IMMEDIATE')
        try:
            v=self.load()
            if mode!='emergency_stopped' and (v['state']in TERMINAL or v.get('safety_stop')):
                raise ValueError('SESSION_TERMINATED: manual review required; create a new linked finite session, never Resume this one')
            if v['operator']=='emergency_stopped' or v['state']=='emergency_stopped':
                if mode!='emergency_stopped':raise ValueError('Emergency stop cannot automatically resume this session')
            v['operator']=mode
            if mode!='running':v['worker_signal']='stop'
            self.save(v);self.conn.execute('COMMIT')
        except BaseException:
            if self.conn.in_transaction:self.conn.execute('ROLLBACK')
            raise
        self.event('operator_control',{'mode':mode})
    def cycle(self,run_id,payload):
        self.conn.execute('INSERT OR IGNORE INTO exported_cycles VALUES(?,?)',(run_id,canonical(payload)))

class SupervisorLock:
    def __init__(self,path):self.path=Path(path);self.stream=None
    def __enter__(self):
        self.stream=self.path.open('a+b');self.stream.seek(0,2)
        if self.stream.tell()==0:self.stream.write(b'0');self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name=='nt':
                import msvcrt;msvcrt.locking(self.stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl;fcntl.flock(self.stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:self.stream.close();raise RuntimeError('Session supervisor already active') from None
        return self
    def __exit__(self,*args):
        if os.name=='nt':
            import msvcrt;self.stream.seek(0);msvcrt.locking(self.stream.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl;fcntl.flock(self.stream.fileno(),fcntl.LOCK_UN)
        self.stream.close()
