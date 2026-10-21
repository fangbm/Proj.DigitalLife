"""Finite durable supervisor for model-led life. Formal API runs need approval."""
from __future__ import annotations
import argparse
import asyncio
import getpass
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import uuid
from dataclasses import asdict
from . import __version__
from .longrun_state import SessionLedger,SoakPolicy,SupervisorLock,TERMINAL,REVIEW_STOP,canonical,runtime_manifest,sha
from .longrun_health import ReadOnlyDeviceProbe,readonly_db_health,resource_sample,resource_violations,process_alive
from .models import utc_now

def secure_directory(path):
    path.mkdir(parents=True,exist_ok=False,mode=0o700)
    if os.name=='nt':
        domain=os.environ.get('USERDOMAIN','');owner=(domain+'\\' if domain else '')+getpass.getuser()
        result=subprocess.run(['icacls',str(path),'/inheritance:r','/grant:r',owner+':(OI)(CI)F'],capture_output=True,timeout=15)
        if result.returncode:raise PermissionError('Private evidence ACL restriction failed')
    else:path.chmod(0o700)
    return True

def prepare_session(directory,*,mode='mock',seconds=86400,private_evidence=False,policy=None,source_kind=None,lease_path=None,reviewed_session=None,review_reference=None):
    from .config import Config
    from .storage import Store
    from .life_storage import enable_life
    directory=Path(directory).resolve();policy=policy or SoakPolicy()
    expected='offline_fixture' if mode=='mock' else 'live_api_preflight' if seconds<=300 else 'live_api_natural_occurrence'
    if mode not in {'mock','api'} or (source_kind is not None and source_kind!=expected):raise ValueError('Natural, preflight and injected sources cannot be mixed')
    if mode=='api' and seconds>300 and seconds!=86400:raise ValueError('Formal API acceptance duration is exactly 86400 seconds')
    cfg=Config.from_env();root=Path.cwd()
    coordinator=Path(lease_path or (root/'data/phone-life.sqlite3' if mode=='api' else directory/'mock-lease.sqlite3')).resolve()
    if mode=='api' and not coordinator.is_file():raise FileNotFoundError('Existing shared coordinator required')
    link=None
    if reviewed_session is not None or review_reference is not None:
        if not reviewed_session or not review_reference or not review_reference.strip():raise ValueError('Explicit review reference and prior session required')
        prior=SessionLedger(Path(reviewed_session)/'soak-control.sqlite3',readonly=True)
        try:
            old=prior.load()
            if old['state']not in TERMINAL or not old.get('safety_stop'):raise ValueError('Prior session must remain safety terminated')
            link={'session_id':old['session_id'],'fingerprint':old['fingerprint'],'reason_code':REVIEW_STOP,
                'barrier_sha256':sha(prior.review_path) if prior.review_path.is_file() else hashlib.sha256(canonical(old['safety_stop']).encode()).hexdigest(),
                'barrier_medium':'marker' if prior.review_path.is_file() else 'sqlite','operator_review_reference':review_reference}
        finally:prior.close()
    restricted=secure_directory(directory)
    config={'version':__version__,'mode':mode,'source_kind':expected,'serial':cfg.emulator_serial if mode=='api' else 'mock',
        'device':'emulator' if mode=='api' else 'mock','lease_path':str(coordinator),'agent_db':str(directory/'agent.sqlite3'),
        'allowed_apps':['com.android.settings'],'private_evidence':bool(private_evidence),'private_acl_restricted':restricted,
        'policy':asdict(policy),'runtime_sha256':runtime_manifest(),
        'persona_path':str((root/'persona.life.example.md').resolve()),'persona_sha256':sha(root/'persona.life.example.md'),
        'launcher_path':str((root/'data/start-soak.ps1').resolve()),'launcher_sha256':sha(root/'data/start-soak.ps1'),
        'api_base':cfg.api_base,'gui_api_base':cfg.gui_api_base,'brain_model':cfg.brain_model,'gui_model':cfg.gui_model,
        'approved_formal_preflight_required':expected=='live_api_natural_occurrence'}
    if link:config['prior_reviewed_fault']=link
    if mode=='api' and (not cfg.emulator_serial or not cfg.api_base or not cfg.gui_api_base or not cfg.brain_model or not cfg.gui_model):raise ValueError('Prepare requires endpoints/models/serial, never an API key')
    store=Store(Path(config['agent_db']));enable_life(store);store.close()
    if mode=='mock':s=Store(coordinator);s.close()
    ledger=SessionLedger(directory/'soak-control.sqlite3')
    try:return ledger.create(config,seconds)
    finally:ledger.close()

