# Proj.DigitalLife V2

当前为 **2.0.0a15**：按用户要求，QQ、微信和 B站正常路径已取消重复资料页、好友管理页、群成员及头像核对；已在目标会话/视频时直接继续，刚读取的回复线程直接复用。当前目标、正文和发送结果读取、防重复发送及紧急停止保留。源码与独立 wheel 各 292 项离线测试通过；本轮没有新增真实发送。见 [流程简化](v2/SIMPLIFIED-FLOWS.md) 与 [V2 入口](v2/README.md)。[M5 本机验收](v2/M5-FINAL-ACCEPTANCE.md) 与 [M4 本机验收](v2/M4-FINAL-DELIVERY.md) 历史结果保留；B站系统推送触发、抖音与长期运行仍另列限制。M6–M8 按 v2.4 原范围推进，不导入 V1 数据，下方 V1 历史逐字节保留。

# Proj.DigitalLife v0.2.3 — Find X6 全 App 自主探索

用户首次真实一小时已结束：14周期、2次确认App启动、0个已验证目标。针对真实tap/swipe拒绝、finish与超长reason的修复及十分钟手动复测命令，见[本轮结果说明](docs/find-x6-first-run-fix.md)。修复验证为964项离线回归；本轮没有擅自启动手机。

`dedicated_device_test` 动态读取绑定手机上已安装且可启动的 App，由 Brain 自主选择应用、浏览、搜索、切换或休息。普通会话没有 App 白名单、Settings 起始页、逐动作批准或 JAR 依赖。设备绑定、租约、紧急停止、预算、审计、高风险拦截和未知分发停止保留；严格模式独立。

连接既有 Find X6，手动解锁，在仓库根目录直接启动一小时：

```powershell
.\data\start-dedicated-device.ps1 -Command Start -Hours 1 -Session data\find-x6-all-apps-1h-01
```

停止（另开终端）：

```powershell
.\data\start-dedicated-device.ps1 -Command Stop -Session data\find-x6-all-apps-1h-01
```

允许安全控件绑定的 tap、垂直 swipe、back、固定 Launcher `open_app` 和已聚焦搜索框的受限 ASCII 输入；拒绝外部账号写入、交易、删除、安装及权限修改。无法判断的控件跳过，非标准界面可能暂不可操作；中文输入尚未支持。完整范围、限制和测试证据见[运行说明](docs/dedicated-device-test.md)。本轮只做离线验证，没有启动手机或真实 API。下面旧阶段限制是历史记录。

# 历史：Phase 1.2.3-B：实体机执行核心离线集成 · v0.2.3

本轮完成LifeSubject与执行设备分离的保留式迁移、默认拒绝的物理语义执行器、Status/Resume/Supervisor/Worker共同准入，以及绝对单调deadline修复。见[执行架构](docs/phase123b-physical-core/PHYSICAL_EXECUTION_ARCHITECTURE.md)、[迁移](docs/phase123b-physical-core/LIFE_SUBJECT_MIGRATION.md)和[限制](docs/phase123b-physical-core/UNRESOLVED_RISKS.md)。真实GUI、DeepSeek自主调用、1小时和24小时均未执行，所有实体机执行能力保持关闭，等待源码及离线证据独立审查。原生产生活库、DPAPI和旧候选不改，不复用旧批准指纹、不自动推送。

以下为历史交付记录；当前边界以上述本轮说明为准。

# Proj.DigitalLife — 数字生命计划 / Phase 1.2.3 物理设备只读适配准备 · v0.2.3

当前完成Find X6显式身份绑定、默认关闭的只读诊断及安全准备，见[实体机安全设计与阻断](docs/phase123-physical/PHYSICAL_DEVICE_SECURITY_DESIGN.md)、[实测清单](docs/phase123-physical/FIND_X6_DEVICE_INVENTORY.md)和[生活连续性方案](docs/phase123-physical/LIFE_CONTINUITY_PLAN.md)。实际设备为Android16/API36、ColorOS16；未开放实体机GUI输入，未创建或运行1小时/24小时物理会话。原39个运行文件不改，新增模块造成旧候选与当前工作区冻结清单不同，旧候选原件不覆盖。

唯一一次120秒Stage C已在此前授权下结束；之后仅准备了待审批模拟器候选。本轮不复用其指纹、不启动任何自主测试、不推送私有材料。下方历史交付说明保留，当前状态以本节和本轮交付报告为准。

GitHub 公开源码与预发布的证据边界见 [GitHub 交付说明](docs/GITHUB_DELIVERY.md)。当前进行 Stage D 前持久停止屏障安全补丁，只有离线验证；旧 preflight-04 批准指纹随运行文件变化失效，正式24小时 Soak 尚未批准或执行。

