# Proj.DigitalLife v0.2.3 / Phase 1.2.2 — Stage A-C 交付报告

**状态：Stage A、B 完成，Stage C 真实预检通过，停在等待明确批准。正式 24 小时 Soak 未启动，不能宣布 Phase 1.2.2 验收通过。**

## 已完成工作

- M0：非敏感验证依赖迁入 acceptance，新增公开完整性模式与只读私有复验；全新解压目录原 212 项通过，旧源码包保留。
- M1-M3：新增持久全局时钟、有限 worker 监督、单实例、共享租约、只读健康、预算窗口等待、资源硬停止、异常收尾及 EmergencyStop 粘滞。
- M4：逐周期 SQLite/JSONL 去重导出，候选/动作/恢复/休息/验证/记忆/反思/兴趣及 API/资源分开统计。
- 原 212 项测试文件/意图保持，新增 54 项，完整回归 **266/266 PASS**。
- 原 36 冻结生产文件中 34 个字节不变，另外两个仅更新版本文字；原动作、能力、决策、持久化和独立验证未放宽。新增运行模块 4 个，wheel 内 39 个运行文件与冻结源码一致。

架构、状态、限制与逐文件作用见 [长期运行管理](PHASE1_2_2_LONGRUN.md)；公开与私有验证边界见 [源码验证说明](SOURCE_VERIFICATION.md)。

## 真实短时预检

原 Android 15/API35 隔离 AVD，serial=emulator-5582，QEMU 身份检查通过；既有 DPAPI 只读加载，真实 DeepSeek 主脑和 GUI API 可达。没有指定目标、没有 force 唤醒、没有缩短休息。会话单调时长 **120.034 秒**；含预检收尾共 120.642 秒。
结果：{'uncertain': 2, 'failed': 1}；模型确认动作 {}。本次没有确认的 tap/swipe/back，不能声称获得本轮 GUI 行动覆盖。没有为了增加动作强迫模型。
未经授权 0，假成功 0，未知传输 0。所有 failed/interrupted 均按实际原因保留，不删除或改成成功。
主脑实际选择 {'explore': 2}；候选 5，候选校验 {'valid': 2, 'rejected': 2}；分阶段请求 [{'phase': 'brain', 'status': 'received', 'count': 4}, {'phase': 'gui', 'status': 'received', 'count': 2}, {'phase': 'reflection', 'status': 'received', 'count': 2}]。
语义观察 8，质量 {'trusted': 8}，恢复状态 {}。未出现的恢复、动态页面或停滞覆盖不能写成自然故障已通过。
实际主脑输入哈希匹配的跨轮记忆关联 2 条，反思引用和兴趣变化保存在私有 SQLite/JSONL；不声称反事实学习已证明。

| TaskRun | 状态 | 决策 | 理由码 | API |
| --- | --- | --- | --- | --- |
| `36df6e3c-f527-4cd0-858e-2ecd790c0c46` | uncertain | explore | GOAL_NOT_VERIFIED | 3 |
| `18b2f75e-50d1-427c-b309-b1ae76a2ed3a` | uncertain | explore | EXECUTOR_UNCERTAIN | 3 |
| `b23aac0c-3f73-4b6f-855a-dfe643125953` | failed | unavailable | MISSING_CONTINUATION_REFERENCE | 2 |

### 新的自然 continuation 错误证据

本次短预检观察到 **1 个自然 MISSING_CONTINUATION_REFERENCE 源周期**，不是受控替换。两次真实提供商响应哈希均与冻结主脑拒绝候选 SHA、请求行和审计哈希吻合；源 TaskRun 和情景记忆保存了具体字段，源周期没有目标行或 GUI 输入。详见 NATURAL_CONTINUATION_SAMPLES.json。
原 v0.2.2 自然样本未观察的历史结论不改写；这是 v0.2.3 新样本。它只补充自然错误源头被观察，不补充完整自然跨轮送达：全局120秒会话随后按退避到期，没有后续主脑请求。不能把同周期的结构化修复当成下一周期，也不强制唤醒模型以补足统计。正式24h的 Recovery/Memory 仍为 NOT OBSERVED。

## 时钟与资源

