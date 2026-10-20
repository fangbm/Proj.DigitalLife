# Proj.DigitalLife v0.2.0 / Phase 1.2 验收报告

日期：2026-10-19（Asia/Shanghai）。本报告在真实短时验证收尾后冻结；24 小时无人值守测试不在本次已完成结果内。

## 1. 交付范围

实现了可运行的模型自主闭环：真实观察 → 来源过滤记忆 → 状态与预算 → 模型候选及选题/休息 → 原 GUI 执行器和额外安全能力检查 → 类型化独立验证 → 一次有预算反思 → 原子更新记忆、兴趣、状态和下次唤醒。

本轮没有预编排真实日常任务，也没有向真实主脑指定“打开关于手机”等具体 GUI 目标。真实初始环境恰好保留在上一阶段的 About emulated device 页；角色配置只描述自主探索、安全和环境能力。模型自行提出了滚动查看、设备事实基线、视觉分区观察等目标。默认环境能力限于只读系统设置/公共事实；尚不是任意 App 的通用自主浏览器。

模型的主观评估与权威执行/验证分开存储。兴趣、好奇心、能量等是有界计算状态，不声称真实意识或主观体验。`exploration_observed`、`partially_met` 和 `uncertain` 没有并入 succeeded。

架构、契约、全部表结构和运行方式见 [Phase 1.2 运行文档](PHASE1_2_AUTONOMOUS_LIFE.md)，原始开发要求保留为 [开发规格](specs/PHASE1_2_LIFE_SPEC.md)。

## 2. 修改文件与接口

| 工作包 | 文件 | 实现 |
|---|---|---|
| M1 决策与状态 | life_models.py、life_state.py、brain.py | 严格 LifeContext/GoalCandidate/LifeDecision/Reflection；模型选题、候选 ID 绑定、有界状态演化；离线 fixture |
| M2 记忆与反思 | memory.py、life_storage.py、autonomy.py | 分层记忆、相关检索、去重、摘要、过时标记、来源与关联、动态主题、一次反思与实际后续作用 |
| M3 执行与验证 | autonomy.py、autonomy_policy.py、verification_registry.py、loop.py、llm.py、devices.py | 原执行器/租约内整合；只读能力 gate；注册式规则、只读事实命令；保留旧 GUI 规范 |
| M4 预算与恢复 | budgets.py、recovery.py、soak.py、cli.py | 请求预约与窗口配额、中断分类、启动前审阅、新观察、持久控制、短试/常驻/有时限 Soak 与只读观测 |
| 持久化收尾 | storage.py | finish_task 可选同步 finalizer；与状态/记忆/目标/唤醒在同一事务提交；核心 schema 保持 12 |
| 版本与配置 | __init__.py、pyproject.toml、.env.example | 0.2.0、共享设备租约协调配置；沿用 Pillow，无新增外部服务/数据库 |
| 运维与角色 | data/start-life.ps1、persona.life.example.md | 只读复用现有 DPAPI，独立生活库，共享旧设备租约；不给具体 GUI 目标的角色设置 |
| 测试 | tests/test_autonomy.py | 新增因果、来源、状态、反思、验证、安全、预算、进程退出、控制、重启和迁移回归 |
| 文档和证据 | README、ARCHITECTURE、当前文档、本报告、phase12 证据目录 | 更新当前入口，旧 Phase 1.1/1.1.1 报告及证据保留 |

当前目录无 Git 元数据，不能创建真实 Git 提交。交付按工作包生成可审阅补丁，并提供冻结源码 SHA 和源码/证据压缩包；不创建空提交或初始化无关仓库。补丁存在接口依赖，应整体应用后测试。

## 3. 自动化验收与故障注入

最终 **123/123 PASS**，耗时 18.142 秒：原 69 项全部保留且源文件字节不变，新增 54 项。修改前原 69 项基线也通过。见 [最终测试日志](test-evidence/phase12-2026-10-19/final-tests.log)、[基线日志](test-evidence/phase12-2026-10-19/baseline-tests.log)、[逐项测试映射](test-evidence/phase12-2026-10-19/automated-results.json)。环境为 Windows / Python 3.13.5 / Pillow 11.3.0 / SQLite 3.45.3；不把该结果冒称为 Linux 或多天验收。

主要验证并非只检查 JSON：

- 第一次事实经历 → 反思产生动态 hardware 兴趣 → 持久权重改变 → 下一轮 mock 主脑实际选了不同目标；输入含源经历和相关事实。
- 不同能量/实际休息时间/失败/重复/新颖内容导致不同状态与休息；模型 rest/reflect 均可以零 GUI。
- 反思说成功不能提升开放观察；反思 API 超时不覆盖已验证结果；新主脑 API 超时记账并至少退避 600 秒；无效主脑输出只有两次请求。
- 来源与设备过滤排除 benchmark/manual/mock/旧 NULL；显式旧 autonomous 可只读桥接；去重保留多个来源，过时信息标记而不删除。
- 当前目标、工作上下文、情景、兴趣、下一次唤醒和数值状态跨数据库重开保留。收尾事务故障回滚后，旧 running 可审阅，租约释放。
- 真实两个 OS 子进程竞争最后一个请求额度，只能一个成功；独立任务库共用设备协调库时无法交叉控制。
- 真实子进程 os._exit 后保留 unknown dispatch；过期租约恢复时不重发。覆盖四种中断分类、未来唤醒前的启动审阅、动作中取消、暂停与急停。
- 敏感/未知控件、外来覆盖、状态控件、自由输入、未授权 App、非列表滑动拒绝；只读 fact/前台/控件/视觉/组合各自按证据判定。
- 原严格 JSON、坐标、修复、停滞、ADB 不确定性、实体机/QEMU、安全复放、租约异常路径测试均保留。

