# Proj.DigitalLife v0.2.3 — Stage C 最终安全修复与重新预检

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
- tests/test_stop_barrier.py 新增 28 项；README、新状态契约和本报告补充交付边界；acceptance/stop_barrier_delivery.py 只做审计/归档。

现有文件最小差异见 minimal-existing-source.diff；逐文件前后哈希见 CHANGED_FILES.json。原核心动作、能力、主脑决策、请求/动作预算、休息、退避、持久化与验证器文件未更改。原 wheel 和 source ZIP 不覆盖，新产物全部在 dist/stagec-stop-fix。

优先级：EmergencyStop > 未知分发/持久屏障 > 其他安全故障 > 正常到期 > Pause/普通调度。EmergencyStop 与未知并发时，状态为 emergency_stopped，未知字段与审阅屏障仍保留。清除手机故障或数据库临时标记不会清除屏障，Resume 和 --force 均不能重开同一会话。正常 86400 秒语义保留。

操作人完成审阅后，只有显式 Prepare 新目录并传入 ReviewedSession/ReviewReference 才形成新故障关联会话；程序不会替人宣称已审阅。任何新的正式会话还须新指纹单独授权。详见 PHASE1_2_2_STOP_BARRIER.md。

## 自动化与故障注入

原266个测试方法全部保留，各方法断言数均未减少，TEST_INTENT_RETENTION.json 可审计。最终 **294/294 PASS**，full-regression-final-r2.log；干净候选解压也 294/294 PASS，clean-candidate-r2/offline-tests.log。原有212项安全用例字节保持，另外旧54项只有上述一处已证明错误的完成断言强化。

offline_fixture 覆盖未知持久停止、清除标记不恢复、重启、Resume竞态、force拒绝、期限竞态/取消后检查、EmergencyStop并发、强杀、租约释放、控制库写失败、只读导出、86400正常期限、原预算与退避。持久样本 offline-safety-stop 和 SAFETY_STOP_FIXTURE.json 保存失败状态/动作原记录/取消审计，实际API和ADB为0，不计自然故障。

新增用例：

- `test_unknown_marker_cleared_must_not_start_worker_or_complete`
- `test_failure_at_deadline_must_not_be_completed_duration`
- `test_unknown_stop_is_durable_and_has_typed_audit`
- `test_clearing_agent_marker_cannot_resume_stopped_session`
- `test_restart_preserves_stop_and_status_is_readonly`
- `test_force_cli_cannot_bypass_review_barrier`
- `test_direct_worker_cannot_bypass_existing_stop`
- `test_unknown_at_expired_restart_does_not_complete`
- `test_unknown_and_emergency_preserve_both_with_emergency_priority`
- `test_emergency_wins_normal_expiry`
- `test_unknown_overrides_pause_and_resume`
- `test_resolved_device_offline_cannot_clear_review`
- `test_legacy_terminal_db_stop_also_rejects_resume`
- `test_control_db_write_failure_leaves_independent_stop`
- `test_append_once_barrier_ignores_db_state_reclassification`
- `test_normal_86400_elapsed_still_completes_without_fault`
- `test_exports_and_status_never_rewrite_original_attempt`
- `test_new_linked_session_preserves_old_stop_and_requires_reference`
- `test_live_predispatch_journal_is_not_a_confirmed_transport_failure`
- `test_owned_worker_crash_latches_unknown_even_with_live_lease`
- `test_graceful_owned_child_cancels_and_releases_lease`
- `test_unresponsive_owned_worker_has_bounded_kill`
- `test_expiry_health_race_still_checks_unknown_after_cancel`
- `test_controller_heartbeat_cannot_bypass_final_health_audit`
- `test_repeated_review_cannot_replace_original_trigger`
- `test_unreadable_barrier_fails_closed_without_repair`
- `test_resume_cannot_race_supervisor_before_latch`
- `test_historical_pending_under_live_lease_requires_review_before_deadline`

## 新120秒真实自主预检

来源 live_api_preflight，真实 DeepSeek 主脑/GUI、原 emulator-5582 Android15/API35 QEMU、原共享租约与DPAPI。没有固定目标、强迫动作、缩短休息或故障退避。会话单调时长 **120.030秒**，含预检准备/收尾 120.924秒；状态 completed_duration。ready_for_review=True。

| TaskRun | 状态 | 理由码 |
| --- | --- | --- |
| 2f4415dc-1798-4228-be1a-497d86d898f2 | uncertain | GOAL_NOT_VERIFIED |
| 0bf8a794-4076-4ecc-b4e3-2878bca1fb81 | succeeded | GOAL_VERIFIED |
| f9c5bbd5-57ff-40e6-8c48-de6011cc1bf9 | succeeded | GOAL_VERIFIED |

确认模型动作：{}；程序手工动作：{}；有界恢复 launch=0。未观察到的 GUI/恢复不得算已覆盖。
本轮两项 succeeded 来自原有白名单独立只读事实测量（型号、电量），验证类型 readonly_device_fact，最终观察SHA绑定一致。无需触屏即可验证这些目标；确认GUI输入仍为0。组合事实目标因合同覆盖范围更窄而保留 uncertain。逐任务关联见 VERIFICATION_ASSOCIATIONS.json。
未经授权分发 0、假成功 0、未知传输 0；这些是短预检统计，不替代正式24h门槛。
语义观察 9，质量 {'trusted': 9}。逐周期 JSONL 在 preflight-04/live-session/cycles.jsonl；原始 TaskRun、请求哈希/提供商响应关联、记忆/反思/兴趣及健康事件在两个 SQLite 中。