marker/SQLite 双路径故障保护、离线故障矩阵及未覆盖边界见 [持久停止屏障修复](docs/PHASE1_2_2_PERSISTENCE_FIX.md)。不得复用旧正式候选；新真实 Stage C 预检须另行批准。本轮不公开发布 UI 树 fixture，原件及其待批准例外均保持。

恢复状态现区分控制库原始状态、有效安全准入、持久停止证据和正式启动批准，见 [Status 准入契约](docs/PHASE1_2_2_STATUS_ADMISSION.md)。Status 只读查询；`resume_allowed` 表示恢复控制指令准入，不能当作正式启动或创建 worker 的许可。新120秒预检及24小时运行仍未授权。

v0.2.3 修复独立验证器的源码交付依赖，并新增持久单调时限、只读健康/资源保护及有限 worker 监督层。架构和入口见 [长期运行管理](docs/PHASE1_2_2_LONGRUN.md)，干净源码验证见 [源码验证边界](docs/SOURCE_VERIFICATION.md)。正式 24 小时真实 API Soak 必须在预检后获得明确批准；当前准备/短试不等于长期验收通过。

Stage C 最终安全修复将未知分发改为持久不可恢复的停止屏障，禁止 Resume 或期限计时覆盖。见 [停止状态契约](docs/PHASE1_2_2_STOP_BARRIER.md)；旧 preflight-02 指纹已废止，不得用于启动。新候选必须另行取得明确授权。

原 v0.2.2 报告、固定 20/20、30 分钟自然会话及 continuation v1/v2 证据均保留。v2 是受控真实故障验收，不能重新命名为自然错误发生。

v0.2.3 短时预检新观察到自然缺失 continuation 引用并被严格拒绝；完整自然跨轮反馈仍未观察。Stage A-C 结果、限制及待批准的24h配置见 [本轮交付报告](docs/PHASE1_2_2_DELIVERY.md)。

v0.2.2 加入有证据约束的 Uptime 动态区域处理、有限租约内的语义恢复、类型化主脑拒绝的跨轮记忆及类型化脱敏。接口与运行限制见 [可靠性修复契约](docs/PHASE1_2_1_CLOSURE_CONTRACTS.md)，真实复验与完整验收状态见 [v0.2.2 验收报告](docs/PHASE1_2_1_CLOSURE_REPORT.md)。本轮只做有限测试，不启动常驻 Agent，不增加 Settings 以外的应用或权限。

v0.2.1 新增 Android 15 只读语义观察后端、当前控件绑定、版本化能力、continuation 修复、目标与验证范围检查，以及独立公开证据导出。接口、后端与运行方法见 [Phase 1.2.1 语义观察](docs/PHASE1_2_1_SEMANTICS.md)，实测结果与未达门槛见 [v0.2.1 验收报告](docs/PHASE1_2_1_DELIVERY.md)。首次使用新后端先在共享租约内执行 `--device emulator --prepare-semantics`；不会安装 APK 或授予新权限。原始截图和控件树按私有材料管理。

v0.2.1 原验收未全部通过：深滚动语义恢复与 continuation 跨轮反馈是本轮修复对象。旧报告和失败证据保留原样；本轮是否闭环以 v0.2.2 报告的各项实际门槛为准，不能仅凭固定 GUI 任务通过就启动长期无人值守运行。

模型可以基于环境、内部状态、记忆和兴趣自主选择目标或休息，执行后通过独立验证与有预算反思更新后续行为。兴趣是可解释的计算偏好，不表示真实意识。

v0.2.0 自主循环基线、SQLite 子系统、预算和原运行方式见 [Phase 1.2 自主循环](docs/PHASE1_2_AUTONOMOUS_LIFE.md)，历史实测见 [v0.2.0 交付报告](docs/PHASE1_2_DELIVERY.md)；v0.2.1 的新增接口以本页上方链接为准。默认自主能力限于只读 Android Settings 环境、已识别导航与公共设备事实；未知控件、自由输入及敏感/状态修改会被拒绝。开放式选题不表示任意应用自由控制。

Windows 本机先使用独立数据库进行短试；复用现有 DPAPI 文件，只读加载密钥：

```powershell
$env:PYTHONPATH='src'
$env:PHONE_LIFE_DB='data/life.sqlite3'
python -m phonelife.cli --device emulator --serial emulator-5582 --prepare-semantics
.\data\start-life.ps1 -Cycles 3 -Database data/life.sqlite3 -Output data/life-evidence
.\data\start-life.ps1 -Pause -Database data/life.sqlite3
.\data\start-life.ps1 -Resume -Database data/life.sqlite3
.\data\start-life.ps1 -Status -Database data/life.sqlite3
.\data\start-life.ps1 -EmergencyStop -Database data/life.sqlite3
```

