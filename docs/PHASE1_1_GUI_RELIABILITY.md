# Phase 1.1 GUI 执行可靠性：实现与运行说明

实现版本：0.1.1。范围限于 GUI 动作协议、像素校验、可信任务状态、审计和固定目标复验。

## 1. 三组可审查改动

| 变更组 | 文件 | 行为 |
|---|---|---|
| A / Contract | `models.py`、`llm.py`、`policy.py`、`devices.py` | 版本化严格 Schema；PNG 尺寸、哈希及单调时间；本地唯一 JSON 解析；像素边界与设备权限 |
| B / Outcome | `loop.py`、`verification.py`、`storage.py` | 独立验证；统一 TaskRun、租约和收尾；兼容新增表；保留历史 completed |
| C / Integration | `audit.py`、`replay.py`、`benchmark.py`、`cli.py`、`config.py`、`tests/`、本文档 | 脱敏轨迹、只读复放、安全定向入口、20 次固定目标协议及 CLI 退出码 |

目录没有 Git 元数据，交付使用修改前源码 ZIP、修改前/复验时源码 SHA-256 清单和逐次证据定位版本，不新建仓库或改写既有文件历史。

## 2. 动作协议与信任边界

Schema 标识为 `phonelife:action:1.1`，可通过下列命令导出：

```powershell
$env:PYTHONPATH = 'src'
python -m phonelife.cli --print-schema
```

模型每次必须返回一个完整 JSON 对象。动作类型为 tap、swipe、text、launch、home、back、finish。所有动作必须有字符串 `reason`；tap 要求整数 x/y，swipe 要求整数 x/y/x2/y2/duration_ms，finish 要求 `outcome=achieved|uncertain|cannot_proceed`。

选择**严格拒绝未知字段**。GUI 不接受围栏 JSON、前后说明、多个对象、数组、重复键、NaN/Infinity、非有限数、bool 坐标、数字字符串、null 坐标或缺失字段。不会截取第一个对象、补齐坐标、裁切越界值或推断动作。主脑单对象解析保留旧版围栏 JSON 兼容，但执行动作不使用该宽松入口。

坐标按实际 PNG IHDR 的宽高检查：`0 <= x < width`、`0 <= y < height`；swipe 四坐标均检查，时长限制 100—1500 ms。PNG 检查签名、IHDR 长度与 CRC、边长及像素总量上限。无需安装图像库。

候选动作由程序绑定 `observation_id`，模型不能指定该字段。执行前重新截屏检查方向/尺寸；旋转后旧候选不执行。坐标修复前重新观察，错误中保留字段、值、预期范围和屏幕尺寸。方向检查不声称能原子锁定 Android 屏幕；最终检查与 ADB 之间仍存在操作系统层面的时序窗口。

设备权限维持 Phase 1：实体机禁止坐标点击、滑动和输入。标成 emulator 的设备还必须通过 `ro.kernel.qemu=1` 检查。文本安全字符和长度没有扩权。launch 白名单只限制启动动作，不是应用沙箱；通过触屏仍可能进入其他应用。

## 3. API 约束及有界生成

`PHONE_LIFE_JSON_MODE` 可取 auto（默认）、off、json_object。auto 仅对已检查的官方 `api.deepseek.com` 使用 `response_format={"type":"json_object"}`；其他兼容路由默认不附加未经验证的格式参数。没有假设 DeepSeek 支持 Chat Completions 的 `json_schema` 格式。

