"""Read-only health and storage probes. No action, tap, swipe, or recovery API."""
from __future__ import annotations
import asyncio
import ctypes
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import time

def process_alive(pid):
    if not pid:return False
    if os.name=='nt':
        api=ctypes.windll.kernel32;api.OpenProcess.restype=ctypes.c_void_p
        handle=api.OpenProcess(0x1000,False,pid)
        if not handle:return False
        try:
            code=ctypes.c_ulong();return bool(api.GetExitCodeProcess(ctypes.c_void_p(handle),ctypes.byref(code))) and code.value==259
        finally:api.CloseHandle(ctypes.c_void_p(handle))
    try:os.kill(pid,0);return True
    except OSError:return False

def rss_bytes(pid):
    if not pid:return 0
    if os.name=='nt':
        class Memory(ctypes.Structure):
            _fields_=[('cb',ctypes.c_ulong),('faults',ctypes.c_ulong)]+[(n,ctypes.c_size_t) for n in ('peak','working','qpeak','q','ppeak','p','page','pagepeak')]
        api=ctypes.windll.kernel32;api.OpenProcess.restype=ctypes.c_void_p
        handle=api.OpenProcess(0x410,False,pid)
        if not handle:return 0
        try:
            m=Memory();m.cb=ctypes.sizeof(m)
            return int(m.working) if ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.c_void_p(handle),ctypes.byref(m),m.cb) else 0
        finally:api.CloseHandle(ctypes.c_void_p(handle))
    try:
        for line in Path(f'/proc/{pid}/status').read_text().splitlines():
            if line.startswith('VmRSS:'):return int(line.split()[1])*1024
    except OSError:pass
    return 0

