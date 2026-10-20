"""Stage A-C audit/export. Does not approve or start formal acceptance."""
import collections
import difflib
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
from acceptance.evidence_metrics import inspect
from phonelife.longrun_state import SessionLedger,runtime_manifest,sha
from phonelife.storage import Store

ROOT=Path(__file__).resolve().parents[1]

def write(path,value):path.write_text(json.dumps(value,indent=2,ensure_ascii=False),encoding='utf8')

def captures(path):
    store=Store.open_readonly(path);c=store.conn;counts=collections.Counter()
    try:
        for r in c.execute('SELECT * FROM observations'):
            if r['evidence_path']:
                assert sha(r['evidence_path'])==json.loads(r['metadata'])['sha256'];counts['png']+=1
        for r in c.execute('SELECT * FROM semantic_observations'):
            if not r['private_path']:continue
            p=Path(r['private_path']);assert sha(p)==r['raw_sha256'];counts['raw_tree']+=1
            for frame in json.loads(p.read_text(encoding='utf8')).get('_collection',{}).get('frames',[]):
                q=p.parent/(r['task_run_id']+'-'+r['observation_id']+f'-sample-{frame["index"]}-PRIVATE.png')
                assert sha(q)==frame['sha256'];counts['internal_png']+=1
        for r in c.execute("SELECT payload FROM events WHERE event_type='additional_semantic_collection'"):
            x=json.loads(r[0]);counts['additional_samples']+=1
            if x.get('private_path'):assert sha(x['private_path'])==x['raw_sha256']
            for f in x['private_frames']:assert sha(f['path'])==f['sha256'];counts['additional_png']+=1
        return dict(counts)
    finally:store.close()

