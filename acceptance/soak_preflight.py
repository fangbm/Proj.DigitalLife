"""Explicit few-minute live preflight only; never starts formal 24h acceptance."""
import argparse
import asyncio
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid
from phonelife.longrun import prepare_session,LongRunSoakController,export_cycles
from phonelife.longrun_state import SoakPolicy,runtime_manifest,sha,SessionLedger
from phonelife.longrun_health import ReadOnlyDeviceProbe,resource_sample,resource_violations
from phonelife.config import Config
from phonelife.storage import Store
from phonelife.models import utc_now

ROOT=Path(__file__).resolve().parents[1]

async def gui_connectivity(directory,config):
    """One non-executing GUI transport probe only when autonomy chose no GUI work."""
    from phonelife.llm import ChatAPI,GUI_SYSTEM
    from phonelife.devices import AdbPhone
    from phonelife.budgets import RequestBudget,MeteredAPI,LifeLimits
    cfg=Config.from_env();store=Store(directory/'agent.sqlite3');leases=Store(Path(config['lease_path']))
    owner=str(uuid.uuid4());held=False;started=False
    try:
        used=store.conn.execute('SELECT COUNT(*) FROM life_requests').fetchone()[0]
        if used>=min(config['policy']['hourly_requests'],config['policy']['daily_requests']):raise RuntimeError('No remaining readiness request allowance')
        held=leases.acquire('emulator',owner,120)
        if not held:raise RuntimeError('DEVICE_BUSY')
        store.start_task(owner,'emulator','Transport readiness only; no model action is executed',
            {'mode':'manual_connectivity_only','source_kind':'live_api_preflight','no_adb_actions':True},run_kind='manual_targeted');started=True
        obs=await AdbPhone(cfg.emulator_serial,'emulator').observe()
        budget=RequestBudget(store,'emulator',LifeLimits(),'manual_targeted');budget.run_id=owner
        api=MeteredAPI(ChatAPI(cfg.gui_api_base,cfg.gui_api_key,timeout=45),budget,'gui')
        response=await api.generate(cfg.gui_model,GUI_SYSTEM,
            json.dumps({'goal':'Preflight transport readiness only; proposal will never execute','screen':obs.metadata(),
                'previous_executed_actions':[]}),obs.png,temperature=.1)
        result={'status':'rested','reason_code':'API_TRANSPORT_READINESS','task_run_id':owner,'steps':0,
            'verification':None,'final_observation_sha256':obs.sha256,'no_device_action_executed':True}
        store.finish_task(owner,result,time.time(),'Manual connectivity probe; not model autonomy or business success')
        return {'passed':True,'source_kind':'live_api_preflight','manual_connectivity_only':True,'request_count':1,
            'response_sha256':hashlib.sha256(response.content.encode()).hexdigest(),'finish_reason':response.finish_reason,
            'gui_actions_sent':0,'task_run_id':owner}
    except BaseException as error:
        if started and store.task(owner)['status']=='running':
            store.finish_task(owner,{'status':'failed','reason_code':getattr(error,'code',type(error).__name__),'task_run_id':owner,'steps':0},time.time()+600,'Readiness failed; no GUI action executed')
        raise
    finally:
        if held:leases.release('emulator',owner)
        leases.close();store.close()

