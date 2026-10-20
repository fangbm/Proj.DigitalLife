# v0.2.3 Stage C：不可恢复的安全停止契约

本轮仅修复监督器边界，不改变动作协议、能力、模型选题、休息、退避、API 配额或 Settings 范围。旧 Stage A–C 报告和 preflight-02 保持原始结论。正式 24 小时仍须另行批准新指纹。

## 根因与最小修复

旧监督器将 needs_review 映射到 paused，但 operator 仍为 running；数据库标记消失后可以再次创建 worker。SessionLedger.tick 又会将到期时的 failed 请求覆盖为 completed_duration。修复前两个确定性离线用例均 FAIL，原始日志保留在新证据目录 reproduction-before.log。

新监督器在启动、常规轮询、期限收尾后检查原始动作记录。未知分发触发 UNKNOWN_DISPATCH_NEEDS_REVIEW，写入会话 failed 终态，另有与控制库同名的 `.review-stop.json` 追加一次屏障。屏障先落盘并 fsync，再写控制 SQLite；控制库写失败时仍先停止拥有的子进程，重新打开会话仍被屏障阻止。屏障内容损坏也保守停止，不自动修复或删除。

原执行器在 ADB 前暂存 uncertain_after_transport_error，因此本监督器仍持有活跃 worker 时的瞬时预写不能直接当成真实失败。已终结任务、明确 transport_error、已退出的所属 worker，或当前监督器没有活跃 worker 句柄时的历史未知记录都会触发屏障，即使旧租约尚未过期。租约仍有效不等于传输结果已确认。原始 TaskRun/ActionAttempt/observation 不删除；必要的中断分类复用原 recover_interrupted，并且必须取得原共享租约，不抢占旧 owner。

## 冲突优先级

1. EmergencyStop 最高，状态 emergency_stopped 保持粘滞；并发 unknown 仍持久化完整待审阅原因。
2. 未知分发或已有停止屏障产生 failed + UNKNOWN_DISPATCH_NEEDS_REVIEW，优先于期限、Pause、Resume、唤醒和设备重新上线。
3. 资源、身份和数据库安全故障优先于普通到期。
4. 无上述阻断时，先停止所属 worker，再重新读取安全记录，才可 completed_duration。普通心跳、重启计时和异常路径不再能提前完成。
5. Pause 是可恢复的人工暂停；不可代替安全终止。控制库读取不可用时不创建 worker；写入失败则终止所属 worker，不等待无限审计重试。

worker 接收持久取消信号，最多等待60秒；控制库信号写失败则立即 terminate 并最多等待15秒；未结束时 kill，仅针对本控制器持有的 Popen 子进程，再等最多15秒。绝不按保存的 PID 杀未知进程。强杀后的租约依赖原有限 TTL，不抢占他人租约，不重发旧动作。无法确认子进程结束时不得正常完成。

## Status 与授权

Status 使用 SQLite 只读模式，显示 safety_terminated、reason、requires_manual_review、resume_allowed、session_terminated 及原始安全源记录。Resume 对终态明确报错；不存在 --force 绕过入口。worker 自身在创建 Android/API 适配器前检查屏障和历史 unknown。

人工完成审阅后只能 Prepare 一个新的有限会话，并显式传入 `--reviewed-session` 与 `--review-reference`（PowerShell：ReviewedSession / ReviewReference）。新配置绑定旧 session_id、指纹和屏障 SHA，旧会话不改为可运行。此审阅引用是操作人声明的审阅凭据，程序不会自动宣称人工已审阅。新的正式 API 会话仍须独立批准自己的指纹。

屏障不可视为抵御拥有文件写权限的恶意操作者；操作人不得删除屏障或直接改库绕过。独立 SQLite 与屏障同时无法持久化（例如整个磁盘写失败）时，立即停止当前子进程，保留未决原始动作；启动仍先审阅原始数据库，数据库不可读则禁止执行。恢复磁盘后应人工审查并新建会话，不承诺抵御全部持久介质损坏。

## 审计边界

触发记录含 session_id、TaskRun/ActionAttempt ID、原状态、原因、UTC/单调时钟、所属 worker PID、租约与未知传输标记；收尾记录 review-stop-result.json 保存停止方式/退出码/租约、新分发日志检查、old_actions_replayed=0 和人工审阅要求。日志不可读时新分发检查明确为 unavailable，不将缺失证据当成零。

故障注入统一标为 offline_fixture；真实短试单列 live_api_preflight。原 preflight-02 自然 continuation 源周期不重分类，跨轮送达未观察结论不改写。正常短试也不能证明24小时或所有真实故障恢复。
