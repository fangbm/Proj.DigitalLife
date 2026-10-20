# v0.2.3 Stage D 前持久停止屏障补丁

本补丁只做必要安全修复、offline_fixture 测试和本地归档。没有新的真实 API 预检、AVD 操作或正式 Soak。旧 preflight-04 原件保留，其批准指纹 `c2fb482ad56efcd9c4cfaebf80d51976f4abca1f8f18c24467099a88a69ee6b2` 对新运行代码失效；不得复用旧候选启动。

## 确认范围

静态确认：旧 `SessionLedger.latch_review` 的 marker open/write/flush/fsync 异常可跳过 SQLite 事务。修改生产代码前，独立临时 SQLite/mock worker 的 28 个新探针产生14个断言失败、3个错误；PermissionError/OSError 创建失败后，实际 SQLite 保持 prepared。原日志及当时测试源码保存于 `data/phase1222-persistence-fix-01`。flush/fsync 的错误包含旧版缺少新诊断字段，不能把全部错误数量都称为同一种控制流复现。

这是离线故障注入，不证明真实手机发生过自动恢复或越权分发。真实 API 已观察行为仍只来自旧 preflight-04 等历史记录；本补丁没有新增自然故障证据。

## 两条独立写入路径

`longrun_state.py` 保留 exclusive `open('x')`、flush、fsync 的 append-once marker；PermissionError/OSError 被记录为操作阶段、异常类型、errno/winerror，随后仍尝试 SQLite 事务。SQLite 终态显式保存 safety_stop、requires_manual_review、stop 信号和诊断，不再依赖 marker 存在才置 failed。写事务内重读既有 safety_stop，避免另一停止写入者的首次事件被覆盖。

读取时，SQLite 屏障、当前进程待提交屏障、独立 marker 任一路径均要求停止。SQLite 中首次事件在冲突时优先；坏 JSON、空对象、错会话 marker 都保守停止。EmergencyStop 状态优先于 failed，但未知分发的审阅要求保留；正常到期不能覆盖任何安全终态。

`longrun.py` 继续在 finally 有界停止所属 Popen 句柄；收尾审计增加两条持久化路径的状态。直接 worker 入口新增安全数据库可读性检查，在适配器创建前拒绝无法核验的状态。审阅后的新关联会话支持 SQLite-only 屏障的 canonical 哈希，不要求故障 marker 必须存在；原会话仍禁止 Resume。关联字段仍是显式运维接口，不是全球强制审阅真实性认证。

## 故障矩阵

| marker | SQLite 写入 | 当前监督器 | 关闭/新进程后 |
|---|---|---|---|
| 创建前 PermissionError / OSError | 成功 | 有界停所属 worker；SQL failed+具体诊断 | SQL 独立屏障禁止恢复 |
| 半写/flush/fsync 失败 | 成功 | 不替换半成品；SQL failed | SQL 屏障生效；残缺 marker 也保守阻止 |
| 成功/既有有效文件 | 失败 | 有界停所属 worker；保留独立原件 | marker 禁止恢复 |
| 既有损坏/空/错会话文件 | 成功或失败 | 保守停止，不自动修复原件 | marker 存在即保守停止 |
| 不存在且创建失败 | 失败 | 内存待审阅屏障；只终止所属子进程；不无限重试 | 无法保证该新故障事件已持久保存；必须重新核验原 TaskRun/ActionAttempt、租约、控制库 |
| SQL 有屏障，marker 缺失或不同 | 可读 | SQL 首次事件保持；不恢复 | SQL 屏障持续生效 |
| 控制/任务/租约数据库无法核验 | 不明 | 不创建新 worker | 拒绝运行，等待人工修复与审阅 |

实测矩阵 JSON 为 `full-cases-final`，记录实际 SQL、marker 哈希/有效性、worker 收尾、requires_manual_review/resume_allowed、跨进程结果、原始行保留及零新请求/重放。原294个测试文件不修改；新测试独立于真实 AVD/API。

## 原子性、Windows 与幂等

没有增加 replace/rename 操作。Windows 的独占创建不允许覆盖已存在文件；创建后半写可能留下空/部分文件，所有读取均失败关闭。首次 marker 字节不重写；重复触发保留第一事件来源，事务内再次核验 SQL 首次事件，避免陈旧初读覆盖并发提交。事务失败回滚，测试检查 in_transaction=false。

每次所属 worker 收尾维持原有有限取消/kill 等待，不改变休息、退避、预算和租约。停止检查依赖审计时间戳，不能撤回已交给 ADB 的动作，也不控制不遵守共享租约的外部人工 ADB。

## 明确限制与下一步

若 marker 与 SQLite 都无法写，内存屏障只保证当前进程停止；系统崩溃后不能凭空恢复丢失事件。重启时原未知 ActionAttempt/TaskRun 仍能触发停止；这些历史或控制状态也无法可靠读取时，入口必须拒绝创建 worker。若所有介质同时丢失或被外部篡改成“干净”，本方案不宣称能识别已遗失事实。

同一物理磁盘上的 SQLite 与 marker 不等于独立硬件容错。fsync/SQLite commit 的验证以当前 OS/进程重启语义为限，不是断电或目录元数据持久化证明。文件系统、内核或硬件同时失效时，无法保证保留完整事件。禁止用通过的离线测试将正式24小时九项门槛改成 PASS。

运行文件已变，工程上需要新的 Stage C 真实预检；建议另行批准后在新目录按原预算、原隔离 AVD/DPAPI/共享租约运行120秒，保持自然休息和自主选题，不指定 GUI 目标。该建议未获实施批准，本轮不运行、不生成正式候选，也不启动 Stage D。

fixture 的两份树及 PROVENANCE 字节保留；完整层级公开例外仍未授权。新源码 ZIP 只是本地审查原件，不向 GitHub 或其他公开服务上传。最终结果、哈希与完整证据以 `dist/stagec-persistence-fix-01/FINAL_REPORT.md` 和 Manifest 为准。