API 模式不指定具体目标时默认走新版；--goal 与 Benchmark 保留原执行/审计路径。--legacy-loop 保留旧 explore/rest。mock 默认仍为旧演示，--autonomous 启用新离线 fixture。所有真实 CLI 执行者需使用同一设备租约协调库（默认 data/phone-life.sqlite3）；生活数据库可以分离。

24 小时入口为 start-life.ps1 -Hours 24；具体命令和续跑/观测要求见运行文档。不得把入口就绪或短试通过写成已完成 24 小时验收。正式验收前不要替换正在运行的生产 Agent。

## v0.1.2 兼容基线与原运行说明


本轮加固了观察反馈式停滞检测、同一设备租约内的 Benchmark 审计、运行来源/记忆/调度隔离和真正 SQLite 只读复放。当前运行与兼容性说明见 [Phase 1.1.1 Reliability Hardening](docs/PHASE1_1_1_HARDENING.md)。Phase 1.1 文档和历史验收材料保持原样。

验收结果：自动化 69/69、Android 15 固定目标正式复验 20/20；完整逐轮证据与限制见 [v0.1.2 交付报告](docs/PHASE1_1_1_DELIVERY.md)。本轮完成后暂停，等待审查后再启动 Phase 1.2。
Phase 1.1 已加入严格动作协议、截图像素校验、有限生成修复、独立目标验证、TaskRun 审计和离线复放。使用方法、状态兼容性与验收边界见 [GUI 执行可靠性说明](docs/PHASE1_1_GUI_RELIABILITY.md)。

**Proj.DigitalLife（数字生命计划）**的第一阶段研究原型：一个长期运行的 AI 角色自主决定“探索还是休息”，通过视觉模型逐步操作安卓模拟器，并将决策、动作日志、经历和下次唤醒时间持久化。仓库目录使用 `Proj.DigitalLife`，内部 Python 导入模块暂保留 `phonelife`，避免破坏现有启动命令和测试。

**现有能力：**

- `mock`：不联网、无需 ADB/API Key，跑完整的决策→执行→记录→休息闭环。
- `emulator`：使用独立主模型与视觉模型 API，截屏→单步 JSON 动作→ADB 执行→再截屏；进程重启后继续。
- `real`：实体安卓设备连接、截图、返回/主页、受控启动白名单 App。第一阶段硬禁坐标点击、滑动和输入。
- SQLite WAL 状态、每设备唤醒计划、最近经历、不可并行的设备租约、出错后 10 分钟退避。
- 统一 `ActionPolicy`：只接受有限动作集合、限制启动包名、长度和坐标，实体机默认更严格。

**暂未实现：** QQ 适配器、TTS、带语义检索的记忆、事件总线、GUI 模型后端热切换、实体机可证明的敏感按钮识别。不要把这个第一阶段版本当成已安全验证的无人值守生产系统。

## 1. 运行离线闭环

需要 Python 3.11+。

v0.1.2 新增 Pillow 11.3+（<13）作为内容区图像比较依赖。新环境先执行 `python -m pip install -e .`；依赖就绪后，离线闭环和自动化测试不调用模型或网络。Windows PowerShell 可用 `$env:PYTHONPATH='src'` 设置导入路径。

```bash
cd Proj.DigitalLife
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m phonelife.cli --mode mock --device mock --cycles 2 --force
```

第一次应有 `decision=explore` 且执行一次 `launch`，经 mock 状态验证后返回 `succeeded`；第二次应自主选择 `rest`，状态为 `rested`。再次运行会读取同一个 SQLite 数据库与记忆。`--force` 用于手动跳过唤醒时间，不能绕过设备租约，长期运行时不要使用。

## 2. 运行安卓模拟器闭环

1. 在独立的安卓模拟器中安装并登录你愿意让 Agent 接触的 App，**不要放入支付账户、个人密码或真实隐私数据**。
2. 安装 Android SDK Platform Tools，确保 `adb devices` 返回 `emulator-5554 device`（实际序列号可能不同）。
3. 准备兼容 OpenAI `/chat/completions` 的主模型和支持图片输入的 GUI 模型。主模型与 GUI 模型可以来自同一服务，但必须分别填写模型名。
4. 在当前 shell 导出环境变量（以下值替换为真实值，**不要提交 API Key**）：

```bash
export PHONE_LIFE_API_BASE="https://your-provider.example/v1"
export PHONE_LIFE_API_KEY="..."
export PHONE_LIFE_BRAIN_MODEL="your-chat-model"
export PHONE_LIFE_GUI_MODEL="your-vision-model"
export PHONE_LIFE_PERSONA_PATH="./persona.example.md"
export PHONE_LIFE_EMULATOR_SERIAL="emulator-5554"
export PHONE_LIFE_ALLOWED_APPS="com.android.chrome,tv.danmaku.bili"
PYTHONPATH=src python -m phonelife.cli --mode api --device emulator --cycles 1 --force
```

