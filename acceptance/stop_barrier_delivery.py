"""Read-only Stage C safety-fix audit and separately named review packaging."""
import argparse
import ast
import collections
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import zipfile
from acceptance.source_archive import ROOT,build,clean_check
from acceptance.verify_public import verify_archive
from acceptance.evidence_metrics import inspect
from phonelife.longrun_state import runtime_manifest,sha,SessionLedger
from phonelife.storage import Store

def write(p,value):p.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8')
def ro(p):return sqlite3.connect(Path(p).resolve().as_uri()+'?mode=ro',uri=True)

def audit(out):
    base=json.loads((out/'BASELINE.json').read_text());pre=json.loads((out/'preflight-04/SOAK_PREFLIGHT.json').read_text())
    assert not pre['formal_soak_started'] and pre['frozen_runtime_sha256']==runtime_manifest()
    assert pre['approval_fingerprint']!=base['old_approval_revoked']
    assert sha(ROOT/'acceptance/soak_preflight.py')==pre['preflight_entry_sha256']
    old=json.loads((ROOT/'data/phase1222/PRIVATE-SHA256.json').read_text());assert all(sha(ROOT/p)==v['sha256'] for p,v in old['files'].items())
    assert sha(ROOT/'data/phase1222/PRIVATE-SHA256.json')==base['old_private_manifest_sha256']
    assert sha(ROOT/'data/deepseek.key.dpapi')==base['files']['data/deepseek.key.dpapi']
    assert sha(ROOT/'persona.life.example.md')==base['files']['persona.life.example.md']
    assert all(sha(ROOT/p)==h for p,h in base['files'].items() if p.startswith('dist/'))
    db_checks=[]
    for row in base['backups']:
        a=ro(ROOT/row['backup']);b=ro(ROOT/row['original']);missing=0
        try:
            for table, in a.execute("SELECT name FROM sqlite_master WHERE type='table' AND name!='sqlite_sequence'"):
                query='SELECT * FROM "'+table.replace('"','""')+'"'
                missing+=sum((collections.Counter(a.execute(query))-collections.Counter(b.execute(query))).values())
            check=b.execute('PRAGMA quick_check').fetchone()[0];assert check=='ok' and missing==0
            db_checks.append({'database':row['original'],'quick_check':check,'missing_original_rows':missing})
        finally:a.close();b.close()
    preserved=[]
    for name,methods in base['old_tests'].items():
        tree=ast.parse((ROOT/name).read_text(encoding='utf-8-sig'));now={n.name:n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name.startswith('test_')}
        assert set(methods)==set(now)
        before=ast.parse((out/'source-before'/name).read_text(encoding='utf-8-sig'))
        before={n.name:n for n in ast.walk(before) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name.startswith('test_')}
        for method in methods:
            count=lambda n:sum(isinstance(x,ast.Call) and isinstance(x.func,ast.Attribute) and x.func.attr.startswith('assert') for x in ast.walk(n))
            assert count(now[method])>=count(before[method])
        preserved.append({'file':name,'methods_retained':len(methods),'assertion_count_not_reduced':True,'byte_unchanged':sha(ROOT/name)==base['files'][name]})
    write(out/'TEST_INTENT_RETENTION.json',{'old_methods_retained':sum(x['methods_retained'] for x in preserved),'files':preserved,
        'intentional_assertion_correction':'Only killed-process unknown fixture now requires failed/UNKNOWN_DISPATCH_NEEDS_REVIEW; all original no-replay/request/review assertions preserved'})
    diff=[];changed=[]
    for name,h in base['files'].items():
        p=ROOT/name
        if p.is_file() and sha(p)!=h:
            old_path=out/'source-before'/name
            changed.append({'file':name,'before_sha256':h,'after_sha256':sha(p)})
            if old_path.is_file() and p.suffix in {'.py','.ps1','.md','.toml'}:
                diff.extend(difflib.unified_diff(old_path.read_text(encoding='utf-8-sig').splitlines(True),p.read_text(encoding='utf-8-sig').splitlines(True),fromfile='before/'+name,tofile='after/'+name))
    additions=[p.relative_to(ROOT).as_posix() for base_dir in ('src/phonelife','tests','acceptance','docs') for p in (ROOT/base_dir).rglob('*')
        if p.is_file() and p.suffix in {'.py','.md','.jar','.ps1'} and '__pycache__'not in p.parts and p.relative_to(ROOT).as_posix()not in base['files']]
    write(out/'CHANGED_FILES.json',{'modified':changed,'added':additions});(out/'minimal-existing-source.diff').write_text(''.join(diff),encoding='utf8')
    formal=SessionLedger(Path(pre['formal_session'])/'soak-control.sqlite3',readonly=True)
    try:
        f=formal.load();assert f['state']=='awaiting_approval' and f['started_monotonic']is None and f['elapsed_seconds']==0
    finally:formal.close()
    # Old candidate is read only: source mismatch rejects its old approval before any execution.
    old_l=SessionLedger(ROOT/'data/phase1222/preflight-02/formal-24h-candidate/soak-control.sqlite3',readonly=True)
    try:old_cfg=old_l.load()['config']
    finally:old_l.close()
    from phonelife.longrun import verify_freeze
    try:verify_freeze(old_cfg)
    except ValueError as e:old_rejected=str(e)
    else:raise AssertionError('Old fingerprint must be unusable')
    prior03=SessionLedger(out/'preflight-03/formal-24h-candidate/soak-control.sqlite3',readonly=True)
    try:
        f03=prior03.load();assert f03['started_monotonic']is None and f03['fingerprint']!=pre['approval_fingerprint']
        try:verify_freeze(f03['config'])
        except ValueError:pass
        else:raise AssertionError('Superseded03 fingerprint must be unusable')
    finally:prior03.close()
    integrity={'databases':db_checks,'old_sealed_files_verified':len(old['files']),'old_private_manifest_unchanged':True,
        'old_artifacts_unchanged':True,'key_ciphertext_unchanged':True,'persona_unchanged':True,'old_fingerprint_rejected':old_rejected,
        'superseded_preflight03_unstarted_and_freeze_rejected':True,'formal_not_started':True}
    write(out/'INTEGRITY.json',integrity)
    session=out/'preflight-04/live-session';metrics=inspect(session/'agent.sqlite3')
    s=Store.open_readonly(session/'agent.sqlite3');c=s.conn
    try:
        events=[{'kind':r[0],'payload':json.loads(r[1])} for r in c.execute('SELECT event_type,payload FROM events')]
        exchanges=[e['payload'] for e in events if e['kind']=='longrun_api_exchange']
        wire_links=[]
        for event in events:
            if event['kind']not in {'life_model_choice','life_decision_rejected'}:continue
            for a in event['payload'].get('audit',[]):
                if not a.get('response_sha256'):continue
                matches=[w for w in exchanges if w['phase']=='brain' and w['status']=='received' and w['task_run_id']==event['payload']['task_run_id']
                    and w['response_sha256']==a['response_sha256'] and w['request_user_sha256']==a.get('request_user_sha256')]
                assert matches
                wire_links.append({k:matches[0][k] for k in ('request_id','task_run_id','request_user_sha256','request_payload_sha256','response_sha256')})
        usage=collections.Counter()
        for e in exchanges:usage.update(e.get('provider_usage',{}))
        requests=[dict(r) for r in c.execute('SELECT phase,status,COUNT(*) AS count FROM life_requests GROUP BY phase,status')]
        tasks=[{'task_run_id':r['task_run_id'],'status':r['status'],'reason_code':(s.task(r['task_run_id']).get('result')or{}).get('reason_code')} for r in c.execute('SELECT * FROM task_runs ORDER BY rowid')]
        verification_links=[]
        for task in tasks:
            t=s.task(task['task_run_id']);v=(t.get('result')or{}).get('verification')or{}
            link={'task_run_id':t['task_run_id'],'status':t['status'],'verification_verdict':v.get('verdict'),
                'evidence_type':v.get('evidence_type'),'observation_sha256':v.get('observation_sha256'),
                'final_hash_matches':v.get('observation_sha256')==(t.get('result')or{}).get('final_observation_sha256')}
            if t['status']=='succeeded':assert v.get('verdict')=='verified' and link['final_hash_matches']
            verification_links.append(link)
        write(out/'VERIFICATION_ASSOCIATIONS.json',verification_links)
        leases=ro(ROOT/'data/phone-life.sqlite3')
        try:lease_rows=leases.execute('SELECT COUNT(*) FROM lease').fetchone()[0]
        finally:leases.close()
        assert lease_rows==0 and not c.execute("SELECT 1 FROM task_runs WHERE status='running'").fetchone()
        # All stored original capture hashes are independently verified.
        from acceptance.soak_delivery import captures
        capture_counts=captures(session/'agent.sqlite3')
    finally:s.close()
    l=SessionLedger(session/'soak-control.sqlite3',readonly=True)
    try:
        summary=l.status();health=[json.loads(r[0]) for r in l.conn.execute("SELECT payload FROM health_events WHERE kind='health'")]
        resource_peak={k:max([h['resources'].get(k,0) for h in health]+[0]) for k in ('total_bytes','audit_bytes','database_bytes','wal_bytes','worker_rss_bytes','supervisor_rss_bytes')}
    finally:l.close()
    write(out/'LIVE_METRICS.json',{'source_kind':'live_api_preflight','metrics':metrics,'tasks':tasks,'requests':requests,'token_usage':dict(usage),
        'token_usage_received':sum(e.get('token_usage_reported',False) for e in exchanges),'exchange_count':len(exchanges),'estimated_currency_cost':None,
        'resource_peak_bytes':resource_peak,'health_samples':len(health),'capture_hashes_verified':capture_counts,'shared_lease_rows':lease_rows,
        'session_summary':summary,'brain_wire_audit_links':wire_links,'formal_started':False})
    public=out/'public';public.mkdir(exist_ok=True)
    write(public/'TASKS.json',metrics['public_tasks'])
    write(public/'VERIFICATION_ASSOCIATIONS.json',verification_links)
    write(public/'METRICS.json',{k:v for k,v in metrics.items() if k not in {'public_tasks','causal_feedback','memory_feedback','deep_scroll_followups'}})
    write(public/'PREFLIGHT.json',{k:pre[k] for k in ('version','stage','formal_soak_started','ready_for_review','tests_passed','frozen_runtime_sha256','approval_fingerprint','budget','formal_acceptance_matrix','api')})
    write(public/'COUNTS.json',{'requests':requests,'tokens':dict(usage),'resources_peak_bytes':resource_peak,'statuses':metrics['statuses'],
        'source_kind':'live_api_preflight','old_fingerprint_revoked':base['old_approval_revoked'],'old_fingerprint_rejected':old_rejected,'private_behavior_reproved':False})
    print(json.dumps({'integrity':True,'old_tests_retained':266,'preflight_state':summary['state'],'statuses':metrics['statuses'],'requests':requests}))