class ReadOnlyDeviceProbe:
    ALLOWED=(('get-state',),('shell','getprop','ro.kernel.qemu'),('shell','getprop','ro.build.version.sdk'))
    def __init__(self,serial,runner=None):
        if not serial or serial.startswith('-'):raise ValueError('Explicit emulator serial required')
        self.serial=serial;self.runner=runner or self._run
    async def _run(self,args):
        if args not in self.ALLOWED:raise ValueError('Health command is not read-only allowlisted')
        p=await asyncio.create_subprocess_exec('adb','-s',self.serial,*args,
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        try:stdout,_=await asyncio.wait_for(p.communicate(),timeout=5)
        except BaseException:
            if p.returncode is None:p.kill();await p.communicate()
            raise
        if p.returncode:raise RuntimeError('ADB_READONLY_PROBE_FAILED')
        return stdout.decode('utf8','replace').strip()
    async def sample(self):
        try:
            state=await self.runner(self.ALLOWED[0])
            if state!='device':return {'online':False,'qemu':None,'reason':'DEVICE_NOT_ONLINE'}
            qemu=await self.runner(self.ALLOWED[1]);sdk=await self.runner(self.ALLOWED[2])
            return {'online':True,'qemu':qemu=='1','sdk':sdk,'reason':'OK' if qemu=='1' else 'WRONG_DEVICE_IDENTITY'}
        except (Exception,):return {'online':False,'qemu':None,'reason':'ADB_UNAVAILABLE'}

def readonly_db_health(path,lease_path,device='emulator',now=None,quick=True,run_kind='autonomous',policy=None,worker_alive=None,worker_exited=False):
    now=time.time() if now is None else now
    result={'database_readable':False,'needs_review':False,'next_wake_at':0,'operator':'running','warnings':[]}
    c=None;l=None
    try:
        c=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=.5);c.row_factory=sqlite3.Row
        c.execute('PRAGMA query_only=ON')
        result['quick_check']=c.execute('PRAGMA quick_check').fetchone()[0] if quick else 'not_sampled'
        if result['quick_check'] not in ('ok','not_sampled'):raise ValueError('DATABASE_INTEGRITY_FAILED')
        result['counts']={t:c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in ('observations','action_attempts','events','task_runs')}
        row=c.execute('SELECT mode FROM life_controls WHERE device=?',(device,)).fetchone()
        result['operator']=row[0] if row else 'running'
        row=(c.execute('SELECT next_wake_at FROM schedule WHERE device=?',(device,)).fetchone() if run_kind=='autonomous'
            else c.execute('SELECT next_wake_at FROM run_schedules WHERE device=? AND run_kind=?',(device,run_kind)).fetchone())
        result['next_wake_at']=row[0] if row else 0
        wake=result['next_wake_at']
        if not math.isfinite(wake) or wake>now+86520:result['warnings'].append('INVALID_NEXT_WAKE')
        last=c.execute('SELECT task_run_id,status,result FROM task_runs ORDER BY rowid DESC LIMIT 1').fetchone()
        result['last_task']=dict(last) if last else None
        if last:result['last_task']['result']=json.loads(last['result']) if last['result'] else {}
        attempt_rows=c.execute('SELECT attempt_id,task_run_id,payload FROM action_attempts').fetchall()
        attempts=[{**json.loads(r['payload']),'task_run_id':r['task_run_id']} for r in attempt_rows]
        statuses={r[0]:r[1] for r in c.execute('SELECT task_run_id,status FROM task_runs')}
        unknown=[a for a in attempts if a.get('execution_status') in {'dispatching','outcome_unknown','uncertain_after_transport_error'} and statuses.get(a.get('task_run_id'))!='running']
        result['unknown_dispatches']=len(unknown)
        result['needs_review']=bool(unknown or c.execute("SELECT 1 FROM task_runs WHERE status='needs_review' LIMIT 1").fetchone())
        hashes=[r[0] for r in c.execute('SELECT metadata FROM observations ORDER BY rowid DESC LIMIT 20')]
        values=[json.loads(r).get('sha256') for r in hashes]
        result['repeated_last_20_screens']=len(values)==20 and len(set(values))==1
        if result['repeated_last_20_screens']:result['warnings'].append('REPEATED_SCREEN_OBSERVED_NO_FORCED_ACTION')
        recent=c.execute("SELECT status,details FROM life_requests WHERE phase='brain' ORDER BY requested_at DESC LIMIT 3").fetchall()
        result['consecutive_brain_request_failures']=len(recent)==3 and all(r['status']=='failed_or_cancelled' for r in recent)
        result['requests']=[dict(r) for r in c.execute('SELECT phase,status,COUNT(*) AS count FROM life_requests GROUP BY phase,status')]
        result['budget_until']=0
        if policy:
            for duration,limit in ((3600,policy.hourly_requests),(86400,policy.daily_requests)):
                rows=c.execute('SELECT requested_at FROM life_requests WHERE device=? AND run_kind=? AND requested_at>? ORDER BY requested_at',(device,run_kind,now-duration)).fetchall()
                if len(rows)>=limit:result['budget_until']=max(result['budget_until'],rows[len(rows)-limit][0]+duration+1)
        result['guard_reason']=None;result['model_kind']=None
        if last:
            for r in c.execute("SELECT event_type,payload FROM events WHERE event_type IN ('life_guard_rest','life_model_choice') ORDER BY id DESC LIMIT 30"):
                item=json.loads(r['payload'])
                if item.get('task_run_id')!=last['task_run_id']:continue
                if r['event_type']=='life_guard_rest':result['guard_reason']=item.get('reason')
                else:result['model_kind']=item.get('model_decision',{}).get('kind')
        l=sqlite3.connect(Path(lease_path).resolve().as_uri()+'?mode=ro',uri=True,timeout=.5);l.row_factory=sqlite3.Row
        result['lease']=[dict(r) for r in l.execute('SELECT * FROM lease WHERE device=?',(device,))]
        live_lease=any(r['expires_at']>now for r in result['lease'])
        sources=[]
        for row,a in zip(attempt_rows,attempts):
            execution=a.get('execution_status');status=statuses.get(row['task_run_id'])
            # The executor journals uncertainty BEFORE ADB. A live in-flight call is
            # not proof of transport failure. A dead owned worker or expired owner is.
            unknown=execution in {'dispatching','outcome_unknown','uncertain_after_transport_error'}
            # A prior lease is not proof that this supervisor still owns a live
            # in-flight call. On restart, opaque pending dispatch always needs review.
            abandoned=worker_exited or worker_alive is False
            if unknown and (status!='running' or a.get('transport_error') or abandoned):
                sources.append({'task_run_id':row['task_run_id'],'attempt_id':row['attempt_id'],
                    'execution_status':execution,'original_task_status':status,'unknown_transport':True})
        covered={s['task_run_id'] for s in sources}
        sources.extend({'task_run_id':run,'attempt_id':None,'execution_status':None,
            'original_task_status':status,'unknown_transport':False} for run,status in statuses.items()
            if status=='needs_review' and run not in covered)
        result['review_sources']=sources
        result['safety_review_required']=bool(sources)
        result['dispatch_audit']=[{'attempt_id':row['attempt_id'],'task_run_id':row['task_run_id'],
            'dispatch_monotonic_ns':a['dispatch_monotonic_ns']} for row,a in zip(attempt_rows,attempts)
            if isinstance(a.get('dispatch_monotonic_ns'),int)]
        result['database_readable']=True
        return result
    except (sqlite3.Error,ValueError,TypeError) as e:
        result['error_code']=type(e).__name__;return result
    finally:
        if c:c.close()
        if l:l.close()

def resource_sample(directory,agent_db,worker_pid=None,free=None):
    directory=Path(directory);agent_db=Path(agent_db)
    sizes={p:p.stat().st_size for p in directory.rglob('*') if p.is_file()}
    audit=sum(n for p,n in sizes.items() if p.suffix in {'.jsonl','.log'} or p.name=='soak-control.sqlite3')
    return {'total_bytes':sum(sizes.values()),'audit_bytes':audit,
        'database_bytes':agent_db.stat().st_size if agent_db.exists() else 0,
        'wal_bytes':Path(str(agent_db)+'-wal').stat().st_size if Path(str(agent_db)+'-wal').exists() else 0,
        'free_bytes':shutil.disk_usage(directory).free if free is None else free,
        'supervisor_rss_bytes':rss_bytes(os.getpid()),'worker_rss_bytes':rss_bytes(worker_pid)}

def resource_violations(sample,policy):
    result=[]
    for observed,maximum in [('total_bytes','evidence_bytes'),('audit_bytes','audit_bytes'),
        ('database_bytes','database_bytes'),('wal_bytes','wal_bytes'),('worker_rss_bytes','rss_bytes'),('supervisor_rss_bytes','rss_bytes')]:
        if sample[observed]>getattr(policy,maximum):result.append(observed.upper()+'_LIMIT')
    if sample['free_bytes']<policy.min_free_bytes:result.append('DISK_FREE_BELOW_RESERVE')
    return result
