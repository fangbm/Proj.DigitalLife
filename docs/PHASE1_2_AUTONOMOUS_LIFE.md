# v0.2.0 / Phase 1.2 Autonomous Life Loop

本轮在 v0.1.2 上增加模型选题、可解释状态、分层记忆、反思、类型化目标验证、持久预算和中断审阅。模型选择目标；程序提供能力并执行安全、预算和来源限制。兴趣和内部状态是计算模型，不表示真实意识或主观体验。

## 1. 执行路径与兼容性

```text
Startup review (lease, classify orphan runs, fresh observe, never replay)
Wake → same device lease → TaskRun
  → observe → retrieve autonomous memories → evaluate state/budget
  → model candidates / choose explore, continue, switch_goal, reflect or rest
  → validate contract, continuation, risk, resources, repeated-goal cooldown
  → existing GUI Executor + ActionPolicy + additional autonomous capability gate
  → typed independent verification → one bounded reflection
  → atomic TaskRun + memory + state + interests + goal + next wake commit
  → release lease → persistent sleep
```

API 模式未指定 --goal/--benchmark 时默认走新循环。人工定向与 Benchmark 沿用原执行组件及同租约审计；不更新 life state 或兴趣。`--legacy-loop` 保留原 explore/rest 接口，`--mode mock` 默认仍兼容旧演示；加 `--autonomous` 才启用新版离线 fixture。离线 fixture 不作为真实模型自主性的证据。

原严格单 JSON、重复键/Schema、坐标校验、修复预算、停滞保护、独立验证、实体机限制、QEMU 核验、只读 replay、DPAPI 和 run_kind 均保留。动作 Schema 仍为 1.1；基础 TaskRun 审计格式仍兼容 1.1.1，新增 life_contract_version=1.2 和 life_cycles 承载自主契约。

## 2. M1：模型与状态契约

| 接口 | 数据与职责 |
|---|---|
| LifeContext | UTC 时间、单调运行时长、设备/前台/真实 PNG 元数据、最近情景/相关记忆、兴趣、失败、未完成目标、内部状态、请求/动作预算、安全能力 |
| GoalCandidate | 局部 goal_id、开放描述、动机、expected_value、novelty、estimated_cost、risk_class、continuation_of、created_at、verification |
| LifeDecision | explore / continue / switch_goal / reflect / rest，至多 5 候选，唯一选中或 null，理由、唤醒间隔 |
| Reflection | attempted、subjective_outcome、discoveries、topics、worth_continuing、next_adjustment |
| LifeState | curiosity、energy、fatigue、boredom、confidence、consecutive_failures、cycles、last_update |

分数必须是有限的 0..1，费用估计是 0..100 的动作数；契约拒绝额外字段和类型转换。主脑格式最多生成两次，反思最多一次，不通过多轮无界修复消耗预算。模型候选 ID 只是一次提案内的标识；程序用 TaskRun UUID 和局部 ID 生成稳定的全局 UUID，并保存原始选择和绑定映射，避免跨轮同名候选引发数据库冲突。

GoalCandidate 的 novelty/expected_value 是模型判断，不直接写数值状态。状态更新依赖确认动作数、持久 API 请求数、实际休息时间、内容区哈希/验证证据的新颖性、重复目标和客观结果。单轮每个浮点状态变化最多 0.12；长期限定在 0..1。能量消耗按动作和请求计，恢复按实际流逝时间计，承诺休息并不会立即恢复能量。confidence 的成功只接受独立 verified；失败降低 confidence，重复提高 boredom，新内容影响 curiosity。

每次状态变化保存 before/after/causes。低能量、连续失败冷却、预算耗尽可强制休息，但程序不为模型安排具体日常目标。其余状态和兴趣进入 LifeContext，参与模型选题。同验证契约短时间重复两次后冷却，不能靠改写理由无限请求同一个已知事实。

## 3. M2：分层记忆、兴趣和反思

- Working：当前目标、真实屏幕、最近确认动作和下一步意图。保留中断现场，不作为可重发命令。
- Episodic：目标、理由、客观执行/验证、模型评价、兴趣变化、内容哈希和来源。每轮有界摘要，而非复制全部动作日志。
- Semantic：只将独立验证的证据或实际错误码提炼为环境事实/执行限制；反思中的“发现”仍是模型主张。
- Preference：动态主题的权重变化、前后值、客观结果和源 TaskRun。每轮每主题最多变化 0.05，全局最多 100 个动态主题，每轮最多更新 5 个，模型不能直接改权重。

检索使用 SQLite 字段、英文词/中文二元词匹配、重要性和时效性排序，扫描最近 200 条分层记录，最多返回 20 条；可桥接最近 20 条具有明确相同 run_kind 的旧普通经历，不推断其成功。旧 NULL 来源不猜测、不删除、不重新分类。benchmark/manual_targeted 不可向 LifeMemory 写入；mock_test 独立；自主主脑只读 autonomous。