API分阶段：[{'phase': 'brain', 'status': 'received', 'count': 4}, {'phase': 'gui', 'status': 'received', 'count': 3}, {'phase': 'reflection', 'status': 'received', 'count': 3}]。提供商token用量 {'prompt_tokens': 52102, 'completion_tokens': 2627, 'total_tokens': 54729, 'prompt_cache_hit_tokens': 10240, 'prompt_cache_miss_tokens': 41862}，有usage的请求 10/10。没有可靠单价，货币费用 null，不用请求数代替货币。GUI备用连通性探测（若发生）单列 manual_connectivity，不计自主动作。
资源峰值（字节）：{'total_bytes': 2343706, 'audit_bytes': 138135, 'database_bytes': 495616, 'wal_bytes': 444992, 'worker_rss_bytes': 46350336, 'supervisor_rss_bytes': 34775040}；健康采样 4。时长分类 {'fault_backoff': 28.627669399997103, 'running': 59.46281799999997, 'sleeping': 31.939283500003512}，设备不可用 0.000秒、进程不可用 0.000秒；自主休息 0.000秒。

记忆实际输入关联 2 条；本轮 continuation 类型错误跨轮哈希/响应送达关联 1 条；主脑核心审计与真实API传输请求/响应哈希匹配 4 条。只证明有实际来源/请求关联的记录，不把“我学会了”作为因果证明；反事实行为改变不确定。原 preflight-02 的自然 MISSING_CONTINUATION_REFERENCE 源样本及其当时跨轮 NOT OBSERVED 结论保持原样。

本轮实际自然产生1个 INVALID_LIFE_DECISION_SCHEMA 候选拒绝，主脑在原有有界修复预算内修复同一周期候选后继续。提供商请求/响应哈希与核心拒绝记录吻合；这是 live_api_preflight 内的 live_api_natural_occurrence，不是注入，也不等于自然跨轮错误送达。具体字段与请求关联见 NATURAL_REJECTIONS.json。

本轮真实短试不强制制造自然未知ADB、自然崩溃、429/5xx或设备离线；未出现的自然故障恢复仍为 NOT OBSERVED。离线注入不能代替它们。具体自然错误按 TaskRun 理由码保留，不删除失败周期。

## 历史完整性与冻结

修改前备份 44 个SQLite，最终全部 quick_check=ok、原始行多重集无缺失；旧StageA-C封存 1488 文件逐项SHA一致。旧源码包/wheel/报告、preflight-02、密钥密文与角色文件字节不变。INTEGRITY.json 给出逐库记录，旧指纹在当前运行源码下被 RUNTIME_FREEZE_MISMATCH 拒绝。

新运行39文件冻结见 runtime-final-sha256.json；新wheel SHA-256 `7ae46400c2e363669271b0f6714df71a60a7276bd2774da0db153a5b0398fb55`，全部39文件与实际源码相同。源码及公开包哈希见 dist/stagec-stop-fix/SHA256SUMS.txt；打包/干净解压日志及清单见 ARCHIVE_VERIFICATION.json。私有原PNG/树与数据库限制ACL；公开包仅放字段白名单和计数/哈希。最终凭证精确扫描和私有封存清单分别为 CREDENTIAL_SCAN.json 与 PRIVATE-SHA256.json。

## 新候选、验收矩阵与工程判断

正式候选：`D:\codex\Proj.DigitalLife\data\phase1222-stop-fix\preflight-04\formal-24h-candidate`。
**新批准指纹：`c2fb482ad56efcd9c4cfaebf80d51976f4abca1f8f18c24467099a88a69ee6b2`**。
候选 awaiting_approval、elapsed0、TaskRun0、API0，未运行正式24小时。SOAK_PREFLIGHT.json 冻结源码、设备、预算、停止条件和指纹；END_STATE.json 记录本轮测试worker退出和租约释放。原模拟器保留，未停止无关进程。

| 本轮安全修复门槛 | 状态 |
| --- | --- |
| 修复前确定性复现 | PASS |
| 持久审阅屏障、无自动继续、无到期掩盖 | PASS（offline_fixture） |
| 原266项保留及全部回归 | PASS |
| 新wheel/运行源码一致 | PASS |
| 新120秒真实预检 | PASS |
| 旧证据、数据库、角色、密钥完整 | PASS |
| 正式24小时未启动/新候选待批准 | PASS |

Duration、Safety、Truthfulness、Integrity、Resource、Recovery、Autonomy、Memory、Operational 九项**正式24h门槛全部 NOT OBSERVED**，不能用短试或离线PASS替代。

工程判断：建议可考虑批准受硬限保护的Stage D，以取得长期自然证据；这不是24h已经通过。只允许原Settings范围、120/h与500/滚动24h、32请求/目标、15动作/目标、300秒/目标、原存储停止阈值，原角色和自主选题保持。旧指纹不接受，须用户提供新指纹和明确授权。

限制：双重持久介质都不可写时不能保证日志完整，但当前worker保守停止，重启前原始动作/库检查不通过不得执行。只读轮询有采样延迟，实际unknown后原执行器本身已经禁止重发。强杀lease由原有限TTL恢复，不能抢占他人租约。屏障不抵御有文件写权限的人恶意删除证据。动态状态栏同屏监测局限和OS重启单调钟失败关闭策略不变。

本轮已停止测试工作，未开发Phase1.3，不启动24h或永久后台Agent。等待用户审阅和新指纹明确授权。