def verify_freeze(config):
    if config['runtime_sha256']!=runtime_manifest():raise ValueError('RUNTIME_FREEZE_MISMATCH')
    if sha(config['persona_path'])!=config['persona_sha256']:raise ValueError('PERSONA_FREEZE_MISMATCH')
    if sha(config['launcher_path'])!=config['launcher_sha256']:raise ValueError('LAUNCHER_FREEZE_MISMATCH')
    SoakPolicy(**config['policy'])
    if config['allowed_apps']!=['com.android.settings']:raise ValueError('CAPABILITY_SCOPE_CHANGED')
    if config.get('physical_execution'):
        spec=config['physical_execution']
        if config['mode']!='api' or config['serial']!='profile-bound':raise ValueError('PHYSICAL_PROFILE_MODE_MISMATCH')
        for name in ('profile','manifest'):
            if sha(spec[name+'_path'])!=spec[name+'_sha256']:raise ValueError('PHYSICAL_'+name.upper()+'_FREEZE_MISMATCH')

def export_cycles(directory,ledger):
    """Idempotent SQLite export, then an atomic derived JSONL (never deletes source)."""
    from .storage import Store
    from .public_evidence import public_task
    directory=Path(directory);cfg=ledger.load()['config'];store=Store.open_readonly(Path(cfg['agent_db']))
    changed=False
    try:
        exported={r[0]:json.loads(r[1]) for r in ledger.conn.execute('SELECT task_run_id,payload FROM exported_cycles')}
        for row in store.conn.execute('SELECT task_run_id,status FROM task_runs ORDER BY rowid'):
            run=row[0]
            if run in exported and (exported[run]['status']!='running' or row[1]=='running'):continue
            task=store.task(run)
            cy=store.conn.execute('SELECT context,decision,reflection,transition,summary FROM life_cycles WHERE task_run_id=?',(run,)).fetchone()
            life={k:json.loads(cy[k]) if cy[k] else None for k in cy.keys()} if cy else {}
            actions=[a['payload'] for a in task['attempts'] if a['payload'].get('execution_status')=='executed']
            sem=[json.loads(r[0]) for r in store.conn.execute('SELECT payload FROM semantic_observations WHERE task_run_id=?',(run,))] if store.conn.execute("SELECT 1 FROM sqlite_master WHERE name='semantic_observations'").fetchone() else []
            request_counts=[dict(r) for r in store.conn.execute('SELECT phase,status,COUNT(*) AS count FROM life_requests WHERE task_run_id=? GROUP BY phase,status',(run,))]
            trace=[json.loads(r[0]) for r in store.conn.execute("SELECT e.payload FROM events e JOIN event_sources s ON e.id=s.event_id WHERE s.task_run_id=? AND e.event_type='longrun_api_exchange'",(run,))]
            recovery=[json.loads(r[0]) for r in store.conn.execute("SELECT e.payload FROM events e JOIN event_sources s ON e.id=s.event_id WHERE s.task_run_id=? AND e.event_type='semantic_recovery_finished'",(run,))]
            choices=[json.loads(r[0]) for r in store.conn.execute("SELECT e.payload FROM events e JOIN event_sources s ON e.id=s.event_id WHERE s.task_run_id=? AND e.event_type='life_model_choice'",(run,))]
            guards=[json.loads(r[0]) for r in store.conn.execute("SELECT e.payload FROM events e JOIN event_sources s ON e.id=s.event_id WHERE s.task_run_id=? AND e.event_type='life_guard_rest'",(run,))]
            public=public_task(task)
            record={'task_run_id':run,'source_kind':cfg['source_kind'],'status':task['status'],
                'pending_interruption_review':task['status']=='running',
                'task':public,'life':life,'confirmed_model_actions':[a['candidate_action']['type'] for a in actions],
                'activity_origin':'manual_connectivity' if task['run_kind']=='manual_targeted' else 'autonomous_cycle' if life else 'program_startup_review',
                'model_choice':choices[-1].get('model_decision') if choices else None,
                'program_forced_rest':bool(guards),'guard_reasons':[g.get('reason') for g in guards],
                'program_recovery_actions':sum(r.get('recovery_actions',0) for r in recovery),
                'api_counts':request_counts,'api_telemetry':trace,
                'semantic_qualities':{q:sum(s['evidence_quality']==q for s in sem) for q in {s['evidence_quality'] for s in sem}},
                'reason_code':(task.get('result')or{}).get('reason_code'),
                'started_wall_utc':task.get('started_wall_utc'),'ended_wall_utc':task.get('ended_wall_utc'),
                'started_monotonic_ns':task.get('started_monotonic_ns'),'elapsed_ns':task.get('elapsed_ns'),
                'next_wake_at':(task.get('result')or{}).get('next_wake_at'),
                'counterfactual_behavior_change':'not_identifiable_from_observation_alone'}
            if run in exported:
                ledger.event('pending_cycle_finalized',{'task_run_id':run,'previous_payload_sha256':hashlib.sha256(canonical(exported[run]).encode()).hexdigest()})
                ledger.conn.execute('UPDATE exported_cycles SET payload=? WHERE task_run_id=?',(canonical(record),run))
            else:ledger.cycle(run,record)
            changed=True
        target=directory/'cycles.jsonl'
        if changed or not target.exists():
            temporary=directory/'cycles.jsonl.tmp'
            with temporary.open('w',encoding='utf8') as stream:
                for r in ledger.conn.execute('SELECT payload FROM exported_cycles ORDER BY rowid'):stream.write(r[0]+'\n')
            os.replace(temporary,target)
    finally:store.close()