插入时按层和规范化内容 digest 去重，保留多个来源；life_memory_links 连接情景、语义与偏好。整理每轮最多检查 50 条，过时 semantic/working 仅标 stale，不删除历史。真实动作审计仍保存在原 TaskRun/observations/action_attempts/events 中。需要完整复现时查审计，不以摘要代替原记录。

反思在主执行和验证后、设备租约内进行，输入实际动作轨迹、客观结果及相关记忆。输出是经过严格校验的主观建议，不能改验证状态或硬策略。反思 API 故障记录 unavailable 和错误码，不抹去已经获得的独立验证。主题和下一轮上下文能受反思影响；反思文字不会成为系统指令或可信环境事实。

## 4. M3：GoalVerifierRegistry 与安全能力

| 规则 | 可证明内容 / 限制 |
|---|---|
| foreground | 实际前台包是否符合声明的启动契约；不证明阅读了某个页面 |
| page | 前台包与可信 UI 标题资源同时匹配；控件树不可用即 uncertain |
| controls | 前台包与真实可见控件集合，重观测绑定稳定内容区 |
| readonly_fact | 固定只读命令测量 android_version、model、battery_percent、storage_free_kb；模型不能提供任意 shell 参数 |
| about_phone | 保留原页面/设备字段验证及独立视觉备用规则 |
| visual | 独立只读模型请求描述实际可见内容；最多 exploration_observed，不能证明任意目标成功 |
| observe | 确认收到真实截图，记录 exploration_observed，不映射 succeeded |
| all | 2–3 个叶规则全部 verified 才成功；部分证据记录 goal_partially_met / partially_met |

单个执行器 finish 不能产生 succeeded。主观“我成功了”不会覆盖独立结果。未知规则、缺失证据、API 故障和哈希关联错误均不推断成功。注册表可由程序扩展，模型不能注册或替换内置验证器。

自主风险分类与权限独立于模型自评：

| 风险 | v0.2.0 默认能力 |
|---|---|
| Low | 观察、测量白名单公共事实、Home/Back、启动允许的系统设置、通过真实层级识别的只读设置页导航/纵向列表滚动 |
| Medium | 可能修改状态，当前没有自动写入能力；保守拒绝并审计 |
| High | 支付/购买/转账、对外消息/公开发布、删除、账号安全、授权、安装等硬阻断，不提供对应工具 |

自主自由输入关闭。tap 必须指向支持的系统设置导航行，拒绝 Switch/SeekBar/CheckBox/EditText、敏感标签、未知控件、外来覆盖层及系统栏。swipe 需识别真实可滚动列表并限制为应用内容区纵向操作；无法获取层级、前台不符或内容在识别期间变化时拒绝触屏。`Build number` 等可能通过连续点击修改状态的条目不在可点击导航能力内。

默认环境只开放 Android Settings 的只读主题。可选目标不来自固定任务列表，但环境能力目前较窄；这并非任意应用自主浏览能力，也不是 OS 应用沙箱或对恶意系统镜像的安全证明。未来添加应用需先增加经过审阅的能力适配，不能只增加启动白名单。隔离环境、无个人账号和系统镜像可信是运行前提。实体机保留原坐标/输入硬限制。

## 5. M4：SQLite、预算、恢复与控制

沿用基础 user_version=12，启用新循环才在事务中安装独立 `life_schema_migrations(version=1)`。不为了增加可选子系统而重写已有核心表。新增表为：life_states、life_controls、life_cycles、life_goals、life_memories、life_memory_sources、life_memory_links、life_interests、life_requests、life_recovery，以及 life_schema_migrations。重复打开不改旧行或迁移时间。只读 replay/status 不运行任何迁移。

状态、情景、偏好、目标状态、TaskRun 结果和唤醒在同一个 finish_task 事务中提交；失败会回滚，留下可审阅的 running 任务。模型请求、候选、动作及恢复记录提前落盘，不能因收尾失败丢失执行历史。

### 预算

| CLI 配置 | 默认 | 语义 |
|---|---:|---|
| --step-limit | 15 | 单目标动作上限，仍受原有单步修复/停滞保护 |
| --hourly-api-limit | 120 | 过去 3600 秒实际请求预约数上限 |
| --daily-api-limit | 500 | 过去 86400 秒实际请求预约数上限 |
| --max-goal-requests | 32 | 单目标包括主脑、GUI、验证、反思的全部请求 |
| --max-failures | 3 | 连续失败达到阈值后冷却；之后可试探恢复 |
| --min-rest-seconds | 30 | 模型决定的休息/唤醒不得短于此值 |
| --task-timeout | 300 | 单调时限，含模型、动作、验证与反思，硬上限 600 秒 |

请求预算独立于模型的 expected_value/estimated_cost 和内部 energy；每次网络调用前通过 SQLite 写事务预约，两个进程不能重复花费最后一个额度。失败、取消和结果未知的请求也计入，避免重试绕过预算。小时/每日额度耗尽后安排到窗口实际腾出额度的时间；API 故障仍至少 600 秒退避。此版采用请求数限制成本暴露，不提供基于实时服务价格的金额核算或“已花费美元”统计。