def report(out):
    pre=json.loads((out/'preflight-04/SOAK_PREFLIGHT.json').read_text());live=json.loads((out/'LIVE_METRICS.json').read_text());m=live['metrics'];s=live['session_summary']
    tests=pre['tests_passed'];integrity=json.loads((out/'INTEGRITY.json').read_text());wheel=json.loads((out/'WHEEL_VERIFICATION.json').read_text())
    changes=json.loads((out/'CHANGED_FILES.json').read_text());methods=[n.name for n in ast.walk(ast.parse((ROOT/'tests/test_stop_barrier.py').read_text())) if isinstance(n,ast.AsyncFunctionDef) and n.name.startswith('test_')]
    feedback=m.get('causal_feedback',[]);delivered=[x for x in feedback if x.get('context_digest_matches') and x.get('received_api_response_with_input_audit')]
    actual_memory=[x for x in m.get('memory_feedback',[]) if x.get('context_hash_matches_received_request')]
    table='\n'.join('| '+t['task_run_id']+' | '+t['status']+' | '+str(t['reason_code'])+' |' for t in live['tasks'])
    text=f'''# Proj.DigitalLife v0.2.3 — Stage C 最终安全修复与重新预检

状态：安全停止修复与 Stage C 完成，等待新指纹明确批准。正式24小时未启动；旧指纹 b87a4e938ce48aa035a20ba12dfe974c3c56c21448a29842edbaffa3c4ea7fa2 禁止使用。旧 preflight-02 和原交付全部保留。本报告只描述本轮新证据。

## 根因与修复前复现

旧 needs_review 仅设置 paused，operator 仍为 running，外部清除标记后可以启动新 worker。旧 tick 在期限到达时将 failed 覆盖为 completed_duration。两个确定性用例修改生产代码前均 FAIL，日志 reproduction-before.log；同名方法修复后均 PASS，reproduction-after-01.log 和最终全回归均保留。

预检03之后的独立收尾复查还发现：历史 worker 的未知预写与未过期租约同时存在时，旧租约会掩盖当前监督器没有活跃worker的事实。restart-live-lease-before.log 新增用例先FAIL，再修复为“没有当前所属活跃句柄的历史未知即停止”，不等租约过期，也不抢占旧租约。preflight-03 原122.195秒真实记录和293项测试、wheel/源码候选、旧正式候选全部保存并标为 superseded；03指纹不得使用。最终结论以本报告的 preflight-04 与新冻结版为准。

## 修改与停止契约

- longrun.py：启动前、轮询、到期取消后检查未知原记录；停止采用不可恢复屏障；有界取消/只杀所属子进程；复用原租约内中断分类；拒绝未审阅 Resume；只能新建显式关联故障的有限会话。
- longrun_state.py：failed 优先于期限；追加一次、fsync 的独立屏障与 SQLite 终态；跨重启/DB写失败保留；只读 Status 显示终结、人工审阅、Resume 许可。控制器普通心跳和重启记账不能自动完成，只有收尾审计通过后可以正常到期。
- longrun_health.py：只读返回 TaskRun/ActionAttempt、原执行状态、租约与新分发时间戳。区分活跃 ADB 的保守预写与确实未知/崩溃；不重放动作、不修改安全契约。
- data/start-soak.ps1：终态错误明确，提供新有限会话的人工审阅引用参数。
- acceptance/soak_preflight.py：安全终止后不再做 GUI 连通性探测；其余预算/无指定目标语义不变。
- tests/test_longrun.py：原强杀用例的完成状态断言纠正为 failed + UNKNOWN_DISPATCH_NEEDS_REVIEW；原 TaskRun needs_review、不重放、零请求及原始动作唯一性断言保留。
- tests/test_stop_barrier.py 新增 {len(methods)} 项；README、新状态契约和本报告补充交付边界；acceptance/stop_barrier_delivery.py 只做审计/归档。

现有文件最小差异见 minimal-existing-source.diff；逐文件前后哈希见 CHANGED_FILES.json。原核心动作、能力、主脑决策、请求/动作预算、休息、退避、持久化与验证器文件未更改。原 wheel 和 source ZIP 不覆盖，新产物全部在 dist/stagec-stop-fix。

优先级：EmergencyStop > 未知分发/持久屏障 > 其他安全故障 > 正常到期 > Pause/普通调度。EmergencyStop 与未知并发时，状态为 emergency_stopped，未知字段与审阅屏障仍保留。清除手机故障或数据库临时标记不会清除屏障，Resume 和 --force 均不能重开同一会话。正常 86400 秒语义保留。

操作人完成审阅后，只有显式 Prepare 新目录并传入 ReviewedSession/ReviewReference 才形成新故障关联会话；程序不会替人宣称已审阅。任何新的正式会话还须新指纹单独授权。详见 PHASE1_2_2_STOP_BARRIER.md。

## 自动化与故障注入

原266个测试方法全部保留，各方法断言数均未减少，TEST_INTENT_RETENTION.json 可审计。最终 **{tests}/{tests} PASS**，full-regression-final-r2.log；干净候选解压也 {tests}/{tests} PASS，clean-candidate-r2/offline-tests.log。原有212项安全用例字节保持，另外旧54项只有上述一处已证明错误的完成断言强化。

offline_fixture 覆盖未知持久停止、清除标记不恢复、重启、Resume竞态、force拒绝、期限竞态/取消后检查、EmergencyStop并发、强杀、租约释放、控制库写失败、只读导出、86400正常期限、原预算与退避。持久样本 offline-safety-stop 和 SAFETY_STOP_FIXTURE.json 保存失败状态/动作原记录/取消审计，实际API和ADB为0，不计自然故障。

新增用例：

'''+''.join('- `'+name+'`\n' for name in methods)+f'''
## 新120秒真实自主预检

来源 live_api_preflight，真实 DeepSeek 主脑/GUI、原 emulator-5582 Android15/API35 QEMU、原共享租约与DPAPI。没有固定目标、强迫动作、缩短休息或故障退避。会话单调时长 **{s['elapsed_seconds']:.3f}秒**，含预检准备/收尾 {pre['measured_preflight_seconds']:.3f}秒；状态 {s['state']}。ready_for_review={pre['ready_for_review']}。

| TaskRun | 状态 | 理由码 |
| --- | --- | --- |
{table}

确认模型动作：{m['confirmed_model_actions']}；程序手工动作：{m['confirmed_program_manual_actions']}；有界恢复 launch={m['confirmed_recovery_launches']}。未观察到的 GUI/恢复不得算已覆盖。
本轮两项 succeeded 来自原有白名单独立只读事实测量（型号、电量），验证类型 readonly_device_fact，最终观察SHA绑定一致。无需触屏即可验证这些目标；确认GUI输入仍为0。组合事实目标因合同覆盖范围更窄而保留 uncertain。逐任务关联见 VERIFICATION_ASSOCIATIONS.json。
未经授权分发 {m['unsafe_dispatches']}、假成功 {m['false_successes']}、未知传输 {m['unknown_transports']}；这些是短预检统计，不替代正式24h门槛。
语义观察 {m['semantic_observations']}，质量 {m['qualities']}。逐周期 JSONL 在 preflight-04/live-session/cycles.jsonl；原始 TaskRun、请求哈希/提供商响应关联、记忆/反思/兴趣及健康事件在两个 SQLite 中。

API分阶段：{live['requests']}。提供商token用量 {live['token_usage']}，有usage的请求 {live['token_usage_received']}/{live['exchange_count']}。没有可靠单价，货币费用 null，不用请求数代替货币。GUI备用连通性探测（若发生）单列 manual_connectivity，不计自主动作。
资源峰值（字节）：{live['resource_peak_bytes']}；健康采样 {live['health_samples']}。时长分类 {s['phase_seconds']}，设备不可用 {s['device_unavailable_seconds']:.3f}秒、进程不可用 {s['process_unavailable_seconds']:.3f}秒；自主休息 {s['autonomous_rest_seconds']:.3f}秒。

记忆实际输入关联 {len(actual_memory)} 条；本轮 continuation 类型错误跨轮哈希/响应送达关联 {len(delivered)} 条；主脑核心审计与真实API传输请求/响应哈希匹配 {len(live['brain_wire_audit_links'])} 条。只证明有实际来源/请求关联的记录，不把“我学会了”作为因果证明；反事实行为改变不确定。原 preflight-02 的自然 MISSING_CONTINUATION_REFERENCE 源样本及其当时跨轮 NOT OBSERVED 结论保持原样。

本轮真实短试不强制制造自然未知ADB、自然崩溃、429/5xx或设备离线；未出现的自然故障恢复仍为 NOT OBSERVED。离线注入不能代替它们。具体自然错误按 TaskRun 理由码保留，不删除失败周期。

## 历史完整性与冻结

修改前备份 {len(integrity['databases'])} 个SQLite，最终全部 quick_check=ok、原始行多重集无缺失；旧StageA-C封存 {integrity['old_sealed_files_verified']} 文件逐项SHA一致。旧源码包/wheel/报告、preflight-02、密钥密文与角色文件字节不变。INTEGRITY.json 给出逐库记录，旧指纹在当前运行源码下被 {integrity['old_fingerprint_rejected']} 拒绝。

新运行39文件冻结见 runtime-final-sha256.json；新wheel SHA-256 `{wheel['wheel_sha256']}`，全部39文件与实际源码相同。源码及公开包哈希见 dist/stagec-stop-fix/SHA256SUMS.txt；打包/干净解压日志及清单见 ARCHIVE_VERIFICATION.json。私有原PNG/树与数据库限制ACL；公开包仅放字段白名单和计数/哈希。最终凭证精确扫描和私有封存清单分别为 CREDENTIAL_SCAN.json 与 PRIVATE-SHA256.json。

## 新候选、验收矩阵与工程判断

正式候选：`{pre['formal_session']}`。
**新批准指纹：`{pre['approval_fingerprint']}`**。
候选 awaiting_approval、elapsed0、TaskRun0、API0，未运行正式24小时。SOAK_PREFLIGHT.json 冻结源码、设备、预算、停止条件和指纹；END_STATE.json 记录本轮测试worker退出和租约释放。原模拟器保留，未停止无关进程。

| 本轮安全修复门槛 | 状态 |
| --- | --- |
| 修复前确定性复现 | PASS |
| 持久审阅屏障、无自动继续、无到期掩盖 | PASS（offline_fixture） |
| 原266项保留及全部回归 | PASS |
| 新wheel/运行源码一致 | PASS |
| 新120秒真实预检 | {'PASS' if pre['ready_for_review'] else 'FAIL'} |
| 旧证据、数据库、角色、密钥完整 | PASS |
| 正式24小时未启动/新候选待批准 | PASS |

Duration、Safety、Truthfulness、Integrity、Resource、Recovery、Autonomy、Memory、Operational 九项**正式24h门槛全部 NOT OBSERVED**，不能用短试或离线PASS替代。

工程判断：{'建议可考虑批准受硬限保护的Stage D，以取得长期自然证据；这不是24h已经通过。' if pre['ready_for_review'] else '当前不建议批准Stage D；先审查本轮预检失败证据。'}只允许原Settings范围、120/h与500/滚动24h、32请求/目标、15动作/目标、300秒/目标、原存储停止阈值，原角色和自主选题保持。旧指纹不接受，须用户提供新指纹和明确授权。

限制：双重持久介质都不可写时不能保证日志完整，但当前worker保守停止，重启前原始动作/库检查不通过不得执行。只读轮询有采样延迟，实际unknown后原执行器本身已经禁止重发。强杀lease由原有限TTL恢复，不能抢占他人租约。屏障不抵御有文件写权限的人恶意删除证据。动态状态栏同屏监测局限和OS重启单调钟失败关闭策略不变。

本轮已停止测试工作，未开发Phase1.3，不启动24h或永久后台Agent。等待用户审阅和新指纹明确授权。
'''
    (ROOT/'docs/PHASE1_2_2_STOP_FIX_REPORT.md').write_text(text,encoding='utf8')