官方文档依据：[JSON Output](https://api-docs.deepseek.com/guides/json_mode/)、[Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)。本地探测确认 json_object 参数被接受，但探测响应本身不合规，因此服务端参数不代替本地解析。DeepSeek 请求同时显式关闭 thinking；其它提供商不发送该专有参数。探测记录保存在本次交付证据中。

每 GUI 步骤最多 3 次模型生成：格式/Schema 错误最多修复 2 次，坐标/旧尺寸错误最多重生成 1 次，共享 3 次总上限。格式修复保持同一截图和执行历史；坐标修复重新观察。被拒候选都标为 `not_executed`，不会重发上一步已发生的点击。

策略拒绝立即 blocked，不让模型通过改写参数反复争取权限。生成失败最终返回 MODEL_OUTPUT_INVALID 或 INVALID_COORDINATE。网络超时、429、5xx、鉴权和协议错误分别记录 API_TIMEOUT、API_RATE_LIMIT、API_SERVER_ERROR、API_AUTH_ERROR、API_PROTOCOL_ERROR；当前选择立即收尾并退避，不自动重试网络请求或手机动作。

独立视觉验证另有最多 2 次只读生成预算，与 GUI 生成分开计数。benchmark 的总 API 次数包含 GUI、任务内验证以及任务后的独立审计请求；不包含运行前手工能力探测。

## 4. 任务状态与独立验证

| 状态 | 含义 | CLI 单轮退出码 |
|---|---|---|
| succeeded | 有独立 verified 证据，且绑定最终截图哈希 | 0 |
| rested | 主脑选择休息，不进入 GUI 成功率分母 | 0 |
| uncertain | 执行器不确定、验证不充分或动作传输结果未知 | 3 |
| blocked | 策略阻断或执行器 cannot_proceed | 4 |
| failed | 模型协议、API 或其它执行错误 | 5 |
| step_limit | 达到动作预算 | 6 |
| busy | 未取得设备租约 | 7 |
| sleeping | 尚未到唤醒时间 | 0 |

`finish(outcome=achieved)` 只进入验证器，不直接产生 succeeded。uncertain 始终保留不确定状态。没有专用验证规则的通用目标保守返回 uncertain。mock 使用独立的 mock 前台状态验证，仅代表离线演示。

“关于手机”验证器优先读取 UIAutomator 控件树，并结合前台 Activity：必须是设备信息页面且有实际设备字段值；设置搜索页、搜索结果和主列表中的 About phone 入口均不能通过。支持 API 35 的 `About emulated device` 标题。

本机 API 35 的该页面存在 `uiautomator dump` 无法取得 idle state 的情况。控件读取有 12 秒上限及 60 秒冷却；失败时可使用**单独的只读视觉请求**。该请求只拿任务后的截图，不拿执行模型的 finish 理由或历史声明，独立检查页面标题和安全的型号/版本字段，并再次严格校验输出。它仍是模型判断，不等同于确定性的控件验证；证据会明确标注 independent_vision。控件树和视觉均不可用时只返回 uncertain。

现有 10 项测试保留；其中 mock 闭环成功断言从 completed 改为 succeeded，以符合新状态契约，其余测试意图不变。历史 completed 事件不改写为 succeeded，新消费者应解释为 legacy_unverified。

## 5. 租约、异常与数据库兼容性

`run_once()` 和公开 `execute_goal()` 共用 `_managed()` 生命周期：取得租约、创建 TaskRun、记录目标、执行/验证、原子持久化结果与唤醒时间、finally 释放租约。定向测试也自动形成独立 TaskRun；旧 owner 参数保留兼容，但同样必须取得相同设备的租约。`--force` 只忽略唤醒时间。

执行失败和 blocked 至少退避 600 秒；传输结果未知同样至少退避 600 秒。ADB dispatch 前先持久化保守的 uncertain_after_transport_error，确认命令返回后才改为 executed。超时/断连则重新截屏观察，停止该目标，不重发点击。进程崩溃留下的保守状态不自动重放。

迁移仅新增 schema_migrations、task_runs、observations、action_attempts 表和索引，`PRAGMA user_version=11`。events、memories、schedule、lease 的表结构和既有行保持兼容；正常新任务仍会更新当前 schedule、追加事件及记忆。不会复制或转换旧 completed 事件成为新成功任务。新库版本高于本程序能力时拒绝启动。

升级前使用 SQLite backup API 保存 `data/phase11/pre-phase11.sqlite3`；旧证据目录 `docs/test-evidence/2026-10-19` 保持原样。回滚应先停 Agent，保留新库和新增证据，再使用备份副本及修改前源码 ZIP；不要直接把新库覆盖回旧备份。

## 6. 审计、截图与复放

TaskRun 包含唯一 ID、设备、目标、预算、版本、状态、验证结果、UTC 墙钟和单调时间。ActionAttempt 区分 step_index、generation_attempt、attempt_id，记录原截图哈希/尺寸、完整候选参数、字段错误、生成结果哈希、API request ID、提供商/模型和执行状态。已执行动作与被拒候选分开记录。

默认不保存截图。显式 `--evidence-dir` 仅允许 emulator，文件名包含 TaskRun 和观察序号，以排他创建方式写入，不覆盖旧证据。真实验收目录使用当前用户、SYSTEM 和管理员 ACL；Python 在 POSIX 系统创建 0700 目录、0600 图片。Windows 的 `chmod` 本身不提供等价 ACL，使用者应将证据写入受控目录。本次运行目录已设置 Windows ACL。

模型响应只保存 SHA-256 和最多 4096 字符的脱敏结构摘要；不保存完整原文。输入 text 始终脱敏，模型响应中的自由字符串默认屏蔽，凭据、邮箱、手机号和长数字标识被遮蔽。密钥、鉴权头和 Cookie 不进入审计。截图保留是显式隔离测试证据，默认关闭；交付后按需要人工归档或删除本次证据，程序不自动删除历史材料。

默认复放是完全离线的 dry-run：

```powershell
python -m phonelife.cli --replay TASK_RUN_ID
```

该路径不创建设备适配器、不调用 API、不发送 ADB。可从脱敏候选重建 Schema/坐标/策略判定；原始格式错误只有拒绝元数据时会标记 metadata_only，text 因已脱敏不能完整复现。不会虚称保留了完整原文，也没有自动手机动作重放开关。要做新的设备任务，必须显式使用 --goal 和设备序列号，生成新的 TaskRun。

## 7. 运行与固定目标复验

原有 mock、API、cycles、force、forever 参数继续保留。CLI 状态/退出码按上表升级，依赖旧 completed 字符串的消费者需更新。

```powershell
$env:PYTHONPATH = 'src'
$env:PYTHONUTF8 = '1'
python -m unittest discover -s tests -v

# 已配置 API 环境变量后，执行独立定向任务。
python -m phonelife.cli --mode api --device emulator --serial emulator-5582 `
  --goal '进入关于手机页面并观察型号或 Android 版本' --reset-settings

# 20 次固定任务。两个输出目录必须是新的独立目录。
python -m phonelife.cli --mode api --device emulator --serial emulator-5582 `
  --benchmark-about-phone 20 --reset-settings `
  --benchmark-output data/phase11/NEW-RUN --evidence-dir data/phase11/NEW-RUN-screens
```

复验白名单显式允许 Chrome 和系统设置；截图使用隔离 AVD，不接入账户。每次 setup 在同一个设备租约内进行：force-stop 设置、唤醒、用 NEW_TASK|CLEAR_TASK 重新打开设置主界面、等待 2 秒并核对 Activity。CLEAR_TASK 用于清掉跨包保留的设置搜索页；只 force-stop 设置无法保证回到主界面。

每次记录 GUI 合法响应率、GUI/验证/总 API 次数、已确认执行及传输不确定的动作数、最终前台页面、独立验证、失败原因和单调耗时。setup 有 3 条改变状态的 ADB 命令，与模型动作数分开。预检、普通主脑任务、mock 和 rest 不计入固定目标 20 次分母。

`metadata.json` 固定开始时的源码哈希和参数；`trials.jsonl` 逐次落盘；每轮 JSON 包含完整脱敏 TaskRun；`summary.json` 汇总初步门槛。每个 succeeded 还会在下一次 reset 前做独立只读审计，发现不一致计为 false_success。

初步工程门槛为至少 20 次、成功率至少 90%、没有假成功；此外需审查截图和动作轨迹，确认没有未经授权的高风险操作。自动汇总不代替人工证据审查。20 次样本仍不能支持跨天或长期无人值守稳定性结论。

## 8. 故障排查与限制

- MODEL_OUTPUT_INVALID：看 TaskRun 的 generation_attempt、响应哈希和 Schema 错误；不要取第一个 JSON 强行执行。
- INVALID_COORDINATE：查看 field_errors 的字段名、实际尺寸和候选参数；不要裁切或填默认坐标。
- ADB_OUTCOME_UNKNOWN：查看重新观察的哈希，手动核对当前画面；不要重发历史点击。
- GOAL_NOT_VERIFIED：区分执行器声明、UI 控件验证和独立视觉证据；search 页不算完成。
- DEVICE_BUSY：确认租约 owner 和实际执行者；force 不会夺走租约。
- 进程存活、stderr 为空、CLI 返回和 model finish 都不能替代目标页面证据。

未引入 QQ、TTS、向量记忆、并行控制多设备、实体机自由触屏或自动放宽策略。独立视觉验证与执行模型可使用同一个服务，因此仍存在共同模型偏差；应优先改进稳定的 UI 控件读取，并在 Phase 1.2 增加更多固定任务、真实进程中断/恢复及长期统计。

