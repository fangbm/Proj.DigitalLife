# v0.2.3 Stage C 前 Status / 准入契约

本轮仅修正只读状态与必要的执行入口一致性；没有真实 API/AVD 预检、正式会话、新应用或权限。P0持久停止逻辑、预算、休息、退避、租约、主脑决策、continuation与GUI协议不变。旧报告不改写，旧候选不重置。

## 根因及确定性复现

旧 Status 只读控制库及marker，没有结合Resume/supervisor/worker所使用的原始TaskRun/ActionAttempt。双写失败后，控制库可以仍prepared而原始unknown证据明确禁止执行；旧Status却resume_allowed=true。新离线用例在改生产前实际FAIL，跨进程Status与Resume拒绝记录保存在 data/stagec-status-contract-01/reproduction-before.log 及 before-case/dual-write-cross-process.json。这是报告不一致，不是已经发生的真实越权分发。

## 字段语义

| 字段 | 含义 |
|---|---|
| control_state | 通过新只读连接读取的物理控制库state，不用推断重写它 |
| state | 兼容旧接口的load/marker/当前进程屏障投影视图；它不是原始控制库状态 |
| effective_state | 合并安全证据后的展示状态；history-only unknown为blocked |
| safety_terminated | 控制库安全屏障或有效独立marker已有可读取持久证据；只发现unknown或坏marker不伪称已提交终态 |
| persistent_barrier_present | 控制库屏障或独立文件存在；坏marker仍保守阻止，但不认证其事件内容 |
| requires_manual_review | 当前屏障/原始unknown证据要求审阅，含当前进程未成功落盘的停止要求 |
| effective_resume_allowed / resume_allowed | 同一个实际Resume控制准入布尔值，不是初次启动正式会话的授权 |
| worker_launch_allowed | 当前生命周期允许创建worker，还受已启动、暂停/stop信号、活跃worker、租约、期限、唤醒和预算约束 |
| formal_approval_required | 尚未启动的正式来源会话需要单独批准指纹 |
| initial_start_allowed_without_approval | 仅健康、非正式来源、未启动会话可无需正式批准开始；不由Status执行 |
| admission_reason_code / admission_evidence | 明确的拒绝原因、控制库/marker/历史证据分类及读取错误类型 |

正常prepared可允许Resume控制操作，但worker_launch_allowed=false，因为未启动；awaiting_approval还明确formal_approval_required=true、initial_start_allowed_without_approval=false。正常paused可接收Resume但不能直接运行worker；恢复控制与生命周期调度不是同一件事。Status不会获取租约或消耗批准指纹。

双写失败但原unknown仍在：control_state=prepared、effective_state=blocked、safety_terminated=false、requires_manual_review=true、两个执行许可false、原因UNKNOWN_DISPATCH_NEEDS_REVIEW。控制/历史无法核验：有效许可false、原因SAFETY_EVIDENCE_UNAVAILABLE，不编造新安全事实。EmergencyStop最高优先级。

## 单一判定依据

`longrun_health.safety_admission` 是共用判定；unknown分类继续来自原 readonly_db_health，没有另造三套unknown检测。SessionLedger.status、Resume、supervisor启动/循环/到期、直接worker调用同一判定并保留原有执行前检查。执行入口仍重新读取证据；Status只是时点快照，不发放可缓存的执行令牌。

Status/Resume使用记录PID的只读活性观察；supervisor以实际Popen句柄判断自己拥有的worker，直接worker沿用“没有可证明的既有in-flight所有者”的保守上下文。活跃worker存在时Status不允许再启动一个worker；历史未知和当前活跃动作的区分仍遵守原审查契约，不将所有正常预写都称为真实运输故障。

## 只读性与读取边界

控制库一律新建mode=ro、query_only连接，SQLite busy timeout 0.25秒，VM进度回调0.75秒预算；历史和租约沿用mode=ro/query_only，各0.5秒busy timeout，并加1秒VM/整批检查预算。marker读取上限1Mi字符，超限失败关闭。没有迁移、checkpoint、marker补写、SQL审计补写、设备适配器、模型调用、ADB或所有权租约操作。

这些是协作式读取上限，不是内核/文件系统无限挂起下的硬实时保证；单次JSON解码/文件系统调用不受SQLite VM回调中断。读取失败只报告不可核验，不补写“failed”到历史。CLI缺失/损坏控制库也输出blocked JSON而不创建空库。

测试比较全部数据库行和主文件/现有WAL/marker哈希；SQLite易变SHM锁索引不作字节相同承诺。采集器先读取行，再记录同一观察基线的文件哈希，并自检连续采集一致。SQLite的只读WAL打开可能物化空WAL/SHM辅助文件；不将这种锁索引行为误报为应用数据更新，也不使用immutable绕过运行中数据库的一致性保护。参考 [SQLite WAL官方说明](https://www.sqlite.org/wal.html#read_only_databases)。没有承诺“磁盘上绝对零元数据操作”。

## 限制与交付边界

双介质都写不成时，原停止事件仍不具备凭空的跨崩溃持久保证；新Status只如实展示原始证据和有效拒绝，不改写P0边界。多数据库读取不是分布式原子快照，后续执行必须重新准入。控制库完全不可读时批准/终态信息可能unknown，所有执行许可都false。

运行文件变化使旧指纹对新代码无效；不能复用旧候选。先完成本地冻结、离线复核，再等待用户单独批准120秒真实Stage C。正式24小时九项门槛保持NOT OBSERVED。完整UI树fixture原字节不变，公开例外尚未批准，不发布新源码包或推送GitHub。最终结果以新 dist/stagec-status-contract-01/FINAL_REPORT.md 与清单为准。