开发过程中发现并修正了分层记忆 INSERT 列数、无效 UI XML 的保守处理、默认 lease_store 在程序化重开时绑定旧连接，以及启动审阅不能等到未来唤醒才进行的问题。中间失败日志一并保留，最终结果不掩盖开发中的错误。

## 4. 真实自主行为与短时 Soak

原 Android 15 / API 35 模拟器 emulator-5582，deepseek-flash 真实 API。正式有时限 Soak 实际运行 **900.001 秒**，保存 **6 个自主循环**；状态分布为 `{"succeeded": 1, "exploration_observed": 1, "blocked": 1, "rested": 3}`，模型请求按阶段为 `{"brain": 6, "gui": 2, "reflection": 2, "verification": 1}`。另保留正式前 1 轮被拒预试及一次等待退避时人工停止的 97 秒空闲会话，不计入正式循环数。所有过程均保留；没有为了数量而跳过 600 秒退避。

确认执行的模型 GUI 输入动作总计 `{}`（空对象表示 0）。这些目标包含真实只读测量和视觉 API 观察，不能把 0 个 GUI 输入说成已验证任意触屏自主能力。未观察到假成功或未经授权高风险动作；结论限于该样本。截图 SHA、完整候选/拒绝、实际请求、动机、反思、兴趣变化与每轮单调耗时见 [真实汇总](test-evidence/phase12-2026-10-19/real-results.json)、[选择与演化](test-evidence/phase12-2026-10-19/model-choices-and-evolution.json)、[请求账本](test-evidence/phase12-2026-10-19/api-request-ledger.json)、[截图索引](test-evidence/phase12-2026-10-19/observation-index.json)。正式会话源码指纹与交付 runtime 完全一致。

真实行为的关键因果链已经出现：

1. 模型从当前 About 页提出继续滚动；程序因 UI 层级不可用拒绝，ADB 输入动作 0，保留 POLICY_DENIED。
2. 反思提出不重试触屏、使用只读事实能力；下一轮明确引用该经历，选择测量 android_version/model/battery_percent/storage_free_kb。独立固定命令得到了真实值，目标 verified。
3. 硬件/只读测量相关兴趣上升后，模型判断重读同一基线价值低，自主改选视觉观察页面分区；独立视觉结果只记 exploration_observed。
4. 反思区分“收到观察”与“目标已验证”，并提出停止重复询问已见内容；其主题权重按重复/无新信息向下调整，没有因为模型自评成功就算 succeeded。

例如 `emulator device identity fields` 权重从 0.50 → 0.52（被拒任务仍有初次画面观察）→ 0.57（新的已验证事实）。`read-only fact collection as alternative to gui navigation` 从 0.50 → 0.52 → 0.57，随后没有新内容的视觉询问降到 0.55。每步都有来源 TaskRun、前后值、客观验证和 novelty 原因。它们是算法偏好更新，不能当作模型真实情绪。

另一次模型使用 continue 但 continuation_of=null，被拒绝并退避；这暴露了开放主脑的契约稳定性限制，没有放宽引用校验。该次完整选择可在 life_model_choice 事件中审阅，即使目标入口尚未接受、TaskRun.goal 为空，也不丢失模型原始候选与理由。

| 来源 / TaskRun | 自主决策与目标 | 结果 / 原因 | API | 单调秒 |
|---|---|---|---:|---:|
| live-pilot<br>`ad3c5894-bd68-4174-a71b-0ad8e1117f5b` | explore: Scroll down the About emulated device page to see what other device detail sections exist (build number, Android version, etc.) | blocked / POLICY_DENIED | 3 | 18.118 |
| live-final<br>`b10ecbc9-1578-44a6-8b4b-f90ec2c1473b` | continue: Collect the supported read-only facts (android_version, model, battery_percent, storage_free_kb) as a baseline record | succeeded / GOAL_VERIFIED | 3 | 7.826 |
| live-final<br>`8114f56f-f673-4175-972f-b455316969ac` | explore: Ask a single read-only visual question about which section headers are visible on the current About page, to test whether visual verification works where touch was denied. | exploration_observed / GOAL_NOT_VERIFIED | 4 | 8.050 |
| live-final<br>`2d563562-3460-4c66-80cc-671eee2747ef` | continue: Verify the currently visible About emulated device page via the dedicated about_phone rule, confirming the foreground Settings page matches the observation. | blocked / POLICY_DENIED | 1 | 2.499 |
| live-final<br>`b12ffd00-c37b-4eb5-a78a-5e503581739f` | rest: 目标在接纳前被拒，见原始选择事件 | rested / REST_DECISION | 1 | 2.273 |
| live-final<br>`20fb4c54-25db-474e-8677-6f8c9072d252` | rest: 目标在接纳前被拒，见原始选择事件 | rested / REST_DECISION | 1 | 1.738 |
| live-final<br>`f213a141-bb88-4ca7-b1f3-1a23a3c1b416` | rest: 目标在接纳前被拒，见原始选择事件 | rested / REST_DECISION | 1 | 2.149 |

