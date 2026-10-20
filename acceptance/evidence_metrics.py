"""Allowlisted metrics derived read-only from original journals; no outcome rewrites."""
import collections,hashlib,json,sqlite3
from pathlib import Path
from phonelife.storage import Store
from phonelife.public_evidence import public_task
from phonelife.semantic_stability import is_about_page
from types import SimpleNamespace

CODES={'MISSING_CONTINUATION_REFERENCE','INVALID_CONTINUATION_REFERENCE','CONTINUATION_BUDGET_EXCEEDED',
       'CONTINUATION_CONTRACT_CHANGED','INVALID_LIFE_DECISION_SCHEMA','MODEL_OUTPUT_INVALID'}

def inspect(path):
 store=Store.open_readonly(path);c=store.conn
 tasks=[store.task(r[0]) for r in c.execute('SELECT task_run_id FROM task_runs ORDER BY rowid')]
 tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
 attempts=[a['payload'] for t in tasks for a in t['attempts']]
 executed=[a for a in attempts if a.get('execution_status')=='executed']
 unsafe=[];unknown=[]
 for a in executed:
  b=a.get('semantic_binding') or {};target=b.get('target');action=a.get('candidate_action') or {}
  valid=a.get('validation_status')=='valid' and b.get('pre_dispatch_consistency')=='verified'
  if action.get('type') in {'tap','swipe'}:
   valid &= bool(target) and target.get('enabled') is True and target.get('visible') is True
   valid &= bool(target) and all(target.get(k) is False for k in ('checkable','editable','password','long_clickable'))
  if not valid:unsafe.append(a.get('observation_id'))
  if a.get('execution_status')=='uncertain_after_transport_error':unknown.append(a.get('observation_id'))
 sem=[json.loads(r[0]) for r in c.execute('SELECT payload FROM semantic_observations')] if 'semantic_observations' in tables else []
 events=[{'event_type':r[0],'payload':json.loads(r[1])} for r in c.execute('SELECT event_type,payload FROM events')]
 additional=[e['payload']['assessment'] for e in events if e['event_type']=='additional_semantic_collection']
 recoveries=[e['payload'] for e in events if e['event_type']=='semantic_recovery_finished']
 recovery_dispatches=[e['payload'] for e in events if e['event_type']=='semantic_recovery_dispatch']
 for d in recovery_dispatches:
  if d.get('action',{}).get('type')!='launch' or d.get('action',{}).get('package')!='com.android.settings':unsafe.append(d.get('task_run_id'))
 unconfirmed_recovery=sum(not any(r.get('task_run_id')==d.get('task_run_id') and r.get('recovery_actions')==1 for r in recoveries) for d in recovery_dispatches)
 cycles=[]
 if 'life_cycles' in tables:
  for row in c.execute('SELECT * FROM life_cycles ORDER BY rowid'):
   cy=dict(row)
   for k in ('context','summary','decision','reflection'):cy[k]=json.loads(cy[k]) if cy[k] else None
   cycles.append(cy)
 false_success=[]
 unverified_final=[];unconfirmed_primary=[];contradicted_primary=[]
 for t in tasks:
  result=t.get('result') or {};v=result.get('verification') or {}
  post=result.get('post_audit') or {}
  if t['status']=='succeeded' and (v.get('verdict')!='verified' or v.get('observation_sha256')!=result.get('final_observation_sha256')):
   unverified_final.append(t['task_run_id'])
  if post.get('primary_status')=='succeeded' and post.get('verification',{}).get('verdict')=='uncertain':
   unconfirmed_primary.append(t['task_run_id'])
  if post.get('primary_status')=='succeeded' and post.get('verification',{}).get('verdict')=='not_met':
   contradicted_primary.append(t['task_run_id'])
  if ((t['status']=='succeeded' and (v.get('verdict')!='verified' or v.get('observation_sha256')!=result.get('final_observation_sha256')))
          or (post.get('primary_status')=='succeeded' and post.get('verification',{}).get('verdict')!='verified')):
   false_success.append(t['task_run_id'])
 feedback=[];prior={};memory_feedback=[];past_cycles=set()
 deep_followups=[]
 for a in executed:
  binding=a.get('semantic_binding') or {}
  original=c.execute('SELECT payload FROM semantic_observations WHERE observation_id=?',(binding.get('observation_id'),)).fetchone()
  original=json.loads(original[0]) if original else {}
  view=SimpleNamespace(foreground_package=original.get('foreground_package'),foreground_activity=original.get('foreground_activity'),
      controls=[SimpleNamespace(**n) for n in original.get('controls',[])])
  if a['candidate_action']['type']!='swipe' or not is_about_page(view):continue
  after=[]
  for row in c.execute('SELECT s.observation_id,s.screenshot_sha256,s.evidence_quality,o.metadata FROM semantic_observations s JOIN observations o USING(observation_id) WHERE s.task_run_id=? ORDER BY o.rowid',(a['task_run_id'],)):
   if json.loads(row[3])['captured_monotonic_ns']>a.get('finished_monotonic_ns',2**63):
    after.append({'observation_id':row[0],'screenshot_sha256':row[1],'quality':row[2]})
  deep_followups.append({'task_run_id':a['task_run_id'],'dispatch_monotonic_ns':a.get('dispatch_monotonic_ns'),
    'dynamic_about_before_dispatch':original.get('evidence_quality')=='trusted_dynamic_noninteractive',
    'fresh_trusted_after_dispatch':any(s['quality'] in {'trusted','trusted_dynamic_noninteractive'} for s in after),
    'followup_observations':after})
 for cy in cycles:
  context=json.dumps(cy['context'],sort_keys=True)
  ctx=cy['context'] or {}
  context_sources={source for ep in ctx.get('recent_episodes',[]) for source in ep.get('source_runs',[])} & past_cycles
  cycle_audits=[audit for event in events if event['event_type'] in {'life_model_choice','life_decision_rejected'}
      and event['payload'].get('task_run_id')==cy['task_run_id'] for audit in event['payload'].get('audit',[])]
  ctx_sha=hashlib.sha256(json.dumps(cy['context'],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
  if context_sources:
   memory_feedback.append({'task_run_id':cy['task_run_id'],'earlier_episode_sources':sorted(context_sources),
      'model_reflection_present':any(ep.get('body',{}).get('model_reflection') and set(ep.get('source_runs',[])) & context_sources for ep in ctx.get('recent_episodes',[])),
      'context_hash_matches_received_request':any(a.get('life_context_sha256')==ctx_sha and a.get('response_sha256') for a in cycle_audits)})
  context_refs=[r for ep in (cy['context'] or {}).get('recent_episodes',[])+(cy['context'] or {}).get('recent_failures',[])
      for r in ep.get('body',{}).get('objective',{}).get('decision_rejections',[])]
  for source,rejections in prior.items():
   found=[r for r in rejections if r.get('reason_code') in CODES and any(
       ref.get('source_task_run_id')==source and ref.get('reason_code')==r['reason_code'] and
       ref.get('candidate_sha256')==r.get('candidate_sha256') for ref in context_refs)]
   if not found:continue
   same_error=[e for e in events if e['event_type']=='life_decision_rejected' and e['payload'].get('task_run_id')==cy['task_run_id']]
   audit_events=[e for e in events if e['event_type'] in {'life_decision_rejected','life_model_choice'} and e['payload'].get('task_run_id')==cy['task_run_id']]
   request_audits=[a for e in audit_events for a in e['payload'].get('audit',[])]
   next_codes={r.get('reason_code') for e in same_error for r in e['payload'].get('rejections',[])}
   next_codes.update(a.get('feedback',{}).get('reason_code') for a in request_audits if a.get('validation_status')=='rejected')
   next_codes &= CODES
   model_choices=[e['payload'].get('model_decision',{}) for e in audit_events if e['event_type']=='life_model_choice']
   context_sha=hashlib.sha256(json.dumps(cy['context'],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
   input_proof=any(any(r.get('source_task_run_id')==source and r.get('reason_code') in {x['reason_code'] for x in found}
                       for r in a.get('included_decision_rejections',[])) and a.get('request_user_sha256') and a.get('response_sha256') for a in request_audits)
   feedback.append({'source_task_run_id':source,'next_task_run_id':cy['task_run_id'],
       'reason_codes':sorted({r['reason_code'] for r in found}),'in_next_context':True,'received_api_response_with_input_audit':input_proof,
       'context_digest_matches':any(a.get('life_context_sha256')==context_sha for a in request_audits),
       'new_decision_kind':(cy['decision'] or {}).get('kind','unavailable'),
       'new_model_kind':model_choices[-1].get('kind') if model_choices else 'no_accepted_choice',
       'new_selected_goal_id':(cy['decision'] or {}).get('selected_goal_id'),
       'new_candidate_sha256s':[a['response_sha256'] for a in request_audits if a.get('response_sha256')],
       'next_rejection_codes':sorted(next_codes),'same_error_repeated':bool(next_codes & {r['reason_code'] for r in found}),
       'any_rejection_next_episode':bool(next_codes)})
  rejections=(cy['summary'] or {}).get('objective',{}).get('decision_rejections',[])
  if rejections:prior[cy['task_run_id']]=rejections
  past_cycles.add(cy['task_run_id'])
 result={'tasks':len(tasks),'statuses':dict(collections.Counter(t['status'] for t in tasks)),
  'confirmed_model_actions':dict(collections.Counter(a['candidate_action']['type'] for a in executed if a.get('provider')!='program_owned_manual_diagnostic')),
  'confirmed_program_manual_actions':dict(collections.Counter(a['candidate_action']['type'] for a in executed if a.get('provider')=='program_owned_manual_diagnostic')),
  'unsafe_dispatches':len(unsafe),'false_successes':len(false_success),
  'unverified_final_successes':len(unverified_final),'unconfirmed_primary_claims':len(unconfirmed_primary),
  'contradicted_primary_claims':len(contradicted_primary),
  'unknown_transports':sum(a.get('execution_status')=='uncertain_after_transport_error' for a in attempts)+unconfirmed_recovery,
  'semantic_observations':len(sem),'qualities':dict(collections.Counter(s['evidence_quality'] for s in sem)),
  'additional_semantic_samples':len(additional),'additional_qualities':dict(collections.Counter(s['evidence_quality'] for s in additional)),
  'additional_backend_reasons':dict(collections.Counter(a['reason'] for s in additional for a in s['attempts'])),
  'backend_reasons':dict(collections.Counter(a['reason'] for s in sem for a in s['attempts'])),
  'dynamic_audits':sum(bool(s.get('stability')) for s in sem),
  'dynamic_text_changes':sum(r.get('before_text_sha256')!=r.get('after_text_sha256') for s in sem for r in s.get('stability',[])),
  'recovery_successes':sum(r.get('state') in {'recovered_readonly','recovered_after_launch'} for r in recoveries),
  'recovery_failures':sum(r.get('state') not in {'recovered_readonly','recovered_after_launch'} for r in recoveries),
  'confirmed_recovery_launches':sum(r.get('recovery_actions',0) for r in recoveries),
  'recovery_dispatches':len(recovery_dispatches),'unconfirmed_recovery_dispatches':unconfirmed_recovery,
  'recovery_states':dict(collections.Counter(r.get('state') for r in recoveries)),
  'rejected_actions':sum(a.get('validation_status') in {'policy_denied','invalid','reference_invalid'} for a in attempts),
  'policy_denied_candidates':sum(a.get('validation_status')=='policy_denied' for a in attempts),
  'causal_feedback':feedback,'structured_rejection_episodes':len(prior),
  'memory_feedback':memory_feedback,
  'deep_scroll_followups':deep_followups,
  'public_tasks':[public_task(t) for t in tasks]}
 if cycles:
  result['cycle_metrics']=[{'task_run_id':cy['task_run_id'],'kind':(cy['decision'] or {}).get('kind','unavailable'),
     'model_chose_rest':any(e['event_type']=='life_model_choice' and e['payload'].get('task_run_id')==cy['task_run_id'] and e['payload'].get('model_decision',{}).get('kind')=='rest' for e in events),
     'program_forced_rest':any(e['event_type']=='life_guard_rest' and e['payload'].get('task_run_id')==cy['task_run_id'] for e in events),
     'objective':{k:(cy['summary'] or {}).get('objective',{}).get(k) for k in ('status','reason_code','confirmed_actions','api_calls')},
     'selected_verification_kind':next((g['verification']['kind'] for g in (cy['decision'] or {}).get('candidates',[]) if g['goal_id']==(cy['decision'] or {}).get('selected_goal_id')),None),
     'rejection_codes':[r.get('reason_code') for r in (cy['summary'] or {}).get('objective',{}).get('decision_rejections',[])]} for cy in cycles]
  result['api_requests']=[dict(r) for r in c.execute('SELECT phase,status,COUNT(*) AS count FROM life_requests GROUP BY phase,status')]
 store.close();return result