互斥状态时长：{'fault_backoff': 67.15750119999575, 'running': 52.87612490000902}；设备不可用 0.000s，进程不可用 0.000s（后两项次级时间不再加到总时长）。模型自主休息 0.000s，程序强制休息 0.000s。
资源采样峰值（字节）：{'total_bytes': 2388007, 'audit_bytes': 131459, 'database_bytes': 475136, 'wal_bytes': 469712, 'worker_rss_bytes': 46555136, 'supervisor_rss_bytes': 34340864}；结束剩余磁盘 157728100352 字节。WAL/证据预算和请求硬限见 SOAK_PREFLIGHT。
提供商 token 汇总 {'prompt_tokens': 42532, 'completion_tokens': 2123, 'total_tokens': 44655, 'prompt_cache_hit_tokens': 11648, 'prompt_cache_miss_tokens': 30884}；有 usage 的请求 8/8，缺失项保留 unknown。未配置可靠单价，货币费用为 null，公式与假设单列，不将请求数当费用。

## 故障与回归边界

离线受控覆盖 API timeout、429/5xx、ADB 不可用/设备 offline、真实 mock 子进程强杀、unknown 不重放、SQLite 写失败/损坏、磁盘/WAL/RSS 阈值、租约竞争、Pause/Resume/EmergencyStop、冻结篡改、时钟重启和去重。它们统一属于 offline_fixture，不混入正式自然运行。
开发阶段的 smoke-01 Path 类型异常、离线时间重复计数失败及后续修复日志保留，最终测试通过不抹去开发失败记录。
第一次120秒预检和其候选wheel/source保留为 preflight-01；随后补充内部 worker 必须已有启动会话的边界检查，在测试完成后更新代码，再对新冻结版本进行独立 preflight-02。未在任何运行中的测试中修改执行源码。
本次自然短预检没有观察到 API 限流、设备离线、自然进程崩溃或未知分发；这些真实恢复覆盖为 NOT OBSERVED。正常截止取消是有界停止证据，不是自然崩溃恢复。

## 24 小时正式验收矩阵

| 门槛 | 当前正式状态 |
| --- | --- |
| Duration | NOT OBSERVED |
| Safety | NOT OBSERVED |
| Truthfulness | NOT OBSERVED |
| Integrity | NOT OBSERVED |
| Resource | NOT OBSERVED |
| Recovery | NOT OBSERVED |
| Autonomy | NOT OBSERVED |
| Memory | NOT OBSERVED |
| Operational | NOT OBSERVED |

正式会话还没有起点、周期 JSONL 或任何实际 API 请求。短试 completed_duration 只对应 120 秒，不可替代 86400 秒或整体成功。
候选会话：`D:\codex\Proj.DigitalLife\data\phase1222\preflight-02\formal-24h-candidate`，state=awaiting_approval。冻结批准指纹：`b87a4e938ce48aa035a20ba12dfe974c3c56c21448a29842edbaffa3c4ea7fa2`。

## 拟批准的范围

真实单调目标 86400 秒，休息/配额等待照常计时，不安排每日目标。原 AVD、Settings 只读、既有 DPAPI、原共享协调库；120 请求/滚动小时、500/滚动24h、32/目标、15动作/目标、300秒/目标，更低值不得提高。新证据总预算4GiB，保留5GiB空闲，WAL128MiB、DB512MiB、审计64MiB、每进程RSS512MiB。
停止条件包括期限、EmergencyStop、未知分发待审阅、资源阈值、QEMU身份变化、源码/配置/角色冻结变化、持续数据库失败和有限重启上限。正常停止后不保留长期后台 Agent。

## 完整性、归档与限制

修改前备份 30 个 SQLite；最终全部 quick_check=ok，原记录多重集完整。旧 v0.2.2 的两个私有清单逐项核验、旧报告/源码ZIP/wheel/公开证据/v1-v2 定义保持，密钥密文与角色字节不变。
新短试的原图/完整树在限制 ACL 的私有会话目录；公开包仅有 allowlist TaskRun、哈希、状态、预算和计数，不放原 PNG/树/SQLite 或未经筛选模型文本。实际凭证会在内存中做 UTF-8/UTF-16 扫描，不保存凭证或明文凭证摘要。
源代码 SHA、新旧差异、完整测试、打包干净目录检查、wheel 一致性及私有文件清单均单独留存；新归档另命名，不覆盖旧材料。
当前健康画面警告基于连续20张完全相同的PNG；动态状态栏可能使其漏报。真实动作的反馈停滞保护仍由原执行器负责，不据监测警告强迫动作。
LifeContext 的进程 elapsed 字段保持原生命周期语义；验收全局时钟独立持久化。OS重启或单调钟不连续安全失败，不承诺跨重启拼接为连续24h。
设备不可用从首次确认后计时，统计为采样近似；API/RSS/磁盘估计不保证预测所有宿主机故障。强杀清理可能依赖原有限租约 TTL，但不会盲目重放。
未启动正式24h、未完成 Stage D/E 的正式数据审计、未开发 Phase1.3。按任务8.1明确要求，现在停在Stage C等待用户批准。
