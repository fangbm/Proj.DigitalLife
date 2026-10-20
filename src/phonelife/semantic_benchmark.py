"""Explicit multi-page model-driven trials; setup/execution/independent audit share the lease."""
import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from .benchmark import reset_settings, source_manifest
from .models import utc_now
from .verification_registry import GoalVerifierRegistry

PAGES=('Battery','Storage','System','About emulated device')


async def benchmark_pages(life,count,output):
    if not life.semantic_provider or life.phone.kind!='emulator' or not 1<=count<=100:
        raise ValueError('Semantic Benchmark requires a bounded emulator session with semantic evidence')
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    metadata={'version':'0.2.3','started_wall_utc':utc_now(),'count':count,'pages':PAGES,
        'source_sha256':source_manifest(),'model':life.gui.model,'provider':life.gui.api.provider,
        'setup':'Settings main through leased reset_settings','unattended_acceptance':False}
    (output/'metadata.json').write_text(json.dumps(metadata,indent=2),encoding='utf8')
    rows=[];original=life.verifier
    try:
        with (output/'trials.jsonl').open('x',encoding='utf8') as stream:
            for index in range(count):
                title=PAGES[index%len(PAGES)]
                candidate=SimpleNamespace(description='Open the '+title+' page',
                    verification={'kind':'page','package':'com.android.settings','title':title})
                registry=GoalVerifierRegistry(life.phone,semantic_provider=life.semantic_provider)
                life.verifier=registry.bind(candidate,require_consistency=True)
                async def audit(result,final):
                    # New verifier instance and fresh tree, no executor finish/outcome dependency.
                    verifier=GoalVerifierRegistry(life.phone,semantic_provider=life.semantic_provider).bind(candidate,True)
                    verification=await verifier.verify(candidate.description,final,[])
                    return {'verification':asdict(verification),'foreground':await life.phone.foreground()}
                result=await life.execute_goal(candidate.description,prepare=lambda:reset_settings(life.phone),
                    run_kind='benchmark',post_audit=audit)
                task=life.store.task(result.task_run_id) if result.task_run_id else None
                attempts=[a['payload'] for a in task['attempts']] if task else []
                actual=[a['candidate_action'] for a in attempts if a.get('execution_status')=='executed']
                independent=(result.post_audit or {}).get('verification',{})
                row={'trial':index+1,'page':title,'task_run_id':result.task_run_id,'status':result.status,
                    'reason_code':result.reason_code,'summary':result.summary,'actual_actions':actual,
                    'tap_count':sum(a['type']=='tap' for a in actual),'swipe_count':sum(a['type']=='swipe' for a in actual),
                    'api_calls':len(attempts),'uncertain_dispatches':sum(a.get('execution_status')=='uncertain_after_transport_error' for a in attempts),
                    'elapsed_monotonic_ns':task['elapsed_ns'] if task else 0,'primary_verification':asdict(result.verification) if result.verification else None,
                    'independent_audit':independent,'final_observation_sha256':result.final_observation_sha256,
                    'audit_observation_sha256':(result.post_audit or {}).get('observation_sha256'),
                    'false_success':(result.post_audit or {}).get('primary_status')=='succeeded' and independent.get('verdict')!='verified',
                    'confirmed_bound_actions':all(a.get('semantic_binding',{}).get('pre_dispatch_consistency')=='verified'
                        for a in attempts if a.get('execution_status')=='executed')}
                rows.append(row);stream.write(json.dumps(row,ensure_ascii=False)+'\n');stream.flush()
                if task:
                    (output/f'trial-{index+1:02d}-PRIVATE.json').write_text(json.dumps(task,ensure_ascii=False,indent=2),encoding='utf8')
                print(json.dumps(row,ensure_ascii=False),flush=True)
    finally: life.verifier=original
    success=sum(r['status']=='succeeded' and r['independent_audit'].get('verdict')=='verified' for r in rows)
    summary={'count':len(rows),'verified_successes':success,'success_rate':success/count,
        'false_successes':sum(r['false_success'] for r in rows),
        'tap_count':sum(r['tap_count'] for r in rows),'swipe_count':sum(r['swipe_count'] for r in rows),
        'uncertain_dispatches':sum(r['uncertain_dispatches'] for r in rows),'api_calls':sum(r['api_calls'] for r in rows),
        'all_dispatched_actions_bound':all(r['confirmed_bound_actions'] for r in rows),
        'passed_gate':count>=20 and success/count>=.9 and not any(r['false_success'] for r in rows),
        'scope':'Fixed goals only; independent autonomous acceptance required separately'}
    (output/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf8')
    return summary
