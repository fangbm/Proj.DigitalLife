"""Bounded all-app request views. Immutable source memories/audits stay in SQLite."""
from copy import deepcopy
from dataclasses import replace

def excerpt(value,limit=240):return value[:limit] if isinstance(value,str) else value

def error_view(errors):
    if not isinstance(errors,(list,tuple)):return []
    return [{k:excerpt(e[k],200) for k in ('code','field','expected','actual_length') if k in e} for e in errors[:4] if isinstance(e,dict)]

def memory_view(item):
    result={k:deepcopy(item[k]) for k in ('memory_id','layer','subject','source_runs','related_memory_ids','trust','environment_relation') if k in item}
    result['subject']=excerpt(result.get('subject',''),160)
    body=item.get('body',{});objective=body.get('objective',{})
    if objective:
        # Keep concrete decision-error delivery fields intact, not recursively
        # nested prior reflections or the long control-failure candidate text.
        result['body']={'objective':{k:deepcopy(objective[k]) for k in ('status','reason_code','confirmed_actions','api_calls','decision_rejections') if k in objective}}
        result['body']['objective']['detail']=excerpt(objective.get('detail',''),240)
        result['body']['objective']['control_failures']=[error_view(errors) for errors in objective.get('control_failures',[])[:3]]
        verification=objective.get('verification')or{}
        result['body']['objective']['verification']={k:deepcopy(verification[k]) for k in ('verdict','evidence_type','observation_sha256') if k in verification}
        result['body'].update({k:excerpt(body[k],240) for k in ('goal','decision_reason','content_sha256') if k in body})
        reflection=body.get('model_reflection')or{}
        result['body']['model_reflection']={k:deepcopy(reflection[k]) for k in ('discoveries','topics','worth_continuing','next_adjustment') if k in reflection}
    else:
        result['body']={k:deepcopy(v) for k,v in body.items() if k in {'evidence','evidence_type','reason_code','status','legacy_summary','verification','topic','before','after','verified_outcome','new_content_observed','source_type','recovery'}}
        if not result['body']:result['body']={'summary':excerpt(str(body),320)}
    return result

def compact_context(context):
    episodes=[memory_view(v) for v in context.recent_episodes]
    relevant=[memory_view(v) for v in context.relevant_memories if v.get('memory_id') not in {e.get('memory_id') for e in episodes}]
    # Failed decision fields/source refs remain available in recent_episodes;
    # recent_failures is a distinct index, not a second full copy of each episode.
    failures=[memory_view(v) for v in context.recent_failures]
    for v in failures:v['body']={'objective':v['body'].get('objective',{})}
    environment=deepcopy(context.semantic_environment)
    environment['recent_pages']=[{k:p[k] for k in ('quality','content_sha256','known_page_labels') if k in p} for p in environment.get('recent_pages',[])][-5:]
    environment['recent_control_failures']=[{k:p[k] for k in ('status','reason_code','confirmed_actions') if k in p} for p in environment.get('recent_control_failures',[])][-5:]
    safety=deepcopy(context.safety_constraints)
    environment.get('capabilities',{}).pop('installed_launchable_apps',None)
    environment['inventory_source']='safety_constraints.autonomous_apps'
    return replace(context,recent_episodes=episodes,relevant_memories=relevant[:8],recent_failures=failures,
        interests=context.interests[:12],semantic_environment=environment,safety_constraints=safety)

def compact_gui(request):
    request=deepcopy(request)
    # Current package enum in action_schema is authoritative and complete.
    request.pop('launch_allowlist',None)
    semantic=request.get('semantic_context',{})
    semantic.get('capabilities',{}).pop('installed_launchable_apps',None)
    request['previous_executed_actions']=[{**p,'reason':excerpt(p.get('reason',''),120)} for p in request.get('previous_executed_actions',[])[-6:]]
    return request

def compact_reflection(experience):
    result=deepcopy(experience)
    result['relevant_memory']=[memory_view(v) for v in experience.get('relevant_memory',[])[:4]]
    result['execution_trace']=[{k:deepcopy(v) for k,v in p.items() if k in {'candidate_action','validation_status','execution_status','progress'}} for p in experience.get('execution_trace',[])[-8:]]
    for p in result['execution_trace']:
        if isinstance(p.get('candidate_action'),dict):p['candidate_action']['reason']=excerpt(p['candidate_action'].get('reason',''),160)
    for p,original in zip(result['execution_trace'],experience.get('execution_trace',[])[-8:]):p['field_errors']=error_view(original.get('field_errors')or[])
    return result