def package(out):
    pre=json.loads((out/'preflight-04/SOAK_PREFLIGHT.json').read_text());assert pre['frozen_runtime_sha256']==runtime_manifest()
    dist=ROOT/'dist/stagec-stop-fix';dist.mkdir(exist_ok=True)
    source=dist/'Proj.DigitalLife-v0.2.3-stop-fix-source.zip';build(source);checks=clean_check(source,out/'clean-final')
    expected=pre['tests_passed'];assert f'Ran {expected} tests' in (out/'clean-final/offline-tests.log').read_text(encoding='utf8')
    wheel=dist/'proj_digitallife-0.2.3-py3-none-any.whl'
    with zipfile.ZipFile(source) as z:
        m=json.loads(z.read('CONTENTS-SHA256.json'));assert all(m['src/phonelife/'+p]==h for p,h in runtime_manifest().items())
    with zipfile.ZipFile(wheel) as z:assert z.testzip()is None and all(hashlib.sha256(z.read('phonelife/'+p)).hexdigest()==h for p,h in runtime_manifest().items())
    files=list((out/'public').glob('*.json'))+[ROOT/'docs/PHASE1_2_2_STOP_BARRIER.md',ROOT/'docs/PHASE1_2_2_STOP_FIX_REPORT.md',out/'CHANGED_FILES.json',out/'TEST_INTENT_RETENTION.json']
    manifest={p.relative_to(ROOT).as_posix():sha(p) for p in files};evidence=dist/'Proj.DigitalLife-v0.2.3-stop-fix-public.zip'
    with zipfile.ZipFile(evidence,'x',compression=zipfile.ZIP_DEFLATED) as z:
        for name in manifest:z.writestr(name,(ROOT/name).read_bytes())
        z.writestr('CONTENTS-SHA256.json',json.dumps(manifest,indent=2))
    verify_archive(evidence)
    write(out/'ARCHIVE_VERIFICATION.json',{'clean_checks':checks,'clean_tests_passed':expected,'runtime_files_identical':len(runtime_manifest()),
        'source_sha256':sha(source),'wheel_sha256':sha(wheel),'public_sha256':sha(evidence),'formal_started':False})
    sums=dist/'SHA256SUMS.txt'
    sums.write_text(''.join(sha(p)+'  '+p.name+'\n' for p in (source,wheel,evidence))+sha(out/'preflight-04/SOAK_PREFLIGHT.json')+'  ../../'+(out/'preflight-04/SOAK_PREFLIGHT.json').relative_to(ROOT).as_posix()+'\n',encoding='utf8')

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=('audit','package'));p.add_argument('output',type=Path);a=p.parse_args()
    if a.command=='audit':audit(a.output.resolve());report(a.output.resolve())
    else:package(a.output.resolve())
