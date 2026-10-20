"""Deliberately lossy allowlist export. Raw screenshots/tree/model prose stay private."""
import hashlib
import json
import uuid
from pathlib import Path


def public_task(task):
    """Never copy arbitrary strings, paths, screenshots or semantic text into public output."""
    statuses={'succeeded','uncertain','blocked','failed','step_limit','rested','exploration_observed','partially_met','interrupted','needs_review'}
    result=task.get('result') or {}
    def sha(value):
        return value if isinstance(value,str) and len(value)==64 and all(c in '0123456789abcdef' for c in value) else None
    attempts=[]
    for item in task.get('attempts',[]):
        payload=item['payload'];action=payload.get('candidate_action') or {};binding=payload.get('semantic_binding') or {}
        actual={k:action[k] for k in ('x','y','x2','y2','duration_ms') if type(action.get(k)) is int
                and 0<=action[k]<=8192 and payload.get('execution_status')=='executed'}
        if action.get('type') in {'tap','swipe','back','home','launch','finish'}: actual['type']=action['type']
        attempts.append({'step_index':item['step_index'],'generation_attempt':item['generation_attempt'],
            'response_sha256':sha(payload.get('response_sha256')),
            'executed':payload.get('execution_status')=='executed',
            'outcome_unknown':payload.get('execution_status')=='uncertain_after_transport_error',
            'observation_sha256':sha(payload.get('observation_sha256')),
            'fresh_screenshot_sha256':sha(binding.get('fresh_screenshot_sha256')),
            'binding_checked':binding.get('pre_dispatch_consistency')=='verified','action':actual})
    try: identity=str(uuid.UUID(task['task_run_id']))
    except ValueError: identity=hashlib.sha256(task['task_run_id'].encode()).hexdigest()
    return {'public_schema':'evidence/1','task_run_id':identity,
        'status':task['status'] if task['status'] in statuses else 'unclassified',
        'elapsed_ns':task.get('elapsed_ns') if type(task.get('elapsed_ns')) is int else None,
        'run_kind':task.get('run_kind') if task.get('run_kind') in {'benchmark','autonomous','manual_targeted','mock_test'} else 'unclassified',
        'goal_sha256':hashlib.sha256(task.get('goal','').encode()).hexdigest(),
        'final_observation_sha256':sha(result.get('final_observation_sha256')),
        'independently_verified':(result.get('verification') or {}).get('verdict')=='verified',
        'attempts':attempts,'raw_material_included':False,
        'scope':'Hashes and bounded action facts only; excludes raw PNG/tree/model text and private paths'}


def export_public(store, output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    manifest={}
    for row in store.conn.execute('SELECT task_run_id FROM task_runs ORDER BY rowid'):
        value=public_task(store.task(row[0]));name=value['task_run_id']+'.json'
        data=json.dumps(value,ensure_ascii=False,indent=2).encode('utf8')
        (output/name).write_bytes(data);manifest[name]=hashlib.sha256(data).hexdigest()
    (output/'SHA256.json').write_text(json.dumps(manifest,indent=2),encoding='utf8')
    return {'public_tasks':len(manifest),'raw_material_included':False}