async def main_async(output,seconds,tests_log):
    output=output.resolve();output.mkdir(exist_ok=False)
    cfg=Config.from_env();cfg.require_api();policy=SoakPolicy(health_interval_seconds=30)
    text=tests_log.read_text(encoding='utf8');match=re.search(r'Ran (\d+) tests',text)
    if not match or int(match[1])<212 or '\nOK\n' not in text:raise RuntimeError('Full offline regression required before live preflight')
    started=time.monotonic();initial_sources=runtime_manifest()
    identity=await ReadOnlyDeviceProbe(cfg.emulator_serial).sample()
    if not identity['online'] or not identity['qemu'] or identity.get('sdk')!='35':raise RuntimeError('Original API35 QEMU identity required')
    shared=ROOT/'data/phone-life.sqlite3';lease=Store.open_readonly(shared)
    try:
        if lease.conn.execute('SELECT COUNT(*) FROM lease').fetchone()[0]:raise RuntimeError('DEVICE_BUSY')
    finally:lease.close()
    if resource_violations(resource_sample(output,output/'absent.db'),policy):raise RuntimeError('Insufficient storage/resource budget')
    path=output/'live-session'
    prepare_session(path,mode='api',seconds=seconds,private_evidence=True,policy=policy)
    controller=LongRunSoakController(path)
    try:summary=await controller.run()
    finally:controller.close()
    store=Store.open_readonly(path/'agent.sqlite3')
    try:
        counts={r[0]:r[1] for r in store.conn.execute("SELECT phase,COUNT(*) FROM life_requests WHERE status='received' GROUP BY phase")}
    finally:store.close()
    manual=None
    if summary['state']=='completed_duration' and not summary.get('requires_manual_review') and counts.get('brain',0)>0 and counts.get('gui',0)==0:
        manual=await gui_connectivity(path,summary['config'])
        ledger=SessionLedger(path/'soak-control.sqlite3')
        try:export_cycles(path,ledger)
        finally:ledger.close()
    if initial_sources!=runtime_manifest():raise RuntimeError('Source changed during live preflight')
    formal=output/'formal-24h-candidate'
    prepared=prepare_session(formal,mode='api',seconds=86400,private_evidence=True,policy=policy)
    readonly=Store.open_readonly(path/'agent.sqlite3');coordinator=Store.open_readonly(shared)
    try:
        integrity=readonly.conn.execute('PRAGMA quick_check').fetchone()[0]
        lease_rows=coordinator.conn.execute('SELECT COUNT(*) FROM lease').fetchone()[0]
        api_records=[dict(r) for r in readonly.conn.execute('SELECT phase,status,COUNT(*) AS count FROM life_requests GROUP BY phase,status')]
        running=readonly.conn.execute("SELECT COUNT(*) FROM task_runs WHERE status='running'").fetchone()[0]
        brain_ok=counts.get('brain',0)>0;gui_ok=counts.get('gui',0)>0 or bool(manual and manual['passed'])
        preflight={'version':'0.2.3','stage':'C','formal_soak_started':False,'approval_required':True,
            'ready_for_review':all((brain_ok,gui_ok,integrity=='ok',lease_rows==0,running==0,summary['state']=='completed_duration')),
            'started_utc':summary['started_utc'],'finished_utc':utc_now(),'measured_preflight_seconds':time.monotonic()-started,
            'source_kind':'live_api_preflight','formal_source_kind':'live_api_natural_occurrence',
            'tests_passed':int(match[1]),'tests_log_sha256':sha(tests_log),'device':{'serial':cfg.emulator_serial,**identity},
            'frozen_runtime_sha256':initial_sources,'formal_session':str(formal),
            'preflight_entry_sha256':sha(Path(__file__)),
            'health_sampling_assumption':'Full health/quick_check journal every 30s; resource/operator/worker checks each second; lower long-run audit I/O without changing API or action caps',
            'approval_fingerprint':prepared['fingerprint'],'budget':asdict(policy),'expected_max_requests_in_24h':500,
            'estimated_currency_cost':None,'cost_assumptions':'No price configured; request counts are not money; provider token/cache usage retained when returned',
            'private_evidence_explicit':True,'private_directory_acl_restricted':True,
            'api':{'brain_reachable':brain_ok,'gui_reachable':gui_ok,'records':api_records,'manual_gui_connectivity':manual},
            'database_quick_check':integrity,'shared_lease_rows_at_end':lease_rows,'running_tasks_at_end':running,
            'preflight_agent_state':summary['state'],'resource_sample':resource_sample(path,path/'agent.sqlite3'),
            'formal_acceptance_matrix':{k:'NOT OBSERVED' for k in ('Duration','Safety','Truthfulness','Integrity','Resource','Recovery','Autonomy','Memory','Operational')},
            'stop_conditions':['global monotonic deadline','operator EmergencyStop','unknown dispatch needs review','disk/WAL/RSS/audit hard limit',
                'QEMU identity loss','runtime/config/persona freeze mismatch','persistent DB failure','bounded worker restart cap'],
            'limits':'Short preflight is not 24h acceptance. Natural fault coverage remains NOT OBSERVED. No GUI goals or shortened rests supplied.'}
        (output/'SOAK_PREFLIGHT.json').write_text(json.dumps(preflight,ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps({'ready_for_review':preflight['ready_for_review'],'formal_soak_started':False,
            'tests':preflight['tests_passed'],'api_brain':brain_ok,'api_gui':gui_ok,'approval_fingerprint':prepared['fingerprint']}))
        return preflight
    finally:readonly.close();coordinator.close()

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--live',action='store_true')
    p.add_argument('--output',type=Path);p.add_argument('--seconds',type=float,default=120)
    p.add_argument('--tests-log',type=Path,default=ROOT/'data/phase1222/full-regression-01.log')
    a=p.parse_args()
    if not a.live:print(json.dumps({'enabled':False,'formal_soak_started':False}))
    elif not a.output or not 1<=a.seconds<=300:p.error('Explicit live output and at most five minutes required')
    else:asyncio.run(main_async(a.output,a.seconds,a.tests_log))
