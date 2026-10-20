# v0.1.2 / Phase 1.1.1 Reliability Hardening

本阶段只加固可靠性基础；不实现 Phase 1.2 兴趣、人格演化或长期自主规划。

## 1. 工作包及数据流

| 工作包 | 根因 | 当前实现 | 主要文件 |
|---|---|---|---|
| P0 观察反馈 | 相同动作参数被计为重复，不考虑界面正在滚动 | 稳定应用内容区、多帧差异和空间分布评估；连续两次无可靠进展才阻断第三次相同候选 | progress.py、loop.py |
| P1 设备会话 | execute_goal 返回即释放租约，benchmark 随后重新访问设备 | 将 post_audit 回调纳入统一包装层；setup、执行、最终观察、审计、结果落盘后才释放 | loop.py、benchmark.py、models.py |
| P1 运行来源 | 所有任务共享生活记忆与 schedule | 显式 run_kind；新 benchmark/manual_targeted 不写生活记忆，不改自主 schedule | storage.py、loop.py、benchmark.py、cli.py |
| P2 只读复放 | 普通 Store 初始化执行 WAL 设置、建表和迁移 | Store.open_readonly：文件 URI mode=ro、query_only；不 mkdir、不迁移、不创建空库 | storage.py、cli.py、replay.py |

动作协议、模型格式修复、坐标策略、设备适配器和目标验证器的安全规则沿用 0.1.1，没有放宽。

## 2. ActionProgressDetector 与 StagnationGuard

`ProgressAssessment` 记录 verdict、原因、稳定/变化像素比例、变化 tile 数、前后原图与内容区哈希、比较区域和检测器版本。检测器版本为 stable-body-tiles/2。

每个已确认执行的动作使用 4 个观察样本：模型输入及执行前复查为前两帧，动作间隔后及再等待 0.2 秒为后两帧（mock 不等待）。先排除上方 8%、下方 8%、左右各 5%，屏蔽系统时钟、通知/电量图标、导航区及边缘指示器。再用 Pillow 解码、转灰度并缩小为 72×96 网格。

前两帧或后两帧中变化超过 6 灰度级的点不作为稳定进展证据。前后稳定点差至少 12 灰度级，且变化覆盖至少 1.2% 网格、4 个有效 tile、4 个纵向 tile 区和 3 个横向 tile 区，才记 progressed；稳定点少于 65% 为 uncertain，其余为 stagnant。空间分布约束避免把局部 spinner、慢闪烁控件等当作滚动进展。

该方法比较的是应用内容区的可靠视觉反馈，不只比较整图 SHA-256，也不让模型理由决定进展。它不是通用语义识别：大面积、慢变化动画仍可能难以与实际内容变换区分；小范围但有意义的控件变化也可能被保守判为无进展。step_limit、独立目标验证和原权限边界始终生效。

StagnationGuard 保留原先“两次无效 dispatch 后停止第三次”的预算，没有提高重复阈值。每次真实 progressed 清零无效计数；相同 swipe 在继续滚动、相同 tap 在切换页面时可以继续。相同动作且内容不变、或没有可靠进展证据时不能无限执行。换动作仍受单目标 15 步预算约束。

被阻断候选使用 validation_status=stagnation_blocked、execution_status=not_executed，TaskRun reason_code=STAGNATION。每次已执行动作都有 action_progress 事件和对应 attempt.progress。ADB 次数以 executed 记录为准，不把拒绝候选计为手机动作。

新增运行依赖 Pillow>=11.3,<13；它替代手写 PNG 解码，使实际 PNG 过滤和颜色类型由成熟图像库处理。截图尺寸边界仍由原 ScreenObservation 验证，不使用模型报告尺寸。

## 3. 同一受保护设备会话

公开入口仍为 `run_once()` 和 `execute_goal()`；后者新增关键字 `run_kind='manual_targeted'`、`post_audit=None`。

```python
async def post_audit(result, final_observation):
    # 只读验证；调用时租约仍属于该 TaskRun。
    return {"verification": verification_dict, "foreground": foreground}

result = await life.execute_goal(goal, run_kind="benchmark", post_audit=post_audit)
```

统一包装层负责：租约 → TaskRun → setup → 执行 → 主验证 → 新最终观察 → post_audit → 原子结果收尾 → 释放。Benchmark 返回后只读 TaskRun，不再截屏、调用模型或查询前台设备状态。

审计结果保存在 result.post_audit、TaskRun.result 和事件中，绑定 task_run_id、observation_id、observation_sha256、主结果截图哈希、primary_status 与 lease_protected。审计截图哈希不匹配时任务失败；主验证成功而事后审计不确认时，最终结果降为 uncertain，并保留原声明以计算 false_success，不能通过降级隐藏分歧。

默认租约有效期 120 秒，每最多 30 秒续期；整个设备会话含 setup/生成/执行/审计有 600 秒单调时限。不是无限期锁。过期 owner 不能续期复活，release 只删除匹配 owner 的租约。租约丢失会取消当前会话，记录 LEASE_LOST 并停止控制设备。

异常、API 故障、SESSION_TIMEOUT、取消均经过统一收尾和 finally 释放。OS 强杀无法执行 Python finally；持久租约会在有限有效期后允许新执行者取得，未知手机动作仍不盲目重发。此轮测试验证了强制异常退出后的租约恢复；未增加自动恢复或重放被中断的 TaskRun，遗留 running 任务需审阅，属于明确限制。

## 4. 来源、生活记忆和调度