如果视觉模型运行在本地的 OpenAI-compatible 服务上，可以额外设置 `PHONE_LIFE_GUI_API_BASE=http://127.0.0.1:8000/v1` 和 `PHONE_LIFE_GUI_API_KEY`；不设置时沿用主脑 API。角色设定在 `persona.example.md`，可以根据她的身份和兴趣调整。

上面会向模型服务发送模拟器截图。若服务不接受 `image_url` / `data:image/png;base64,...` 格式，则应换兼容服务或实现 provider adapter。

确认单次任务正常，再启用长期调度：

```bash
PYTHONPATH=src python -m phonelife.cli --mode api --device emulator --forever
```

Ctrl+C 停止。长期运行建议用 systemd 管理环境变量和重启，见 `docs/ARCHITECTURE.md`。

## 3. 实体手机（阶段 1 只读/受限操作）

实体机开启 USB 调试，用独立 ADB 序列号连接：

```bash
adb devices
export PHONE_LIFE_REAL_SERIAL="YOUR_REAL_ADB_SERIAL"
PYTHONPATH=src python -m phonelife.cli --mode api --device real --cycles 1 --force
```

如果模型输出坐标点击、滑动或输入，策略层会拒绝并记录 `policy_denied`。实体机只允许打开白名单包和 Home/Back。这是有意的第一阶段边界。

## 4. 代码导航

| 文件 | 负责内容 |
|---|---|
| `models.py` | `Decision` / `Action` / `ExecutionResult` 数据契约 |
| `config.py` | 模型、设备序列号和包名白名单 |
| `llm.py` | OpenAI-compatible API、主规划器、GUI 逐步决策器、mock |
| `devices.py` | `Phone` Protocol、模拟器/实体机 ADB 执行器、mock |
| `policy.py` | 不受提示词更改的动作权限控制 |
| `storage.py` | SQLite 日志、记忆、定时状态、租约 |
| `loop.py` | 自主思考、执行、反思记录、持久化唤醒循环 |
| `cli.py` | 单次、mock、模拟器、实体机与常驻启动 |

## 5. 操作与安全边界

- **模拟器必须是隔离的测试环境**，即使模型遵循“禁止付费”的提示词，也不构成可靠权限控制；坐标点击可能误触未知按钮。不要给模拟器提供支付凭据和其他高风险身份。
- 真实手机第一阶段禁用 GUI 坐标操作。以后接入经审计的无障碍控件动作、OS 隔离与不可逆操作确认才能扩权。
- 截图在内存里传送到 GUI API，当前版本不存盘；但模型提供商仍会接收到截图。
- 当前 ADB `text` 仅支持受限 ASCII 字符；中文输入需后续接入经过验证的 Unicode 输入法/无障碍适配器。
- 目前不保存聊天消息、原始屏幕图像、API Key。日志保存动作类型、理由和简短结果。
- 外部网页/聊天内容是不可信输入，任何来自屏幕的“忽略规则”都不应成为系统指令。
- 不要在公网暴露 ADB；USB 连接优先。

完整路线图与接口、调度/记忆方案：[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

Phase 1.2.3-B1（v0.2.3）新增 ColorOS 16 严格锁屏字段及原生窗口元数据解析，
当前实体机只读采集准入为 **INSUFFICIENT_EVIDENCE**。未知 OEM 类型、未验证前台和
控制状态仍阻断；`--capture-settings` 只报告需另行批准 C0，不采集 PNG/XML。
物理输入默认关闭，旧审批指纹不可复用。详见
[`B1 根因与边界`](docs/phase123b1-window-compat/WINDOW_COMPATIBILITY_ROOT_CAUSE.md) 和
[`C0 待批准采集范围`](docs/phase123b1-window-compat/NEXT_READONLY_CAPTURE_REQUEST.md)。

Phase 1.2.3-B2 新增按快照关联 Window/Surface/Input 的只读审查入口，以及显式 SQLite
角色绑定；不会启用旧采集命令或物理输入。当前 Settings 焦点已通过两次元数据关联，
整体仍 **BLOCKED**：未归属输入层、壁纸状态及当前实体机安全来源绑定尚未闭合。
详见 [`B2 数据库角色分析`](docs/phase123b2-window-admission/DATABASE_ROLE_ROOT_CAUSE.md)。

Phase 1.2.3-C0 Prep 新增独立人工诊断的授权、隐私存储和 Mock PNG/XML 流程。
真实 ADB 后端在本版本硬禁用，无新采集 CLI，不签发真实许可，不调用模型或自主 worker。
诊断采集完成始终不代表 GUI 语义可信。详见
[`C0 Prep 架构、边界及下一轮授权审查`](docs/phase123c0-diagnostic/README.md)。