## 5. SQLite、来源与历史完整性

修改前建立 `data/phase12/pre-020.sqlite3` 一致性备份、源码/文档归档和旧证据 SHA 清单。基线核心 schema 12，quick_check=ok，设备 lease=0；45 个 TaskRun、460 个 events、37 个 memories、511 个 observations、176 个 action_attempts、2 个 schedule。

真实 Phase 1.2 使用由备份复制的独立 `data/phase12/life-acceptance.sqlite3`，原 `data/phone-life.sqlite3` 只作为共享设备租约协调库。未在原历史库中安装 Life 子系统或写入新的自主经历；不替换生产 Agent。新库保留原记录，仅该副本的 emulator 自主 schedule 正常推进。

Life 子系统迁移版本 1，核心 user_version 仍为 12；增加 11 个 life_* 表及索引，不删除或重分类旧行。只读 replay/status 不安装新表。以独立版本记录可选子系统，避免让旧版核心迁移把新数据重新分类。

复验后原库全部原行/原列值保持一致，独立新库保留历史记录；**440 份旧证据 SHA 全部一致**。原三个测试文件和旧报告/规格字节不变，DPAPI 密文文件 SHA 未变。两库 quick_check=ok，共享设备残留租约 0。新库的 readonly --status 和 --replay 前后 DB/WAL 字节、逻辑 dump SHA、schema 均不变，见 [完整性报告](test-evidence/phase12-2026-10-19/preservation-check.json)、[只读实际检查](test-evidence/phase12-2026-10-19/readonly-cli-check.json)。旧 NULL 来源和 completed 状态不被重新分类或升级。

## 6. 安全、预算和运行说明

原 ActionPolicy、进展检测、About 验证、脱敏、Replay 及原 69 项测试文件与基线字节一致。GUI 执行端新增独立能力 gate；主脑不能通过修改 risk_class、反思或页面提示解除它。Medium 没有默认写能力；High 保持禁止；实体机仍硬禁坐标和文本。

默认每目标 15 动作、32 次总模型请求、300 秒；滚动/点击停滞阈值不增加。每小时 120、滚动 24 小时 500 个请求，失败/取消也计入；能量和主题得分不能调高这些预算。用请求数管理成本暴露，不声称已经核算货币费用。真实证据中的请求数分主脑、GUI、独立验证和反思。

[运行文档](PHASE1_2_AUTONOMOUS_LIFE.md#6-运行与观测) 提供短试、--forever、24 小时 Soak、Pause/Resume/EmergencyStop/Status、重启和新 session 证据保存命令。Resume 不清预算、不跳过退避、不直接启动进程。所有控制同一设备的执行者必须共享租约协调路径；它不是拦截任意外部 adb 的操作系统锁。

## 7. 明确限制和下一阶段建议

- 本轮只完成短时真实运行和离线故障注入，**24 小时以上真实无人值守未执行**。提供入口和统计工具，不把入口就绪当作长期验收。
- UIAutomator idle 问题仍会使自主触屏被保守拒绝。本轮没有为提高动作数量放宽检查。环境能力窄、模型可能耗尽新主题，允许休息；未声称已能通用浏览第三方 App。
- 模型可能提出非法 continuation、重复或与能力不符的目标，程序会拒绝和退避；这仍影响自主探索效率。
- 单独的视觉模型观察可能有误；与主脑使用同服务时有共同偏差。视觉观察不是任意业务成功证明。
- succeeded 证明的是所记录的类型化验证契约；自然语言描述与该契约的匹配仍需审阅，不能把前台状态或一个事实测量泛化成复杂业务目标完成。
- 强杀和急停不能撤销已发动作或已经开始的 HTTP 请求；未知动作保留需要审阅，禁止盲重发。运行日志需要人工定期审阅。
- SQLite 和可选截图随时间增长；本轮没有删除历史或添加自动清理策略。跨平台、长期磁盘管理和更丰富应用适配需要后续验证。
- 本机 Windows/Python 3.13.5/Pillow 11.3.0 实跑；未声称 Linux/Python 3.11 实机验收通过。

建议下一阶段先完成 24 小时隔离 Soak、UI 语义采集可靠性与逐应用只读能力适配，再考虑更多兴趣活动。继续决策应优先改善错误反馈与目标引用契约，保留当前严格校验。向量检索和外部消息能力不应先于来源、安全与验证边界验收。

完成本阶段交付后暂停开发，等待用户审查；不启动下一阶段，也不留下常驻自主测试进程。