def wait_state(health,wall):
    if health.get('budget_until',0)>wall:return 'budget_wait',None
    if health['next_wake_at']<=wall:return None,None
    last=health.get('last_task') or {};result=last.get('result') or {};code=result.get('reason_code','')
    guard=health.get('guard_reason') or ''
    if code=='API_BUDGET_EXHAUSTED' or guard.startswith('API_BUDGET_EXHAUSTED'):return 'budget_wait',None
    if guard.startswith('FAILURE_COOLDOWN'):return 'fault_backoff',None
    if last.get('status') in {'failed','blocked','interrupted','needs_review','step_limit','uncertain'}:return 'fault_backoff',None
    return 'sleeping','forced_rest' if guard else 'model_rest' if health.get('model_kind')=='rest' else 'schedule_wait'

class LongRunSoakController:
    def __init__(self,directory,clock=None,probe=None):
        self.directory=Path(directory).resolve();self.ledger=SessionLedger(self.directory/'soak-control.sqlite3',clock)
        self.clock=self.ledger.clock;self.config=self.ledger.load()['config'];self.policy=SoakPolicy(**self.config['policy'])
        self.probe=probe or (ReadOnlyDeviceProbe(self.config['serial']) if self.config['mode']=='api' and not self.config.get('physical_execution') else None)
        self.worker=None
    def close(self):self.ledger.close()
    def _tick(self,state,**fields):
        # Only the explicit post-cancellation, post-health deadline branch may complete.
        return self.ledger.tick(state,allow_completion=state=='completed_duration',**fields)
    def _health(self,quick=False):
        return readonly_db_health(self.config['agent_db'],self.config['lease_path'],self.config['device'],quick=quick,
            run_kind='autonomous' if self.config['mode']=='api' else 'mock_test',policy=self.policy,
            worker_alive=bool(self.worker and self.worker.poll()is None),
            worker_exited=bool(self.worker and self.worker.poll()is not None),
            life_binding=self.config.get('physical_execution',{}).get('life_binding'),lease_key='real' if self.config.get('physical_execution') else None)
    async def _review_stop(self,health):
        payload={'sources':health.get('review_sources',[]),'worker_pid':self.worker.pid if self.worker else self.ledger.load().get('active_pid'),
            'worker_stop_result':'pending','lease_at_trigger':health.get('lease',[]),
            'unknown_transport':bool(health.get('unknown_dispatches') or any(s.get('unknown_transport') for s in health.get('review_sources',[]))),
            'new_actions_dispatched':None,'dispatch_check':'pending_stop_audit','trigger_monotonic_ns':time.monotonic_ns()}
        try:self.ledger.latch_review(payload)
        finally:
            stopped=await self._stop_owned_worker()
            # Reuse the original interruption classifier under the shared lease.
            # This writes review records only; it creates no worker or GUI action.
            review_error=None
            if stopped['worker_stopped']:
                from .storage import Store
                from .recovery import recover_interrupted
                leases=None;store=None;held=False;owner=str(uuid.uuid4())
                try:
                    leases=Store(Path(self.config['lease_path']))
                    if self.config.get('physical_execution'):
                        from .physical_execution import LeaseNamespace
                        leases=LeaseNamespace(leases,self.config['device'])
                    held=leases.acquire(self.config['device'],owner,30)
                    if held:
                        store=Store(Path(self.config['agent_db']));recover_interrupted(store,self.config['device'],owner)
                except Exception as error:review_error=type(error).__name__
                finally:
                    if store:store.close()
                    if held:leases.release(self.config['device'],owner)
                    if leases:getattr(leases,'store',leases).close()
            final=self._health()
            new_dispatches=[d for d in final.get('dispatch_audit',[]) if d['dispatch_monotonic_ns']>payload['trigger_monotonic_ns']]
            audit={**payload,**stopped,'session_id':self.ledger.load()['session_id'],'reason_code':REVIEW_STOP,
                'finished_utc':utc_now(),'finished_monotonic':self.clock.monotonic(),
                'lease_after_stop':final.get('lease',[]),'old_actions_replayed':0,'requires_manual_review':True,
                'new_actions_dispatched':bool(new_dispatches) if final['database_readable'] else None,
                'dispatch_check':'journal_dispatch_timestamps' if final['database_readable'] else 'unavailable',
                'new_dispatch_records':new_dispatches,'review_finalization_error':review_error,
                'stop_persistence':self.ledger.review_persistence}
            # Separate result preserves the original append-once trigger even if SQLite fails.
            target=self.directory/'review-stop-result.json'
            if not target.exists():
                with target.open('x',encoding='utf8') as stream:
                    stream.write(canonical(audit));stream.flush();os.fsync(stream.fileno())
            self.ledger.event('safety_review_worker_stopped',audit)
    async def _stop_owned_worker(self):
        signal_ok=True
        try:self.ledger.update(worker_signal='stop')
        except Exception:signal_ok=False
        escalated=False
        if self.worker and self.worker.poll()is None:
            if not signal_ok:self.worker.terminate() # only our Popen child; never another device owner
            until=time.monotonic()+(60 if signal_ok else 15)
            while self.worker.poll()is None and time.monotonic()<until:await asyncio.sleep(.05)
            if self.worker.poll()is None:
                escalated=True;self.worker.kill();until=time.monotonic()+15
                while self.worker.poll()is None and time.monotonic()<until:await asyncio.sleep(.05)
            if not signal_ok:
                # No audit-write failure may leave an unbounded child running.
                try:self.ledger.event('owned_worker_forced_stop',{'pid':self.worker.pid,'reason':'CONTROL_DB_WRITE_FAILED',
                    'lease_recovery':'Original finite TTL; preserve unknown dispatch, never replay'})
                except Exception:pass
        return {'worker_pid':self.worker.pid if self.worker else None,'worker_stopped':not self.worker or self.worker.poll()is not None,
            'worker_stop_result':'killed' if escalated else 'cancelled_or_exited' if signal_ok else 'terminated_control_db_failure',
            'worker_exit_code':self.worker.poll() if self.worker else None}
    async def run(self,approval=None):
        with SupervisorLock(self.directory/'.supervisor.lock'):
            verify_freeze(self.config)
            if hashlib.sha256(canonical(self.config).encode()).hexdigest()!=self.ledger.load()['fingerprint']:raise ValueError('CONFIG_FREEZE_MISMATCH')
            if self.ledger.load()['state']in TERMINAL:return self.ledger.status()
            next_probe=next_health=0.;device={'online':True,'qemu':True,'reason':'OFFLINE_FIXTURE'}
            consecutive_bad_db=0;worker_log=None;worker_stopping_since=None;next_launch=0.
            try:
                # Read safety evidence BEFORE start() can account an expired restart.
                initial_health=self._health(quick=True)
                admission=self.ledger.status(health=initial_health,worker_alive=bool(self.worker and self.worker.poll()is None))
                if initial_health['operator']=='stopped' or self.ledger.load()['operator']=='emergency_stopped':
                    self.ledger.operator('emergency_stopped')
                if admission['history_requires_manual_review']:
                    await self._review_stop(initial_health)
                    return self.ledger.status()
                if self.ledger.load()['operator']=='emergency_stopped':
                    self._tick('emergency_stopped',reason='OPERATOR_STOP');return self.ledger.status()
                if not admission['safety_admission_allowed']:
                    self._tick('failed',reason=admission['admission_reason_code']);return self.ledger.status(health=initial_health)
                self.ledger.start(approval,defer_completion=True)
                while True:
                    value=self.ledger.load();now=self.clock.monotonic()
                    if value['config']!=self.config or hashlib.sha256(canonical(value['config']).encode()).hexdigest()!=value['fingerprint']:
                        raise ValueError('CONFIG_FREEZE_MISMATCH')
                    if value['state']in TERMINAL:break
                    verify_freeze(self.config)
                    resource=resource_sample(self.directory,self.config['agent_db'],self.worker.pid if self.worker else None)
                    violations=resource_violations(resource,self.policy)
                    health=self._health(quick=now>=next_health)
                    admission=self.ledger.status(health=health,worker_alive=bool(self.worker and self.worker.poll()is None))
                    if now>=next_health:
                        self.ledger.event('health',{'resources':resource,'agent':health,'worker_alive':bool(self.worker and self.worker.poll()is None)})
                        next_health=now+self.policy.health_interval_seconds
                    if self.probe and now>=next_probe:
                        device=await self.probe.sample();self.ledger.event('device_health',device)
                        next_probe=now+self.policy.device_probe_seconds
                    if health['operator']=='stopped' or value['operator']=='emergency_stopped':
                        self.ledger.operator('emergency_stopped')
                    if admission['history_requires_manual_review']:
                        await self._review_stop(health);break
                    if self.ledger.load()['operator']=='emergency_stopped':
                        self._tick('emergency_stopped',reason='OPERATOR_STOP');break
                    if violations:
                        self.ledger.event('resource_stop',{'violations':violations,'resources':resource})
                        self._tick('failed',reason='RESOURCE_LIMIT');break
                    if not health['database_readable']:
                        consecutive_bad_db+=1
                        self._tick('recovering' if consecutive_bad_db<3 and self.ledger.remaining()>0 else 'failed',reason='DATABASE_UNREADABLE')
                        await asyncio.sleep(1);continue
                    consecutive_bad_db=0
                    if not admission['safety_admission_allowed']:
                        self._tick('failed',reason=admission['admission_reason_code']);break
                    if 'INVALID_NEXT_WAKE'in health['warnings']:
                        self._tick('failed',reason='INVALID_NEXT_WAKE');break
                    if device.get('qemu')is False:
                        self._tick('failed',reason='DEVICE_IDENTITY_CHANGED');break
                    if self.ledger.remaining()<=0:
                        stopped=await self._stop_owned_worker()
                        final_health=self._health(quick=True)
                        final_admission=self.ledger.status(health=final_health,worker_alive=False)
                        if final_health['operator']=='stopped' or self.ledger.load()['operator']=='emergency_stopped':
                            self.ledger.operator('emergency_stopped')
                        if final_admission['history_requires_manual_review']:
                            await self._review_stop(final_health)
                        elif final_health['operator']=='stopped' or self.ledger.load()['operator']=='emergency_stopped':
                            self.ledger.operator('emergency_stopped');self._tick('emergency_stopped',reason='OPERATOR_STOP')
                        elif not final_health['database_readable']:
                            self._tick('failed',reason='DATABASE_UNREADABLE')
                        elif not final_admission['safety_admission_allowed']:
                            self._tick('failed',reason=final_admission['admission_reason_code'])
                        elif not stopped['worker_stopped']:
                            self._tick('failed',reason='OWNED_WORKER_STOP_UNCONFIRMED')
                        else:self._tick('completed_duration',reason='GLOBAL_DEADLINE')
                        break
                    desired=None;sleep_class=None
                    if health['operator']=='paused' or value['operator']=='paused':desired='paused'
                    elif not device['online']:desired='recovering'
                    if desired:
                        self.ledger.update(worker_signal='stop')
                        self._tick(desired,reason='NEEDS_REVIEW' if health['needs_review'] else 'DEVICE_OFFLINE' if desired=='recovering' else 'OPERATOR_PAUSED',device_unavailable=not device['online'])
                    elif self.worker:
                        self._tick('running',reason='MODEL_CYCLE')
                    else:
                        desired,sleep_class=wait_state(health,self.clock.wall())
                        if desired:self._tick(desired,reason='PERSISTED_WAKE',sleep_class=sleep_class)
                        elif now<next_launch:self._tick('recovering',reason='RESTART_BACKOFF',process_unavailable=True)
                        elif any(r['expires_at']>self.clock.wall() for r in health['lease']):
                            self._tick('recovering',reason='DEVICE_LEASE_BUSY')
                        else:
                            self.ledger.update(worker_signal=None)
                            command=[sys.executable,'-u','-m','phonelife.longrun','worker','--session',str(self.directory),'--parent-pid',str(os.getpid())]
                            worker_log=(self.directory/'worker.log').open('ab')
                            self.worker=subprocess.Popen(command,stdout=worker_log,stderr=worker_log,env=dict(os.environ))
                            self.ledger.update(active_pid=self.worker.pid)
                            self.ledger.event('worker_started',{'pid':self.worker.pid,'old_actions_replayed':0})
                            self._tick('running',reason='MODEL_CYCLE')
                    if self.worker:
                        code=self.worker.poll()
                        if code is not None:
                            worker_log.close();worker_log=None;self.ledger.update(active_pid=None,last_worker_exit=code)
                            self.ledger.event('worker_exited',{'pid':self.worker.pid,'exit_code':code})
                            self.worker=None;worker_stopping_since=None
                            export_cycles(self.directory,self.ledger)
                            if code:
                                restarts=self.ledger.load()['restarts']+1;self.ledger.update(restarts=restarts)
                                self._tick('failed' if restarts>self.policy.max_restarts else 'interrupted',reason='WORKER_EXITED',process_unavailable=True)
                                next_launch=self.clock.monotonic()+30
                    await asyncio.sleep(min(1.,max(.01,self.ledger.remaining())))
            except BaseException as error:
                try:
                    self.ledger.event('supervisor_exception',{'code':getattr(error,'code',type(error).__name__)})
                    self._tick('failed' if self.ledger.remaining()<=0 else 'interrupted',reason='SUPERVISOR_INTERRUPTED',process_unavailable=True)
                except Exception:pass
                raise
            finally:
                await self._stop_owned_worker()
                if worker_log:worker_log.close()
                try:self.ledger.update(active_pid=None)
                except Exception:pass
                try:export_cycles(self.directory,self.ledger)
                except Exception as error:
                    try:self.ledger.event('cycle_export_failed',{'error_code':type(error).__name__})
                    except Exception:pass
                try:summary=self.ledger.load()
                except Exception:summary={'state':'failed','reason':'CONTROL_DB_UNAVAILABLE'}
                (self.directory/'summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf8')
            return self.ledger.status()