基础设备 TTL=120 秒、最长每 30 秒续期，完整会话有时限。真实 CLI 默认共用 `data/phone-life.sqlite3` 作为租约协调库；可用 PHONE_LIFE_LEASE_DB / --lease-db 指定同一个绝对路径。独立验收数据可以放在另一库中，但所有控制同一设备的执行者必须用同一协调库和设备键。start-life.ps1 与原 start-api.ps1/Benchmark 的默认协调库相同。程序化调用通过 PhoneLife(lease_store=...) 共享，默认保持使用其原 Store 的兼容行为。不能把租约误称为跨任意数据库、不同配置乃至任意外部 ADB 程序的全局 OS 锁。

### 中断审阅

进程入口先检查 running 记录；取得同一设备租约后，即使下次唤醒仍在未来，也先建立 startup_review TaskRun，分类历史并重新观察，保留原唤醒安排。被另一执行者占用或人工暂停时延期，不抢占。常规新任务也在租约内检查遗留任务。

分类为 not_dispatched、action_outcome_unknown、executed_not_verified、verification_finished_not_finalized。未知动作/已经执行或验证未收尾的旧任务标 needs_review，未发送动作的标 interrupted。原动作和结果保留，不自动重发；原 reserved API 请求标 interrupted_outcome_unknown，额度不退回。新模型只能基于新观察提出新动作。进行中的取消也将不确定 dispatch 标 needs_review。

人工控制持久化为 running / paused / stopped。新循环在活动会话中每 0.25 秒检查（还受操作系统调度及正在执行的有界本地调用影响）；暂停/急停取消后续步骤、取消 ADB 子进程并释放租约。已经发出的动作仍可能发生，必须保留 unknown，不能声称急停撤回手机动作。已发出的 HTTP 工作线程也可能在后台完成，预算仍保守计数。

## 6. 运行与观测

首次先准备 Python 3.11+ 和 Pillow>=11.3,<13。不要同时启动未使用同一协调库的外部自动化。

```powershell
$env:PYTHONPATH='src'
$env:PYTHONUTF8='1'
python -m unittest discover -s tests -v

# 新版离线 fixture；mock_test 命名空间，不是真实 LLM 验收。
$env:PHONE_LIFE_DB='data/offline-life.sqlite3'
python -m phonelife.cli --mode mock --device mock --autonomous --cycles 1
```

本机现有 DPAPI 和 AVD 可复用下列脚本。默认独立生活库 data/life.sqlite3，绝不自动替换历史验收数据库；密钥文件只读加载，不修改、不复制到报告。

```powershell
# 前台短试，不给 GUI 目标。Output 每次创建新的 session-UUID 子目录。
.\data\start-life.ps1 -Cycles 3 -Database data/life.sqlite3 -Output data/life-evidence

# 24 小时隔离 Soak 入口；本轮交付不冒称此命令已完成 24 小时。
.\data\start-life.ps1 -Hours 24 -Database data/life.sqlite3 -Output data/life-evidence

# 另一终端中，Database 必须和运行进程一致。
.\data\start-life.ps1 -Pause -Database data/life.sqlite3
.\data\start-life.ps1 -Resume -Database data/life.sqlite3
.\data\start-life.ps1 -Status -Database data/life.sqlite3
.\data\start-life.ps1 -EmergencyStop -Database data/life.sqlite3

# 进程异常退出后，先审阅 Status 与历史，再用同一库重新运行。
# Resume 只改控制标志，不启动进程、不清除预算或退避。
.\data\start-life.ps1 -Resume -Database data/life.sqlite3
.\data\start-life.ps1 -Hours 24 -Database data/life.sqlite3 -Output data/life-evidence
```

直接 CLI 也支持 --forever / --soak-hours 24。--force / -ForceFirstWake 只跳过首次唤醒等待，不跳过预算、风险、低能量冷却或设备租约；正常 Soak 不使用它。需要后台管理时，由受控的服务管理器启动上述前台命令并保留 stdout/stderr，不在本轮自动安装或替换生产服务。

session 目录保存源码 SHA、请求预算配置、startup-review、完整 cycles.jsonl（TaskRun、life_context/decision/reflection/transition/summary、来源事件）、可选原始 PNG 与真实 elapsed_monotonic_seconds。--status 使用 SQLite 只读连接，不创建模型或设备，返回状态/决策分布、按阶段请求数、待审任务、下一次唤醒、共享设备租约、内部状态及兴趣。任务状态为 exploration_observed 或 uncertain 时应按原样分析，不并入成功率。

24 小时验收建议：检查实际时长与进程连续性、请求窗口上限、重复目标比例、休息/恢复、记忆来源、需要审阅的动作、租约交叉控制及所有验证分歧。先核对短试和故障注入证据，再决定是否进入长期无人值守。