| run_kind | 正常入口 | 生活记忆 | 唤醒位置 |
|---|---|---|---|
| autonomous | API 自主 run_once/forever | 写入 autonomous 生活记忆，主脑仅检索该来源 | 原 schedule |
| manual_targeted | execute_goal / CLI --goal 默认 | 不写普通生活记忆；完整 TaskRun/事件可查询 | run_schedules 对应命名空间 |
| benchmark | 固定任务 benchmark | 不写普通生活记忆；完整审计保留 | run_schedules benchmark |
| mock_test | CLI --mode mock | 仅 mock_test 记忆，供离线闭环选择休息 | run_schedules mock_test |

PhoneLife 构造器默认 autonomous，以保留现有程序化自主执行接口；CLI mock 显式设置 mock_test。定向执行的默认来源是 manual_targeted。

`recent_memories(device)` 默认只返回 autonomous；`recent_memories(device, run_kind=None)` 可显式读取旧的未分类记忆。`count_events` 通过 event_sources 过滤，避免测试事件数影响自主规划。事件和 TaskRun 都仍完整保存，不丢弃测试历史。

旧行不猜测来源：新增 run_kind 列默认为 NULL，原始内容/时间/状态不改写。旧的未分类记忆不会自动进入新的自主检索，但仍可查询；这属于检索策略变更，而非删除或重新分类。历史测试污染不通过擅自清理旧数据解决。

`next_wake`、`set_wake` 支持关键字 run_kind（默认 autonomous）。失败至少 600 秒退避保持不变，但 benchmark/manual 的失败不会覆盖自主唤醒。显式定向测试仍可跳过自身等待，不能抢占设备租约。

## 5. SQLite 迁移与只读复放

schema 版本从 user_version=11 升为 12。新增 task_runs.run_kind、memories.run_kind/task_run_id、event_sources、run_schedules 和来源索引；保留旧表、旧列、旧行和值。迁移在写事务内检查列是否存在，重复打开不重写来源或迁移时间。

`Store.open_readonly(path)` 不调用普通构造器。目标不存在直接 FileNotFoundError，不创建父目录或空库；使用 SQLite 文件 URI mode=ro 和连接级 query_only，保持已提交 WAL 内容可读，不使用会忽略 WAL 的 immutable 模式。不修改 journal mode、schema、事件、记忆或调度。

只读 WAL 连接仍可能参与 SQLite 的 SHM 读锁协调；这不是应用数据修改。回归测试实际比较了主库字节、WAL 字节、schema version 和逻辑内容不变，而不是只看 ADB=0。

```powershell
$env:PYTHONPATH = 'src'
$env:PHONE_LIFE_DB = '.\data\phone-life.sqlite3'
python -m phonelife.cli --replay TASK_RUN_ID
```

复放不实例化模型或设备，不发网络或 ADB。旧库没有 TaskRun 表时给出明确错误，不自动迁移。脱敏 text 和未保存原文的格式错误继续标记不可完整重放。

## 6. 运行、测试与真实复验

```powershell
# 新环境先准备依赖；运行测试时无需联网。
python -m pip install -e .
$env:PYTHONPATH = 'src'
$env:PYTHONUTF8 = '1'
python -m unittest discover -s tests -v

# 按 README 配置 API 环境变量后，在空闲的原模拟器上复验。
python -m phonelife.cli --mode api --device emulator --serial emulator-5582 `
  --benchmark-about-phone 20 --reset-settings `
  --benchmark-output data/phase111/NEW-RUN --evidence-dir data/phase111/NEW-RUN-screens
```

仍需显式隔离 AVD、实际 QEMU 身份、独立序列号及新输出目录；不清空数据库、不重建 AVD、不覆盖旧证据。原 data/start-api.ps1 继续使用未修改的 DPAPI 文件，原 CLI 参数和常驻方式保留。

每轮 TaskRun 含主验证与同租约事后审计、最终图像哈希、API 次数、实际动作、进展评估、失败码和单调耗时。此版 elapsed_ns 包含事后审计，不能和旧版只计主任务的耗时直接比较。

原 41 项测试保留；两项定向错误退避断言改查 manual_targeted 命名空间，以验证退避仍持久化且不污染自主 schedule。新增测试覆盖视觉变化、动态干扰、实际事件/dispatch 数、审计 IPC 竞争、续期、异常/取消/时限、来源隔离、重启、迁移、进程异常退出和 SQLite 文件级只读。

## 7. 限制与 Phase 1.2 接口建议

- 内容区进展是保守视觉指标，不代表业务成功。大面积动画、小变更及特殊布局可能需要 UI 语义适配；没有增加自动放宽权限的路径。
- About phone 控件树的 idle 问题仍存在，目标验收可使用独立视觉备用请求；同服务模型有共同偏差，需要继续人工核对图像。
- 测试仅覆盖本阶段固定目标和离线故障路径，不等于跨天无人值守验收。
- 强杀后租约可过期恢复，但未自动重放手机动作或自动处理所有遗留 running TaskRun。
- 新依赖需事先安装；本机在 Python 3.13.5/Pillow 11.3.0 测试，Linux/Python 3.11 的兼容设计未替代该平台实跑。

待用户审查后，Phase 1.2 可围绕只接受 autonomous 来源的 `LifeEpisodeReader`、只读 `RunContext(run_kind, task_run_id)`、外部调度的 `NextWake` 以及独立的 GoalVerifier 注册表扩展。建议先补跨平台运行与中断 TaskRun 审阅工具，再讨论兴趣与长期规划。本轮不实现这些接口或系统。