async def run_worker(directory,parent_pid):
    from .storage import Store
    from .life_storage import enable_life
    from .brain import APILifeBrain,MockLifeBrain,MockLifeGui
    from .autonomy import AutonomousLife
    from .budgets import LifeLimits
    from .devices import AdbPhone,MockPhone
    from .llm import APIGuiPlanner,MockPlanner
    from .loop import PhoneLife
    from .policy import ActionPolicy
    from .semantic import BoundedSemanticProvider
    from .longrun_api import SoakAPI
    directory=Path(directory).resolve();ledger=SessionLedger(directory/'soak-control.sqlite3',readonly=True)
    state=ledger.load()
    if state['started_monotonic']is None:
        ledger.close();raise PermissionError('Worker requires an explicitly started supervisor session')
    if state['state']in TERMINAL or state['operator']!='running' or state['worker_signal']=='stop' or ledger.remaining()<=0:
        ledger.close();return
    cfg=state['config'];verify_freeze(cfg)
    if hashlib.sha256(canonical(cfg).encode()).hexdigest()!=state['fingerprint']:raise ValueError('CONFIG_FREEZE_MISMATCH')
    physical=cfg.get('physical_execution')
    health=readonly_db_health(cfg['agent_db'],cfg['lease_path'],cfg['device'],worker_alive=False,
        life_binding=physical.get('life_binding') if physical else None,lease_key='real' if physical else None,policy=SoakPolicy(**cfg['policy']))
    admission=ledger.status(health=health,worker_alive=False)
    if not admission['safety_evidence_readable']:
        ledger.close();raise PermissionError('WORKER_REFUSED: safety database unreadable')
    if admission['history_requires_manual_review']:
        ledger.close();barrier=SessionLedger(directory/'soak-control.sqlite3')
        try:barrier.latch_review({'sources':health.get('review_sources',[]),'worker_pid':os.getpid(),
            'worker_stop_result':'worker_refused_before_device_creation','lease_at_trigger':health.get('lease',[]),
            'unknown_transport':bool(health.get('unknown_dispatches') or health.get('review_sources')),
            'new_actions_dispatched':False})
        finally:barrier.close()
        return
    if not admission['safety_admission_allowed']:
        ledger.close();raise PermissionError('WORKER_REFUSED: '+admission['admission_reason_code'])
    store=Store(Path(cfg['agent_db']));leases=Store(Path(cfg['lease_path']));controller=None;held=[]
    try:
        physical_contract=None;lease_adapter=leases;worker_owner=str(uuid.uuid4())
        if cfg['mode']=='api':
            if not leases.acquire('autonomous-api-writer',worker_owner,120):raise PermissionError('AUTONOMOUS_BUDGET_WRITER_BUSY')
            held.append('autonomous-api-writer')
        if physical:
            from .physical_execution import PhysicalContract,PhysicalPhone,PhysicalActionPolicy,ColorOSSemanticProvider,LeaseNamespace
            from .life_subject import attach_subject
            # Same admission as Status/Resume/supervisor, before any API/device execution adapter.
            if not admission['worker_launch_allowed']:
                raise PermissionError('PHYSICAL_WORKER_REFUSED: '+admission['admission_reason_code'])
            if state.get('active_pid')!=os.getpid():raise PermissionError('PHYSICAL_WORKER_NOT_OWNED_BY_SUPERVISOR')
            physical_contract=PhysicalContract(cfg,state).authorize()
            attach_subject(store,physical_contract.binding)
            for key in ('subject:'+physical_contract.binding.life_subject_id,'real'):
                if not leases.acquire(key,worker_owner,120):raise PermissionError('PHYSICAL_WORKER_LEASE_BUSY')
                held.append(key)
            lease_adapter=LeaseNamespace(leases,cfg['device'])
        policy=SoakPolicy(**cfg['policy'])
        limits=LifeLimits(max_actions=policy.max_actions,hourly_requests=policy.hourly_requests,daily_requests=policy.daily_requests,
            max_goal_requests=policy.max_goal_requests,task_timeout_seconds=policy.task_timeout_seconds)
        if cfg['mode']=='api':
            key=os.environ.get('PHONE_LIFE_API_KEY','');gui_key=os.environ.get('PHONE_LIFE_GUI_API_KEY',key)
            if not key or not gui_key:raise ValueError('API key absent')
            timeout=min(45,policy.task_timeout_seconds,max(.5,ledger.remaining()))
            api=SoakAPI(cfg['api_base'],key,store=store,budget=lambda:controller.budget if controller else None,source_kind=cfg['source_kind'],timeout=timeout)
            gui_api=SoakAPI(cfg['gui_api_base'],gui_key,store=store,budget=lambda:controller.budget if controller else None,source_kind=cfg['source_kind'],timeout=timeout)
            brain=APILifeBrain(api,cfg['brain_model'],Path(cfg['persona_path']).read_text(encoding='utf8'))
            gui=APIGuiPlanner(gui_api,cfg['gui_model'],frozenset(cfg['allowed_apps']))
            if physical_contract:
                phone=PhysicalPhone(physical_contract);phone.preheld_lease_owner=worker_owner;provider=ColorOSSemanticProvider()
            else:phone=AdbPhone(cfg['serial'],'emulator');provider=BoundedSemanticProvider()
        else:brain=MockLifeBrain();gui=MockLifeGui();phone=MockPhone();provider=None
        controller=AutonomousLife(store,cfg['device'],brain,limits,'autonomous' if cfg['mode']=='api' else 'mock_test')
        action_policy=PhysicalActionPolicy(physical_contract) if physical_contract else ActionPolicy('emulator' if cfg['mode']=='api' else 'mock',frozenset(cfg['allowed_apps']))
        life=PhoneLife(store,cfg['device'],phone,MockPlanner(),gui,action_policy,
            step_limit=policy.max_actions,session_timeout=policy.task_timeout_seconds,autonomy=controller,run_kind=controller.kind,
            semantic_provider=provider,evidence_dir=directory/'screens' if cfg['private_evidence'] and cfg['mode']=='api' else None,lease_store=lease_adapter)
        async def execute():
            await controller.review_startup(life)
            if store.conn.execute("SELECT 1 FROM task_runs WHERE status='needs_review' LIMIT 1").fetchone():
                return {'status':'needs_review','old_actions_replayed':0}
            if ledger.remaining()<=0:return {'status':'deadline'}
            return await life.run_once() # no --force, assigned goal, or continuation repair
        async def stop_requested():
            while ledger.remaining()>0:
                current=ledger.load()
                if current['worker_signal']=='stop' or current['operator']!='running' or not process_alive(parent_pid):return
                if held:
                    # One autonomous life/one budget writer, independent of device namespace.
                    try:
                        for key in held:leases.renew(key,worker_owner,120)
                    except Exception:return
                await asyncio.sleep(.25)
        task=asyncio.create_task(execute());watch=asyncio.create_task(stop_requested())
        done,_=await asyncio.wait((task,watch),timeout=max(.001,ledger.remaining()),return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            result=await task
        else:
            task.cancel()
            try:await task
            except asyncio.CancelledError:pass
            result={'status':'controller_cancelled','old_actions_replayed':0}
        watch.cancel();await asyncio.gather(watch,return_exceptions=True)
        store.event(cfg['device'],'longrun_worker_finished',{'source_kind':cfg['source_kind'],'status':result['status'],
            'task_run_id':result.get('task_run_id'),'parent_pid':parent_pid,'old_actions_replayed':0})
    finally:
        for key in held:leases.release(key,worker_owner)
        leases.close();store.close();ledger.close()

def control_session(directory,mode):
    from .storage import Store
    from .life_storage import control
    ledger=SessionLedger(Path(directory)/'soak-control.sqlite3')
    try:
        cfg=ledger.load()['config']
        if mode=='running':
            health=readonly_db_health(cfg['agent_db'],cfg['lease_path'],cfg['device'],
                worker_alive=process_alive(ledger.load().get('active_pid')),
                life_binding=cfg.get('physical_execution',{}).get('life_binding'),lease_key='real' if cfg.get('physical_execution') else None)
            admission=ledger.status(health=health)
            if admission['history_requires_manual_review']:
                ledger.latch_review({'sources':health.get('review_sources',[]),'worker_pid':ledger.load().get('active_pid'),
                    'worker_stop_result':'cancellation_signalled_by_resume_guard','lease_at_trigger':health.get('lease',[]),
                    'unknown_transport':bool(health.get('unknown_dispatches') or health.get('review_sources')),'new_actions_dispatched':None})
                raise ValueError('SESSION_TERMINATED: unknown dispatch requires manual review; Resume refused')
            if not admission['effective_resume_allowed']:
                raise ValueError('SESSION_TERMINATED: RESUME_REFUSED: '+admission['admission_reason_code'])
        ledger.operator(mode);store=Store(Path(cfg['agent_db']))
        try:control(store,cfg['device'],{'running':'running','paused':'paused','emergency_stopped':'stopped'}[mode],'Long-run operator request')
        finally:store.close()
    finally:ledger.close()

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('prepare','run','status','pause','resume','emergency-stop','worker'))
    parser.add_argument('--session',required=True,type=Path)
    parser.add_argument('--mode',choices=('mock','api'),default='mock')
    parser.add_argument('--seconds',type=float,default=86400)
    parser.add_argument('--private-evidence',action='store_true')
    parser.add_argument('--approve-fingerprint')
    parser.add_argument('--parent-pid',type=int,default=0)
    parser.add_argument('--hourly-limit',type=int,default=120)
    parser.add_argument('--daily-limit',type=int,default=500)
    parser.add_argument('--goal-request-limit',type=int,default=32)
    parser.add_argument('--action-limit',type=int,default=15)
    parser.add_argument('--task-timeout',type=int,default=300)
    parser.add_argument('--reviewed-session',type=Path)
    parser.add_argument('--review-reference')
    a=parser.parse_args(argv)
    if a.command=='prepare':result=prepare_session(a.session,mode=a.mode,seconds=a.seconds,private_evidence=a.private_evidence,
        policy=SoakPolicy(hourly_requests=a.hourly_limit,daily_requests=a.daily_limit,
            max_goal_requests=a.goal_request_limit,max_actions=a.action_limit,task_timeout_seconds=a.task_timeout),
        reviewed_session=a.reviewed_session,review_reference=a.review_reference)
    elif a.command=='status':
        result=SessionLedger.read_status(a.session/'soak-control.sqlite3')
    elif a.command in {'pause','resume','emergency-stop'}:
        control_session(a.session,{'pause':'paused','resume':'running','emergency-stop':'emergency_stopped'}[a.command]);result={'operator_request':a.command}
    elif a.command=='worker':asyncio.run(run_worker(a.session,a.parent_pid));return 0
    else:
        controller=LongRunSoakController(a.session)
        try:result=asyncio.run(controller.run(a.approve_fingerprint))
        finally:controller.close()
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0

if __name__=='__main__':raise SystemExit(main())
