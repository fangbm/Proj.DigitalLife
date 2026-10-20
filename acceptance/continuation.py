"""Finite, opt-in real transport fault probe. Frozen phonelife code is unchanged.

Run through run-continuation.ps1. No flag/environment setting enables this in the
normal CLI. One source cycle replaces two already-budgeted provider responses;
a separate successor process has injection disabled and makes its own decisions.
"""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import uuid
from dataclasses import replace
from pathlib import Path

from phonelife.llm import ChatAPI

ROOT=Path(__file__).resolve().parents[1]
SOURCE='live_api_controlled_injection'
FIELDS=('reason_code','field','expected','source_task_run_id','candidate_sha256','executed','confirmed_actions')
CODE='MISSING_CONTINUATION_REFERENCE'

def digest(value):
    return hashlib.sha256(value if isinstance(value,bytes) else value.encode('utf8')).hexdigest()

def dump(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8')

def missing_candidate(context):
    ident=str(uuid.uuid4())
    return json.dumps({'kind':'continue','candidates':[{'goal_id':ident,
        'description':'Controlled invalid continuation candidate; not an execution instruction',
        'motivation':'Acceptance-only response substitution','expected_value':.5,'novelty':.5,
        'estimated_cost':1,'risk_class':'low','continuation_of':None,'created_at':context['now'],
        'verification':{'kind':'observe'}}],'selected_goal_id':ident,
        'reason':'Acceptance-only missing reference fault','wake_after_seconds':30},ensure_ascii=False)

def error_projection(context):
    """Only typed internal error facts; never persist complete outgoing prompts."""
    result=[]
    for episode in context.get('recent_episodes',[])+context.get('recent_failures',[]):
        for item in episode.get('body',{}).get('objective',{}).get('decision_rejections',[]):
            if item.get('reason_code')!=CODE:continue
            try:uuid.UUID(item['source_task_run_id'])
            except (ValueError,KeyError,TypeError):continue
            if not re.fullmatch('[0-9a-f]{64}',str(item.get('candidate_sha256',''))):continue
            if item.get('field')!='continuation_of' or not isinstance(item.get('expected'),str):continue
            if item.get('executed') is not False or type(item.get('confirmed_actions')) is not int:continue
            if item['confirmed_actions']!=0:continue
            projected={k:item[k] for k in FIELDS}
            projected['expected']=projected['expected'][:500]
            projected['memory_id']=episode.get('memory_id')
            if projected not in result:result.append(projected)
    return result[:32]

class ProbeChatAPI(ChatAPI):
    """Audits the actual ChatAPI request boundary; no independent network retries."""
    def __init__(self,*args,store,run_id,inject=False,**kwargs):
        super().__init__(*args,**kwargs)
        self.store,self.run_id=store,run_id
        self.inject=bool(inject)
        self.injection_run=None
        self.replacements=0
        self._active=False
        self._wire=None
        if self.inject and store.conn.execute("SELECT 1 FROM events WHERE event_type='acceptance_api_exchange' AND payload LIKE '%\"substituted\": true%' LIMIT 1").fetchone():
            raise ValueError('Injection already consumed in this database')

    def _request(self,payload):
        # This is the exact JSON serialization used by frozen ChatAPI._request.
        body=json.dumps(payload,ensure_ascii=False,allow_nan=False).encode('utf8')
        user=payload['messages'][1]['content']
        if isinstance(user,list):user=user[0]['text']
        self._wire={'request_payload_sha256':digest(body),'request_user_sha256':digest(user),
            'request_system_sha256':digest(payload['messages'][0]['content']),
            'image_included':isinstance(payload['messages'][1]['content'],list)}
        return super()._request(payload)

    async def generate(self,model,system,user,image=None,temperature=.2):
        if self._active:raise RuntimeError('Probe requires one bounded request at a time')
        run=self.run_id()
        row=self.store.conn.execute("SELECT request_id,phase FROM life_requests WHERE task_run_id=? AND status='reserved' ORDER BY requested_at DESC LIMIT 1",(run,)).fetchone()
        if not row:raise RuntimeError('Probe requires existing request budget reservation')
        phase=row['phase'];parsed=json.loads(user) if phase=='brain' else {}
        context=parsed.get('life_context',{})
        if phase=='brain' and context.get('device',{}).get('task_run_id')!=run:
            raise RuntimeError('Request and TaskRun association mismatch')
        event={'task_run_id':run,'request_id':row['request_id'],'phase':phase,
            'experiment_kind':SOURCE,'response_origin':'actual_provider','substituted':False,
            'injection_enabled':self.inject,'context_errors':error_projection(context),
            'life_context_sha256':digest(json.dumps(context,sort_keys=True,ensure_ascii=False)) if context else None,
            'repair':parsed.get('repair',False),'started_monotonic_ns':time.monotonic_ns()}
        self._active=True;self._wire=None
        try:
            response=await super().generate(model,system,user,image,temperature)
            event.update(self._wire or {})
            if event.get('request_user_sha256')!=digest(user):raise RuntimeError('Wire input hash mismatch')
            event.update(status='received',provider_response_sha256=digest(response.content),
                provider_request_id_sha256=digest(response.api_request_id),provider_finish_reason=response.finish_reason)
            if self.inject and phase=='brain':
                if self.injection_run is None:self.injection_run=run
                if self.injection_run!=run:raise RuntimeError('Injection may affect exactly one source cycle')
                if self.replacements>=2:raise RuntimeError('Existing two-request bound exceeded')
                response=replace(response,content=missing_candidate(context),finish_reason='stop')
                self.replacements+=1
                event.update(substituted=True,response_origin='controlled_substitution',injection_attempt=self.replacements)
                if self.replacements==2:self.inject=False
            event['returned_candidate_sha256']=digest(response.content)
            return response
        except BaseException as error:
            event.update(self._wire or {})
            event.update(status='failed_or_cancelled',error_code=getattr(error,'code',type(error).__name__))
            raise
        finally:
            self._active=False
            event['finished_monotonic_ns']=time.monotonic_ns()
            self.store.event('emulator','acceptance_api_exchange',event)

def frozen_check():
    frozen=json.loads((ROOT/'data/phase122/final-runtime-sha256.json').read_text(encoding='utf8'))
    assert all(digest((ROOT/p).read_bytes())==h for p,h in frozen.items()),'Frozen runtime changed'
    return frozen

def preflight(out):
    frozen=frozen_check()
    out.mkdir(parents=True,exist_ok=False)
    # Verify the completed original private evidence before adding this new cohort.
    old_manifest=ROOT/'data/phase122/PRIVATE-SHA256.json'
    prior=json.loads(old_manifest.read_text(encoding='utf8'))['files']
    assert all(digest((ROOT/p).read_bytes())==v['sha256'] for p,v in prior.items()),'Old private evidence changed'
    backups=[]
    for name in ('data/phone-life.sqlite3','data/phase122/autonomy.sqlite3'):
        original=ROOT/name;target=out/('backup-'+original.parent.name+'-'+original.name)
        source=sqlite3.connect(original.resolve().as_uri()+'?mode=ro',uri=True)
        if 'phone-life' in name:assert source.execute('SELECT COUNT(*) FROM lease').fetchone()[0]==0,'DEVICE_BUSY'
        dest=sqlite3.connect(target);source.backup(dest);dest.close();source.close()
        backups.append({'original':name,'backup':target.name,'sha256':digest(target.read_bytes())})
    tests={p.relative_to(ROOT).as_posix():digest(p.read_bytes()) for p in (ROOT/'tests').rglob('*')
        if p.is_file() and '__pycache__' not in p.parts and p.name!='test_continuation_acceptance.py'}
    tools={p.relative_to(ROOT).as_posix():digest(p.read_bytes()) for p in (ROOT/'acceptance').glob('*') if p.is_file()}
    dump(out/'preflight.json',{'frozen_runtime':frozen,'acceptance_sources':tools,'old_198_test_files':tests,
        'old_private_files_verified':len(prior),'old_manifest_sha256':digest(old_manifest.read_bytes()),
        'dpapi_ciphertext_sha256':digest((ROOT/'data/deepseek.key.dpapi').read_bytes()),
        'persona_sha256':digest((ROOT/'persona.life.example.md').read_bytes()),'backups':backups,
        'scope':'Two finite cycles, new DB, same coordinator; forced wake only, no assigned next goal'})

async def worker(out,stage):
    from phonelife.config import Config
    from phonelife.storage import Store
    from phonelife.brain import APILifeBrain
    from phonelife.autonomy import AutonomousLife
    from phonelife.budgets import LifeLimits
    from phonelife.devices import AdbPhone
    from phonelife.loop import PhoneLife
    from phonelife.llm import APIGuiPlanner,MockPlanner
    from phonelife.policy import ActionPolicy
    from phonelife.semantic import BoundedSemanticProvider
    from phonelife.public_evidence import public_task
    frozen_check()
    cfg=Config.from_env();cfg.require_api()
    if cfg.api_base.rstrip('/')!='https://api.deepseek.com':raise ValueError('Expected existing DeepSeek API')
    db=out/'state.sqlite3'
    opened_sha=digest(db.read_bytes()) if db.is_file() else None
    if stage=='source' and db.exists():raise FileExistsError('Source DB must be new')
    if stage=='successor' and not db.is_file():raise FileNotFoundError('Restart requires source DB')
    store=Store(db);leases=Store(ROOT/'data/phone-life.sqlite3')
    started=time.monotonic();controller=None;summary={}
    try:
        brain_api=ProbeChatAPI(cfg.api_base,cfg.api_key,store=store,run_id=lambda:controller.budget.run_id,
            inject=stage=='source',timeout=45)
        persona=(ROOT/'persona.life.example.md').read_text(encoding='utf8')
        brain=APILifeBrain(brain_api,cfg.brain_model,persona)
        controller=AutonomousLife(store,'emulator',brain,LifeLimits(max_actions=15),run_kind='autonomous')
        gui=APIGuiPlanner(ChatAPI(cfg.gui_api_base,cfg.gui_api_key,timeout=45),cfg.gui_model,cfg.allowed_apps)
        life=PhoneLife(store,'emulator',AdbPhone(cfg.emulator_serial,'emulator'),MockPlanner(),gui,
            ActionPolicy('emulator',cfg.allowed_apps),autonomy=controller,run_kind='autonomous',
            lease_store=leases,semantic_provider=BoundedSemanticProvider(),
            evidence_dir=out/'private-screens',session_timeout=300,step_limit=15)
        result=await asyncio.wait_for(life.run_once(force=True),timeout=310)
        run=result.get('task_run_id')
        summary={'stage':stage,'process_id':os.getpid(),'task_run_id':run,'status':result['status'],
            'db_open_sha256':opened_sha,
            'reason_code':result.get('reason_code'),'injection_enabled_at_start':stage=='source',
            'substitutions':brain_api.replacements,'force_wake':True,'assigned_goal':False}
        if run:
            task=store.task(run)
            public=out/'public';public.mkdir(exist_ok=True)
            dump(public/(stage+'-task.json'),public_task(task))
            # Private TaskRun is still filtered, never a complete API prompt.
            dump(out/(stage+'-PRIVATE-task.json'),task)
    except BaseException as error:
        summary={'stage':stage,'process_id':os.getpid(),'status':'probe_exception',
            'error_code':getattr(error,'code',type(error).__name__),
            'task_run_id':getattr(controller,'last_run_id',None)}
        raise
    finally:
        summary['elapsed_monotonic_seconds']=time.monotonic()-started
        leases.close();store.close()
        summary.update(sqlite_connections_closed=True,db_closed_sha256=digest(db.read_bytes()))
        dump(out/(stage+'-worker.json'),summary)
    return summary

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--live',action='store_true')
    p.add_argument('--inject-missing-continuation',action='store_true')
    p.add_argument('--output',type=Path)
    p.add_argument('--worker',choices=('source','successor'),help=argparse.SUPPRESS)
    args=p.parse_args(argv)
    if not args.live:
        print(json.dumps({'enabled':False,'network_calls':0,'device_created':False}));return 0
    if not args.output:p.error('Live acceptance requires a NEW output directory')
    if args.worker:
        if (args.worker=='source')!=args.inject_missing_continuation:p.error('Injection flag must be source-only')
        asyncio.run(worker(args.output,args.worker));return 0
    if not args.inject_missing_continuation:p.error('Explicit --inject-missing-continuation required for this probe')
    out=args.output.resolve();preflight(out)
    codes={}
    for stage in ('source','successor'):
        command=[sys.executable,'-u','-m','acceptance.continuation','--live','--output',str(out),'--worker',stage]
        if stage=='source':command.append('--inject-missing-continuation')
        with (out/(stage+'-process.log')).open('wb') as log:
            process=subprocess.run(command,cwd=ROOT,env=dict(os.environ),stdout=log,stderr=log,timeout=380)
        codes[stage]=process.returncode
        dump(out/'process-results.json',codes)
        if process.returncode:break
    print(json.dumps({'output':str(out),'worker_exit_codes':codes}));return int(any(codes.values()))

if __name__=='__main__':raise SystemExit(main())
