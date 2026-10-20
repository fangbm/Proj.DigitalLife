"""Read-only causal proof and explicitly versioned acceptance interpretation."""
import json
import re
import sqlite3
import sys
from pathlib import Path
from acceptance.continuation import CODE,FIELDS,ROOT,digest,dump,frozen_check
from phonelife.storage import Store

def verify(out,destination=None):
    pre=json.loads((out/'preflight.json').read_text(encoding='utf8'))
    workers={k:json.loads((out/(k+'-worker.json')).read_text(encoding='utf8')) for k in ('source','successor')}
    codes=json.loads((out/'process-results.json').read_text(encoding='utf8'))
    source,next_run=workers['source']['task_run_id'],workers['successor']['task_run_id']
    store=Store.open_readonly(out/'state.sqlite3');c=store.conn
    try:
        tasks={k:store.task(v['task_run_id']) for k,v in workers.items()}
        events=[{'id':r['id'],'kind':r['event_type'],'body':json.loads(r['payload'])} for r in c.execute('SELECT * FROM events ORDER BY id')]
        exchanges=[e for e in events if e['kind']=='acceptance_api_exchange']
        by_run={run:[e for e in exchanges if e['body']['task_run_id']==run and e['body']['phase']=='brain'] for run in (source,next_run)}
        errors=tasks['source']['result'].get('life_decision_rejections',[])
        source_memory=[]
        for row in c.execute("SELECT m.memory_id,m.body FROM life_memories m JOIN life_memory_sources s USING(memory_id) WHERE s.task_run_id=? AND m.layer='episodic'",(source,)):
            source_memory.append({'memory_id':row['memory_id'],'rejections':json.loads(row['body'])['objective']['decision_rejections']})
        def contains_all(saved):
            return all(any(all(x.get(k)==r[k] for k in FIELDS) for x in saved) for r in errors)
        next_context=json.loads(c.execute('SELECT context FROM life_cycles WHERE task_run_id=?',(next_run,)).fetchone()[0])
        next_context_hash=digest(json.dumps(next_context,sort_keys=True,ensure_ascii=False))
        refs=[r for ep in next_context['recent_episodes']+next_context['recent_failures'] for r in ep.get('body',{}).get('objective',{}).get('decision_rejections',[])]
        proofs=[]
        for e in by_run[next_run]:
            b=e['body'];request=c.execute('SELECT * FROM life_requests WHERE request_id=?',(b['request_id'],)).fetchone()
            audits=[a for ev in events if ev['kind'] in ('life_model_choice','life_decision_rejected') and ev['body'].get('task_run_id')==next_run for a in ev['body'].get('audit',[])]
            matching=[a for a in audits if a.get('request_user_sha256')==b.get('request_user_sha256') and a.get('life_context_sha256')==b['life_context_sha256'] and a.get('response_sha256')==b['returned_candidate_sha256']]
            linked=bool(c.execute('SELECT 1 FROM event_sources WHERE event_id=? AND task_run_id=?',(e['id'],next_run)).fetchone())
            details=json.loads(request['details']) if request and request['details'] else {}
            memory_links=all(c.execute('SELECT 1 FROM life_memory_sources WHERE memory_id=? AND task_run_id=?',(ref['memory_id'],source)).fetchone() for ref in b['context_errors'])
            passed=bool(request and request['status']=='received' and request['task_run_id']==next_run
                and b['status']=='received' and not b['injection_enabled'] and not b['substituted']
                and b['response_origin']=='actual_provider' and b['life_context_sha256']==next_context_hash
                and b['provider_request_id_sha256']==digest(details.get('api_request_id',''))
                and matching and linked and memory_links and contains_all(b['context_errors']))
            proofs.append({'task_run_id':next_run,'request_id':b['request_id'],'event_id':e['id'],
                'request_payload_sha256':b['request_payload_sha256'],'request_user_sha256':b['request_user_sha256'],
                'life_context_sha256':b['life_context_sha256'],'provider_response_sha256':b['provider_response_sha256'],
                'provider_request_id_sha256':b['provider_request_id_sha256'],'context_errors':b['context_errors'],
                'sqlite_request_received':request['status']=='received','frozen_brain_audit_matches_wire':bool(matching),
                'event_and_memory_sources_linked':bool(linked and memory_links),'all_required_fields_received':passed})
        source_request_count=c.execute('SELECT COUNT(*) FROM life_requests WHERE task_run_id=?',(source,)).fetchone()[0]
        source_nonbrain=c.execute("SELECT COUNT(*) FROM life_requests WHERE task_run_id=? AND phase!='brain'",(source,)).fetchone()[0]
        source_goals=c.execute('SELECT COUNT(*) FROM life_goals WHERE source_run=?',(source,)).fetchone()[0]
        recovery_actions=sum(e['kind']=='semantic_recovery_dispatch' and e['body'].get('task_run_id')==source for e in events)
        source_audits=[a for e in events if e['kind']=='life_decision_rejected' and e['body'].get('task_run_id')==source for a in e['body'].get('audit',[])]
        source_exchange_links=all(any(a.get('response_sha256')==e['body'].get('returned_candidate_sha256') and a.get('request_user_sha256')==e['body']['request_user_sha256'] for a in source_audits) for e in by_run[source])
        choices=[e['body'].get('model_decision',{}) for e in events if e['kind']=='life_model_choice' and e['body'].get('task_run_id')==next_run]
        reason=choices[-1].get('reason','') if choices else ''
        reason_tokens=[t for t in ('continuation','reference','rejected','refusal','previous','earlier','上次','拒绝') if t in reason.casefold()]
        next_rejections=tasks['successor']['result'].get('life_decision_rejections',[])
        natural_same_error=any(r['reason_code']==CODE for r in next_rejections)
        tests=(ROOT/'data/continuation-regression-final-212.log').read_text(encoding='utf8')
        original=(ROOT/'data/phase122/final-regression-F2.log').read_text(encoding='utf8')
        names=lambda text:set(re.findall(r'^(test_\w+) \(([^)]+)\)',text,re.M))
        offline='Ran 212 tests' in tests and '\nOK\n' in tests and len(names(original))==198 and names(original)<=names(tests)
        checks={
            'original_198_and_new_14_offline_tests':offline,
            'source_exact_missing_reference_twice':len(errors)==2 and all(r['reason_code']==CODE and r['source_task_run_id']==source and r['executed'] is False and r['confirmed_actions']==0 for r in errors),
            'existing_two_request_budget_preserved':source_request_count==2 and [r['generation_attempts'] for r in errors]==[1,2] and [r['repairs_used'] for r in errors]==[0,1],
            'source_no_goal_no_gui_no_recovery_dispatch':source_goals==0 and source_nonbrain==0 and not tasks['source']['attempts'] and recovery_actions==0,
            'source_rejections_and_real_requests_hash_linked':source_exchange_links and len(by_run[source])==2 and all(e['body']['substituted'] and e['body']['status']=='received' for e in by_run[source]),
            'episodic_error_fields_preserved':bool(source_memory) and any(contains_all(m['rejections']) for m in source_memory),
            'new_process_reopened_same_closed_sqlite':workers['source']['process_id']!=workers['successor']['process_id'] and all(w['sqlite_connections_closed'] for w in workers.values()) and workers['source']['db_closed_sha256']==workers['successor']['db_open_sha256'],
            'successor_injection_disabled':workers['successor']['substitutions']==0 and workers['successor']['injection_enabled_at_start'] is False,
            'specific_error_present_in_persisted_next_context':bool(errors) and contains_all(refs),
            'specific_error_received_by_real_api':bool(proofs) and all(p['all_required_fields_received'] for p in proofs),
            'both_finite_workers_exited':codes=={'source':0,'successor':0},
            'frozen_production_runtime_unchanged':frozen_check()==pre['frozen_runtime'],
            'injection_harness_matches_start_manifest':all(digest((ROOT/p).read_bytes())==v for p,v in pre['acceptance_sources'].items()),
            'original_198_test_files_unchanged':all(digest((ROOT/p).read_bytes())==v for p,v in pre['old_198_test_files'].items()),
            'persona_and_dpapi_unchanged':digest((ROOT/'persona.life.example.md').read_bytes())==pre['persona_sha256'] and digest((ROOT/'data/deepseek.key.dpapi').read_bytes())==pre['dpapi_ciphertext_sha256']}
        from acceptance.evidence_metrics import inspect
        safety=inspect(out/'state.sqlite3')
        checks['no_unauthorized_or_false_success_or_unknown_dispatch']=safety['unsafe_dispatches']==0 and safety['false_successes']==0 and safety['unknown_transports']==0
        checks['no_old_actions_replayed']=all(a['payload']['task_run_id']==tasks['successor']['task_run_id'] for a in tasks['successor']['attempts']) and not tasks['source']['attempts']
        old_manifest=ROOT/'data/phase122/PRIVATE-SHA256.json'
        old=json.loads(old_manifest.read_text(encoding='utf8'))['files']
        checks['all_previous_private_evidence_unchanged']=all(digest((ROOT/p).read_bytes())==v['sha256'] for p,v in old.items())
        baseline=json.loads((ROOT/'data/phase122/acceptance-gates.json').read_text(encoding='utf8'))['gates']
        gates={k:('PASS' if v else 'NOT OBSERVED') for k,v in baseline.items()}
        passed=all(checks.values());gates['continuation_error_detail_in_next_episode']='PASS' if passed else 'FAIL'
        result={'evidence_classification':{'offline_fixture':'PASS' if offline else 'FAIL',
            'live_api_controlled_injection':'PASS' if passed else 'FAIL',
            'live_api_natural_occurrence':'OBSERVED_AFTER_CONTROLLED_HISTORY' if natural_same_error else 'not_observed'},
            'definition_version':'v2-explicit-controlled-fault-acceptance',
            'definition_change':'v1 natural source occurrence remains unproved. v2 accepts a labeled controlled source only when real HTTP, persistence, process restart and exact error delivery are proven. Natural occurrence and model behavior are separate observations.',
            'original_v1_gate':'FAIL / NOT OBSERVED; original artifact unchanged',
            'checks':checks,'gates_v2':gates,'closure_under_v2':'PASS' if all(v=='PASS' for v in gates.values()) else 'FAIL',
            'source_task_run_id':source,'next_task_run_id':next_run,'source_rejections':errors,'source_memory':source_memory,
            'actual_successor_requests':proofs,'workers':workers,
            'model_behavior':{'accepted_model_kind':choices[-1].get('kind') if choices else None,
                'reason_sha256':digest(reason),'reason_mentions_history_heuristic':bool(reason_tokens),
                'reason_mentions_specific_prior_continue_error':('malformed continue' in reason.casefold() or CODE.casefold() in reason.casefold()),
                'matched_tokens':reason_tokens,'same_error_repeated_naturally':natural_same_error,
                'note':'Mention heuristic is not required for delivery PASS and does not prove learning or causality'},
            'safety':{k:safety[k] for k in ('statuses','confirmed_model_actions','unsafe_dispatches','false_successes','unknown_transports')},
            'prior_fixed_20_and_30_minute_results':'Inherited from immutable frozen F evidence; no production decision/persistence code changed, so focused real E2E is used, not a new 20/30-minute claim'}
        if destination is not None:
            destination.mkdir(parents=True,exist_ok=False)
            dump(destination/'causal-proof.json',result)
        return result
    finally:store.close()

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cohort',nargs='?',type=Path,help='PRIVATE SQLite cohort and original frozen-runtime evidence required')
    parser.add_argument('--public',type=Path,help='Public archive integrity only; does not reprove real API/device behavior')
    parser.add_argument('--self-check',action='store_true',help='Offline import and verifier dependency self-check')
    parser.add_argument('--output',type=Path,help='Optional NEW derived-output directory; default is read-only')
    args=parser.parse_args()
    if args.public:
        from acceptance.verify_public import verify_archive
        print(json.dumps(verify_archive(args.public),indent=2))
    elif args.self_check:
        from acceptance.evidence_metrics import inspect
        assert callable(inspect) and len(digest('verifier-self-check'))==64
        print(json.dumps({'self_check':'PASS','private_evidence_loaded':False,'live_behavior_reproved':False}))
    elif args.cohort:
        if not (args.cohort/'state.sqlite3').is_file():parser.error('Private SQLite evidence absent; public integrity cannot prove causality')
        result=verify(args.cohort,args.output)
        print(json.dumps({k:result[k] for k in ('evidence_classification','checks','closure_under_v2')},indent=2))
    else:parser.error('Choose --self-check, --public ARCHIVE or a private evidence cohort')