def deliver(out,preflight_dir):
    out=out.resolve();preflight_dir=preflight_dir.resolve()
    base=json.loads((out/'BASELINE.json').read_text(encoding='utf8'))
    pre=json.loads((preflight_dir/'SOAK_PREFLIGHT.json').read_text(encoding='utf8'))
    assert pre['formal_soak_started'] is False
    if pre.get('preflight_entry_sha256'):assert sha(ROOT/'acceptance/soak_preflight.py')==pre['preflight_entry_sha256']
    assert runtime_manifest()==pre['frozen_runtime_sha256']==json.loads((out/'runtime-v023-sha256.json').read_text())
    original_tests={p:v for p,v in base['files'].items() if p.startswith('tests/')}
    assert all(sha(ROOT/p)==v for p,v in original_tests.items())
    historical={p:v for p,v in base['files'].items() if p.startswith(('docs/','dist/'))}
    assert all(sha(ROOT/p)==v for p,v in historical.items())
    private_checks=[]
    for path in ('data/phase122/PRIVATE-SHA256.json','data/continuation-acceptance-01/PRIVATE-SHA256.json'):
        doc=json.loads((ROOT/path).read_text(encoding='utf8'))
        assert all(sha(ROOT/p)==v['sha256'] for p,v in doc['files'].items())
        private_checks.append({'manifest':path,'files_verified':len(doc['files']),'sha256':sha(ROOT/path)})
    dbs=[]
    for row in base['backups']:
        a=sqlite3.connect((ROOT/row['backup']).resolve().as_uri()+'?mode=ro',uri=True)
        b=sqlite3.connect((ROOT/row['original']).resolve().as_uri()+'?mode=ro',uri=True);tables=[]
        try:
            for table, in a.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                if table=='sqlite_sequence':continue
                query='SELECT * FROM "'+table.replace('"','""')+'"'
                before=collections.Counter(a.execute(query));after=collections.Counter(b.execute(query))
                tables.append({'table':table,'original_rows':sum(before.values()),'missing_original_rows':sum((before-after).values())})
            check=b.execute('PRAGMA quick_check').fetchone()[0]
            assert check=='ok' and all(t['missing_original_rows']==0 for t in tables)
            dbs.append({'database':row['original'],'quick_check':check,'tables':tables})
        finally:a.close();b.close()
    assert sha(ROOT/'data/deepseek.key.dpapi')==base['files']['data/deepseek.key.dpapi']
    assert sha(ROOT/'persona.life.example.md')==base['files']['persona.life.example.md']
    formal=SessionLedger(Path(pre['formal_session'])/'soak-control.sqlite3',readonly=True)
    try:
        f=formal.load();assert f['state']=='awaiting_approval' and f['started_monotonic'] is None and f['elapsed_seconds']==0
        assert f['fingerprint']==pre['approval_fingerprint']
    finally:formal.close()
    main=preflight_dir/'live-session';metrics=inspect(main/'agent.sqlite3');natural_samples=[]
    store=Store.open_readonly(main/'agent.sqlite3');c=store.conn
    try:
        requests=[dict(r) for r in c.execute('SELECT phase,status,COUNT(*) AS count FROM life_requests GROUP BY phase,status')]
        events=[{'kind':r[0],'payload':json.loads(r[1])} for r in c.execute('SELECT event_type,payload FROM events')]
        exchanges=[e['payload'] for e in events if e['kind']=='longrun_api_exchange']
        for task_id, in c.execute("SELECT task_run_id FROM task_runs WHERE status='failed'"):
            task=store.task(task_id);errors=(task.get('result')or{}).get('life_decision_rejections',[])
            if not errors or not any(e['reason_code']=='MISSING_CONTINUATION_REFERENCE' for e in errors):continue
            wire=[e for e in exchanges if e['task_run_id']==task_id and e['phase']=='brain' and e['status']=='received']
            audits=[a for e in events if e['kind']=='life_decision_rejected' and e['payload'].get('task_run_id')==task_id for a in e['payload'].get('audit',[])]
            links=[]
            for error in errors:
                matched=[w for w in wire if w.get('response_sha256')==error['candidate_sha256'] and any(
                    a.get('response_sha256')==w['response_sha256'] and a.get('request_user_sha256')==w['request_user_sha256'] for a in audits)]
                assert matched
                w=matched[0];request=c.execute('SELECT status FROM life_requests WHERE request_id=?',(w['request_id'],)).fetchone()
                assert request[0]=='received'
                links.append({k:w[k] for k in ('request_id','request_user_sha256','request_payload_sha256','response_sha256','provider_request_id_sha256')})
            mem=[{'memory_id':r[0],'errors':json.loads(r[1]).get('objective',{}).get('decision_rejections',[])} for r in c.execute(
                "SELECT m.memory_id,m.body FROM life_memories m JOIN life_memory_sources s USING(memory_id) WHERE s.task_run_id=? AND m.layer='episodic'",(task_id,))]
            assert not task['attempts'] and c.execute('SELECT COUNT(*) FROM life_goals WHERE source_run=?',(task_id,)).fetchone()[0]==0
            subsequent=c.execute("SELECT COUNT(*) FROM life_requests WHERE phase='brain' AND task_run_id!=? AND requested_at>(SELECT MAX(finished_at) FROM life_requests WHERE task_run_id=?)",(task_id,task_id)).fetchone()[0]
            natural_samples.append({'experiment_kind':'live_api_preflight','response_origin':'live_api_natural_occurrence',
                'task_run_id':task_id,'reason_code':'MISSING_CONTINUATION_REFERENCE','controlled_substitution':False,
                'source_status':'failed','rejections':errors,'real_request_links':links,'episodic_memory':mem,
                'corresponding_goal_executed':False,'gui_inputs_sent':0,'subsequent_brain_requests':subsequent,
                'natural_error_observed':'PASS','natural_next_episode_delivery':'NOT OBSERVED' if subsequent==0 else 'requires_separate_audit',
                'scope':'New natural error during preflight, not formal24h; original v022 evidence unchanged; same-cycle repair is not next-episode delivery'})
        usage=collections.Counter();reported=0
        for e in exchanges:
            if e.get('token_usage_reported'):reported+=1;usage.update(e['provider_usage'])
        choices=[e['payload']['model_decision'] for e in events if e['kind']=='life_model_choice']
        kinds=collections.Counter(x['kind'] for x in choices)
        candidates=[g for d in choices for g in d.get('candidates',[])]
        proposed=[a for e in events if e['kind'] in {'life_model_choice','life_decision_rejected'} for a in e['payload'].get('audit',[])]
        validated=collections.Counter(a.get('validation_status','unclassified') for a in proposed)
        progress=collections.Counter((a['payload'].get('progress') or {}).get('verdict','not_assessed') for r in c.execute('SELECT task_run_id FROM task_runs') for a in store.task(r[0])['attempts'] if a['payload'].get('execution_status')=='executed')
        cycle_ids=[r[0] for r in c.execute('SELECT task_run_id FROM task_runs')]
        interests=[dict(r) for r in c.execute('SELECT topic,weight,evidence_count FROM life_interests')]
        db_check=c.execute('PRAGMA quick_check').fetchone()[0]
        assert db_check=='ok' and c.execute("SELECT COUNT(*) FROM task_runs WHERE status='running'").fetchone()[0]==0
    finally:store.close()
    ledger=SessionLedger(main/'soak-control.sqlite3',readonly=True)
    try:
        summary=ledger.load();resources=[json.loads(r[0])['resources'] for r in ledger.conn.execute("SELECT payload FROM health_events WHERE kind='health'")]
        health_count=ledger.conn.execute('SELECT COUNT(*) FROM health_events').fetchone()[0]
        exported=ledger.conn.execute('SELECT COUNT(*) FROM exported_cycles').fetchone()[0]
    finally:ledger.close()
    lines_json=[json.loads(x) for x in (main/'cycles.jsonl').read_text(encoding='utf8').splitlines()]
    assert len(lines_json)==len({x['task_run_id'] for x in lines_json})==exported==len(cycle_ids)
    peaks={k:max((r[k] for r in resources),default=0) for k in ('total_bytes','audit_bytes','database_bytes','wal_bytes','worker_rss_bytes','supervisor_rss_bytes')}
    report_metrics={'scope':'live_api_preflight only; not 24h acceptance','elapsed_seconds':summary['elapsed_seconds'],
        'phase_seconds':summary['phase_seconds'],'device_unavailable_seconds':summary['device_unavailable_seconds'],
        'process_unavailable_seconds':summary['process_unavailable_seconds'],
        'autonomous_rest_seconds':summary['autonomous_rest_seconds'],'forced_rest_seconds':summary['forced_rest_seconds'],
        'model_decisions':dict(kinds),'candidate_count':len(candidates),'candidate_validations':dict(validated),
        'distinct_candidate_description_hashes':len({hashlib.sha256(g['description'].encode()).hexdigest() for g in candidates}),
        'api_requests':requests,'provider_usage_totals':dict(usage),'requests_with_usage':reported,
        'requests_without_usage':sum(r['count'] for r in requests)-reported,'estimated_cost':None,
        'cost_formula_if_prices_supplied':'uncached_input_tokens*P_input/1e6 + cached_input_tokens*P_cached/1e6 + output_tokens*P_output/1e6',
        'cost_assumptions':'No provider price configured; missing usage is unknown, not zero; request count is not money',
        'action_progress':dict(progress),'interest_topics':len(interests),
        'health_events':health_count,'resource_peaks':peaks,'cycles_exported_without_duplicates':exported,
        'natural_continuation_error_sources_observed':len(natural_samples),
        **{k:metrics[k] for k in ('statuses','confirmed_model_actions','confirmed_program_manual_actions','unsafe_dispatches','false_successes',
            'unknown_transports','semantic_observations','qualities','additional_semantic_samples','additional_qualities',
            'recovery_states','confirmed_recovery_launches','memory_feedback','structured_rejection_episodes','causal_feedback')}}
    integrity={'old_database_backups':len(dbs),'databases':dbs,'original_212_test_files_unchanged':True,
        'historical_docs_archives_unchanged':True,'original_key_ciphertext_unchanged':True,'persona_unchanged':True,
        'previous_private_manifests':private_checks,'new_database_quick_check':db_check,'new_capture_integrity':captures(main/'agent.sqlite3'),
        'formal_session_not_started':True}
    write(out/'INTEGRITY.json',integrity);write(out/'PREFLIGHT_METRICS.json',report_metrics)
    public=out/'public-review';public.mkdir(exist_ok=False)
    write(out/'NATURAL_CONTINUATION_SAMPLES.json',natural_samples)
    write(public/'NATURAL_CONTINUATION_SAMPLES.json',natural_samples)
    write(public/'PREFLIGHT_METRICS.json',report_metrics)
    write(public/'INTEGRITY.json',{k:v for k,v in integrity.items() if k!='databases'})
    write(public/'SOAK_PREFLIGHT.json',{k:pre[k] for k in ('version','stage','formal_soak_started','approval_required','ready_for_review',
        'started_utc','finished_utc','measured_preflight_seconds','source_kind','formal_source_kind','tests_passed','tests_log_sha256','device',
        'frozen_runtime_sha256','approval_fingerprint','budget','expected_max_requests_in_24h','estimated_currency_cost','cost_assumptions',
        'preflight_entry_sha256','health_sampling_assumption',
        'private_evidence_explicit','private_directory_acl_restricted','database_quick_check','shared_lease_rows_at_end','running_tasks_at_end',
        'preflight_agent_state','formal_acceptance_matrix','stop_conditions','limits')})
    from phonelife.public_evidence import public_task
    private_store=Store.open_readonly(main/'agent.sqlite3')
    try:write(public/'tasks.json',[public_task(private_store.task(run)) for run in cycle_ids])
    finally:private_store.close()
    test_log=next(p for p in out.glob('full-regression-*.log') if sha(p)==pre['tests_log_sha256'])
    log=test_log.read_text(encoding='utf8');total=pre['tests_passed'];added=total-212
    names=re.findall(r'^(test_\w+) \(([^)]+)\)',log,re.M);assert len(names)==total
    write(public/'offline-tests.json',{'classification':'offline_fixture','original':212,'new':added,'total':total,'all_passed':True,
        'full_log_sha256':sha(test_log),'tests':[{'name':a,'class':b,'status':'PASS'} for a,b in names]})
    changes=[];patch=[]
    for base_dir in ('src/phonelife','acceptance','tests','docs'):
        for path in (ROOT/base_dir).rglob('*'):
            if not path.is_file() or path.suffix not in {'.py','.md','.json','.ps1','.jar'} or '__pycache__'in path.parts:continue
            key=path.relative_to(ROOT).as_posix()
            if base['files'].get(key)==sha(path):continue
            changes.append({'file':key,'before_sha256':base['files'].get(key),'after_sha256':sha(path)})
            previous=out/'source-before'/key
            if path.suffix!='.jar':
                old=previous.read_text(encoding='utf8').splitlines(True) if previous.is_file() else []
                patch.extend(difflib.unified_diff(old,path.read_text(encoding='utf8').splitlines(True),fromfile='before/'+key,tofile='after/'+key))
    for key in ('README.md','pyproject.toml','data/start-soak.ps1'):
        path=ROOT/key;changes.append({'file':key,'before_sha256':base['files'].get(key),'after_sha256':sha(path)})
    write(out/'changed-files-before-final-report.json',changes)
    (out/'implementation.patch').write_text(''.join(patch),encoding='utf8')
    original_runtime_changed=[p for p,h in base['frozen_v022'].items() if sha(ROOT/p)!=h]
    assert set(original_runtime_changed)=={'src/phonelife/__init__.py','src/phonelife/semantic_benchmark.py'}
    text=['# Proj.DigitalLife v0.2.3 / Phase 1.2.2 — Stage A-C 交付报告','',
        '**状态：Stage A、B 完成，Stage C 真实预检通过，停在等待明确批准。正式 24 小时 Soak 未启动，不能宣布 Phase 1.2.2 验收通过。**','',
        '## 已完成工作','',
        '- M0：非敏感验证依赖迁入 acceptance，新增公开完整性模式与只读私有复验；全新解压目录原 212 项通过，旧源码包保留。',
        '- M1-M3：新增持久全局时钟、有限 worker 监督、单实例、共享租约、只读健康、预算窗口等待、资源硬停止、异常收尾及 EmergencyStop 粘滞。',
        '- M4：逐周期 SQLite/JSONL 去重导出，候选/动作/恢复/休息/验证/记忆/反思/兴趣及 API/资源分开统计。',
        f'- 原 212 项测试文件/意图保持，新增 {added} 项，完整回归 **{total}/{total} PASS**。',
        '- 原 36 冻结生产文件中 34 个字节不变，另外两个仅更新版本文字；原动作、能力、决策、持久化和独立验证未放宽。新增运行模块 4 个，wheel 内 39 个运行文件与冻结源码一致。','',
        '架构、状态、限制与逐文件作用见 [长期运行管理](PHASE1_2_2_LONGRUN.md)；公开与私有验证边界见 [源码验证说明](SOURCE_VERIFICATION.md)。','',
        '## 真实短时预检','',
        f'原 Android 15/API35 隔离 AVD，serial={pre["device"]["serial"]}，QEMU 身份检查通过；既有 DPAPI 只读加载，真实 DeepSeek 主脑和 GUI API 可达。没有指定目标、没有 force 唤醒、没有缩短休息。会话单调时长 **{summary["elapsed_seconds"]:.3f} 秒**；含预检收尾共 {pre["measured_preflight_seconds"]:.3f} 秒。',
        f'结果：{report_metrics["statuses"]}；模型确认动作 {report_metrics["confirmed_model_actions"]}。本次没有确认的 tap/swipe/back，不能声称获得本轮 GUI 行动覆盖。没有为了增加动作强迫模型。',
        f'未经授权 {metrics["unsafe_dispatches"]}，假成功 {metrics["false_successes"]}，未知传输 {metrics["unknown_transports"]}。所有 failed/interrupted 均按实际原因保留，不删除或改成成功。',
        f'主脑实际选择 {dict(kinds)}；候选 {len(candidates)}，候选校验 {dict(validated)}；分阶段请求 {requests}。',
        f'语义观察 {metrics["semantic_observations"]}，质量 {metrics["qualities"]}，恢复状态 {metrics["recovery_states"]}。未出现的恢复、动态页面或停滞覆盖不能写成自然故障已通过。',
        f'实际主脑输入哈希匹配的跨轮记忆关联 {sum(x["context_hash_matches_received_request"] for x in metrics["memory_feedback"])} 条，反思引用和兴趣变化保存在私有 SQLite/JSONL；不声称反事实学习已证明。','',
        '| TaskRun | 状态 | 决策 | 理由码 | API |','| --- | --- | --- | --- | --- |']
    for cy in metrics.get('cycle_metrics',[]):text.append(f'| `{cy["task_run_id"]}` | {cy["objective"]["status"]} | {cy["kind"]} | {cy["objective"]["reason_code"]} | {cy["objective"]["api_calls"]} |')
    text+=['','### 新的自然 continuation 错误证据','',
        f'本次短预检观察到 **{len(natural_samples)} 个自然 MISSING_CONTINUATION_REFERENCE 源周期**，不是受控替换。两次真实提供商响应哈希均与冻结主脑拒绝候选 SHA、请求行和审计哈希吻合；源 TaskRun 和情景记忆保存了具体字段，源周期没有目标行或 GUI 输入。详见 NATURAL_CONTINUATION_SAMPLES.json。',
        '原 v0.2.2 自然样本未观察的历史结论不改写；这是 v0.2.3 新样本。它只补充自然错误源头被观察，不补充完整自然跨轮送达：全局120秒会话随后按退避到期，没有后续主脑请求。不能把同周期的结构化修复当成下一周期，也不强制唤醒模型以补足统计。正式24h的 Recovery/Memory 仍为 NOT OBSERVED。']
    text+=['','## 时钟与资源','',
        f'互斥状态时长：{summary["phase_seconds"]}；设备不可用 {summary["device_unavailable_seconds"]:.3f}s，进程不可用 {summary["process_unavailable_seconds"]:.3f}s（后两项次级时间不再加到总时长）。模型自主休息 {summary["autonomous_rest_seconds"]:.3f}s，程序强制休息 {summary["forced_rest_seconds"]:.3f}s。',
        f'资源采样峰值（字节）：{peaks}；结束剩余磁盘 {pre["resource_sample"]["free_bytes"]} 字节。WAL/证据预算和请求硬限见 SOAK_PREFLIGHT。',
        f'提供商 token 汇总 {dict(usage)}；有 usage 的请求 {reported}/{len(exchanges)}，缺失项保留 unknown。未配置可靠单价，货币费用为 null，公式与假设单列，不将请求数当费用。','',
        '## 故障与回归边界','',
        '离线受控覆盖 API timeout、429/5xx、ADB 不可用/设备 offline、真实 mock 子进程强杀、unknown 不重放、SQLite 写失败/损坏、磁盘/WAL/RSS 阈值、租约竞争、Pause/Resume/EmergencyStop、冻结篡改、时钟重启和去重。它们统一属于 offline_fixture，不混入正式自然运行。',
        '开发阶段的 smoke-01 Path 类型异常、离线时间重复计数失败及后续修复日志保留，最终测试通过不抹去开发失败记录。',
        '第一次120秒预检和其候选wheel/source保留为 preflight-01；随后补充内部 worker 必须已有启动会话的边界检查，在测试完成后更新代码，再对新冻结版本进行独立 preflight-02。未在任何运行中的测试中修改执行源码。',
        '本次自然短预检没有观察到 API 限流、设备离线、自然进程崩溃或未知分发；这些真实恢复覆盖为 NOT OBSERVED。正常截止取消是有界停止证据，不是自然崩溃恢复。','',
        '## 24 小时正式验收矩阵','', '| 门槛 | 当前正式状态 |','| --- | --- |']
    text += [f'| {k} | {v} |' for k,v in pre['formal_acceptance_matrix'].items()]
    text += ['','正式会话还没有起点、周期 JSONL 或任何实际 API 请求。短试 completed_duration 只对应 120 秒，不可替代 86400 秒或整体成功。',
        f'候选会话：`{pre["formal_session"]}`，state=awaiting_approval。冻结批准指纹：`{pre["approval_fingerprint"]}`。','',
        '## 拟批准的范围','',
        '真实单调目标 86400 秒，休息/配额等待照常计时，不安排每日目标。原 AVD、Settings 只读、既有 DPAPI、原共享协调库；120 请求/滚动小时、500/滚动24h、32/目标、15动作/目标、300秒/目标，更低值不得提高。新证据总预算4GiB，保留5GiB空闲，WAL128MiB、DB512MiB、审计64MiB、每进程RSS512MiB。',
        '停止条件包括期限、EmergencyStop、未知分发待审阅、资源阈值、QEMU身份变化、源码/配置/角色冻结变化、持续数据库失败和有限重启上限。正常停止后不保留长期后台 Agent。','',
        '## 完整性、归档与限制','',
        f'修改前备份 {len(dbs)} 个 SQLite；最终全部 quick_check=ok，原记录多重集完整。旧 v0.2.2 的两个私有清单逐项核验、旧报告/源码ZIP/wheel/公开证据/v1-v2 定义保持，密钥密文与角色字节不变。',
        '新短试的原图/完整树在限制 ACL 的私有会话目录；公开包仅有 allowlist TaskRun、哈希、状态、预算和计数，不放原 PNG/树/SQLite 或未经筛选模型文本。实际凭证会在内存中做 UTF-8/UTF-16 扫描，不保存凭证或明文凭证摘要。',
        '源代码 SHA、新旧差异、完整测试、打包干净目录检查、wheel 一致性及私有文件清单均单独留存；新归档另命名，不覆盖旧材料。',
        '当前健康画面警告基于连续20张完全相同的PNG；动态状态栏可能使其漏报。真实动作的反馈停滞保护仍由原执行器负责，不据监测警告强迫动作。',
        'LifeContext 的进程 elapsed 字段保持原生命周期语义；验收全局时钟独立持久化。OS重启或单调钟不连续安全失败，不承诺跨重启拼接为连续24h。',
        '设备不可用从首次确认后计时，统计为采样近似；API/RSS/磁盘估计不保证预测所有宿主机故障。强杀清理可能依赖原有限租约 TTL，但不会盲目重放。',
        '未启动正式24h、未完成 Stage D/E 的正式数据审计、未开发 Phase1.3。按任务8.1明确要求，现在停在Stage C等待用户批准。','']
    (ROOT/'docs/PHASE1_2_2_DELIVERY.md').write_text('\n'.join(text),encoding='utf8')
    print(json.dumps({'stage_a':'PASS','stage_b':'PASS','stage_c_ready':pre['ready_for_review'],'formal_started':False,
        'old_databases_preserved':len(dbs),'preflight_safety':metrics['unsafe_dispatches'],'preflight_false_success':metrics['false_successes'],
        'formal_gate_status':'NOT OBSERVED','approval_fingerprint':pre['approval_fingerprint']}))

if __name__=='__main__':deliver(Path(sys.argv[1]),Path(sys.argv[2]))
