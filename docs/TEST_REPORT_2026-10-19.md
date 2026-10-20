# Proj.DigitalLife 测试报告

报告日期：2026-10-19（Asia/Shanghai）  
项目版本：0.1.0 / Phase 1  
测试对象：本地 Python Agent、SQLite 状态存储、Android 模拟器、DeepSeek 主脑与视觉调用  
证据快照时间：2026-10-19 12:40:51 +08:00

## 1. 验收结论

**基础运行链路通过，GUI 任务验收未通过。**

10 项现有单元测试全部通过；离线模式已验证探索、休息、状态恢复和自动唤醒。独立 Android 模拟器、真实 DeepSeek API、截图输入、ADB 动作执行和事件落盘均已接通。

“打开关于手机并查看设备信息”没有完成。首轮在执行 9 个动作后因滑动坐标校验失败被拦截；定向复测执行 3 次点击后，模型在动作 JSON 后追加说明及第二个 JSON 对象，触发严格解析错误。复测结束停留在系统设置搜索页，未到达关于手机页面。

报告整理时，模拟器和 API 常驻进程均存活，SQLite 检查正常。**这些结果不构成 GUI 长期稳定运行或无人值守任务可靠性的验收。**

## 2. 环境与配置

| 项目 | 实际环境 |
|---|---|
| 项目目录 | `D:\codex\Proj.DigitalLife` |
| 操作系统 | Windows 11，内部版本 10.0.28120 |
| Python | 3.13.5，`C:\ProgramData\miniconda3\python.exe` |
| 第三方 Python 运行依赖 | 项目声明为空；使用标准库 |
| Android Emulator | 35.2.10.0，build 12414864 |
| ADB | 1.0.41，Platform Tools 36.0.0-13206524 |
| AVD | `digitallife-api35`，独立新建的测试设备 |
| AVD 目录 | `D:\android-avd\digitallife\digitallife-api35.avd` |
| Android 系统 | Android 15 / API 35，Google APIs，x86_64 |
| 虚拟硬件 | 4 核、4096 MB 内存、720 × 1600 屏幕；数据分区配置为 8G |
| 虚拟化 | WHPX 检查返回可用，启动日志确认硬件加速运行 |
| ADB 序列号 | `emulator-5582`，状态 `device` |
| API 地址 | `https://api.deepseek.com`，Chat Completions 接口 |
| 主脑 / 视觉模型 | 均为 `deepseek-flash`；均发生了真实 API 调用 |
| 启动包白名单 | `com.android.chrome` |
| 数据库 | `data/phone-life.sqlite3`，WAL 模式 |
| 普通执行预算 | 每目标最多 15 步，动作间隔 1 秒 |
| 定向复测预算 | 最多 6 步，动作间隔 2 秒 |

密钥使用 Windows DPAPI 加密保存；报告及证据附件不包含密钥或密钥文件。此目录没有 `.git`，无法给出 Git 提交号；本报告记录的是当前本地源码和实际运行结果。

白名单约束的是 `launch` 动作。首轮模型通过 Home、滑动和点击进入系统设置；不能据此宣称白名单把交互范围限制在 Chrome 内。

## 3. 范围与方法

本次覆盖现有单元测试、mock 命令行闭环、持久化与调度、模拟器启动、真实文本和截图 API 调用、ADB 执行、普通自主任务及定向 GUI 复测。

未覆盖实体手机、连续多日运行、API 限流/断网/余额不足、崩溃恢复、真实多进程竞争压测、浏览网页内容正确性、支付或账号操作、QQ、TTS 和高级记忆功能。CLI 能执行、进程存活或模型返回 `completed`，均不单独作为业务目标完成的证据。

普通复测使用完整 `run_once` 路径，包括真实主脑规划。定向复测由测试脚本指定“关于手机”目标，使用真实截图、真实视觉模型、原有动作策略和 ADB，并持有设备租约；该部分直接调用 `execute_goal`，不经过主脑重新选题及 `run_once` 外层异常处理。

## 4. 测试结果

| 编号 | 用例 | 实测结果 | 判定 |
|---|---|---|---|
| UT-01 | 数据契约与 JSON 基础解析，3 项 | 唤醒区间、未知动作拒绝、围栏 JSON 解析均通过 | 通过 |
| UT-02 | 动作策略，5 项 | 实体机点击限制、模拟器合法点击、包白名单、敏感/非法文本拒绝均通过 | 通过 |
| UT-03 | 持久化闭环及租约，2 项 | mock 探索/休息/恢复及第二 owner 被阻挡均通过 | 通过 |
| IT-01 | mock 连续运行 | 首轮 `explore` 执行 1 次模拟 launch，下一轮 `rest` | 通过，仅离线链路 |
| IT-02 | 重启与唤醒 | 新 CLI 进程读取原数据库并返回 `sleeping`；mock 常驻进程到期后自动运行并新增记忆 | 通过，仅 mock 实测 |
| IT-03 | Android 启动与截图 | `sys.boot_completed=1`，ADB 在线，截图确认 Android 主屏实际显示 | 通过 |
| IT-04 | DeepSeek 文本 API | 模型列表返回 `deepseek-flash` / `deepseek-v4-pro`；最小 JSON 请求返回 `{"ok":true}` | 通过 |
| IT-05 | 首轮 Chrome 真实链路 | 主脑生成目标；视觉模型驱动 1 次 launch、2 次 tap；随后因屏幕状态不确定结束 | 链路通过，首页验收未通过 |
| IT-06 | 自主打开关于手机 | 已执行 9 个动作并进入设置；下一条 swipe 被坐标策略拦截，返回 `blocked` | 失败 |
| RT-01 | 普通强制复测 | 主脑改选“确认当前屏幕状态”；视觉模型确认设置页后 finish，`completed`、0 个动作 | 观察用例通过，未验证动作可靠性 |
| RT-02 | 定向复测关于手机 | 执行 3 次 tap；第 4 条模型响应格式错误，解析失败，输入动作未执行 | 失败 |
| OP-01 | 报告整理时运行状态 | 模拟器、API Python 进程和启动包装进程存活；API stderr 快照为空 | 通过，仅状态快照 |
| DB-01 | 数据完整性及租约释放 | `PRAGMA quick_check=ok`，`journal_mode=wal`，快照中没有遗留租约 | 通过，仅当前快照 |

现有 10 项单元测试在报告整理时再次执行，结果为 `Ran 10 tests in 0.163s` / `OK`。该耗时仅指单元测试，不能解释为真实模型或 GUI 操作延迟。[完整输出](test-evidence/2026-10-19/unit-tests.log)

## 5. 真实执行证据

### 5.1 首轮 Chrome：事件 18—22

主脑选择“在模拟器上启动 Chrome，观察默认启动页面是否正常显示”。事件 19—21 记录了 3 个实际 ADB 动作。最后的执行摘要为：

> Chrome 当前展示为纯白空白页（仅状态栏与导航栏可见），无法确认默认启动页是否正常加载；屏幕状态不确定，按规则停止，不进行猜测性操作。

尽管事件 22 的状态是 `completed`，上述摘要没有确认首页正常。随后检查曾看到 Chrome 广告隐私介绍页面，不能把本轮计为浏览器首页验收成功。

### 5.2 关于手机首轮：事件 25—36

事件 26—34 记录了 9 个已执行动作，包括 tap、swipe 和 home；最终进入系统设置。事件 35 记录：

```json
{"action":"swipe","why":"Missing or invalid screen coordinates"}
```

事件 36 返回 `blocked` / `steps=9`。现有事件没有保存被拒动作的原始坐标，因此只能确认坐标缺失或无效，不能进一步确定是哪个字段缺失、越界或模型使用了其他字段。

### 5.3 普通复测：事件 37—38

使用 `data/start-api.ps1 -Once -Force` 运行。主脑选择观察当前设置画面，视觉模型直接 finish，最终 `completed` / `steps=0`。这证明 API 与截图观察路径仍可使用，不能作为“关于手机”任务通过或坐标问题已修复的依据。

### 5.4 定向复测：事件 39—43

目标是“在当前 Android 系统设置中找到并打开 About phone 页面，观察设备信息后结束”，最多执行 6 步。

| 步骤 | 模型输出/执行情况 |
|---|---|
| 1 | `tap(x=365,y=400)`，ADB 执行，打开设置搜索入口 |
| 2 | `tap(x=397,y=1482)`，ADB 执行，模型理由为关闭手写笔试用弹窗 |
| 3 | `tap(x=350,y=108)`，ADB 执行，点击搜索框 |
| 4 | 返回 `text` 动作 JSON，随后追加说明文字和第二个 JSON 对象；解析失败，没有执行输入 |

步骤坐标来自当次会话输出；持久化事件仅保存动作类型、理由和简短观察，没有保存坐标。第 4 步异常为：

```text
json.decoder.JSONDecodeError: Extra data: line 3 column 1 (char 75)
```

模型输出形态如下，省略了重复的理由文本，**不是完整原始响应**：

```text
{"type":"text","text":"About phone","reason":"..."}

打字动作已准备……
{"type":"text","text":"About phone","reason":"..."}
```

`parse_object()` 使用 `json.loads` 严格解析单个对象，因此拒绝了带额外内容的响应。没有从两条动作中猜选或执行任意一条。

定向脚本异常后通过 `finally` 释放了租约。事件 43 的 `manual_retest_outcome` 及复测 JSON 是随后依据异常和事件记录补记的测试结果，不是守护进程自动生成的失败状态。普通 `run_once` 的调度仍保留 RT-01 的 `completed`；这不代表 RT-02 成功。

最终截图确认停留在设置搜索页，搜索内容为空，未到达关于手机页面。

![定向复测最终画面：设置搜索页，未到达关于手机](test-evidence/2026-10-19/retest-after.png)

## 6. 问题记录

| 编号 | 优先级 | 问题及影响 | 证据与状态 |
|---|---|---|---|
| DL-T01 | 高 | GUI 响应可能在 JSON 后混入文字及第二个对象，导致当前任务中断 | RT-02 确认；未修复，未测复现概率 |
| DL-T02 | 高 | 模型可能输出不满足策略要求的 swipe 坐标，阻断导航任务 | IT-06 确认；RT-02 未复现同类错误，不能认定已修复 |
| DL-T03 | 中 | 当前任何 `finish` 都映射为 `completed`，即使理由是屏幕不确定或目标未确认；容易高估任务成功率 | IT-05 及 `loop.py` 实现共同证实；未修复 |
| DL-T04 | 中 | 事件缺少完整动作字段和模型响应，限制失败归因与确定性复放 | IT-06 无法定位具体坐标字段；完整定向脚本及原始响应未持久化 |

API stderr 为空不表示任务没有失败：策略拒绝和任务结果主要保存在 SQLite 事件或标准输出中。RT-02 的 traceback 出现在单独的测试会话中，不在守护进程 stderr 中。

`run_once` 源码包含异常记录和 600 秒退避，但 RT-02 直接调用 `execute_goal`，本轮没有验证真实 JSON 失败经过外层处理后自动退避的行为。

## 7. 后续修复与复验建议

以下是建议，尚未实施：

1. 约束模型仅返回单个结构化动作，对格式错误进行有限次数纠正；仍保留严格解析，不从混合文字中猜选动作。
2. 明确截图像素尺寸和完整动作字段，对 tap/swipe 必填参数做结构化校验；无效动作应记录可定位的字段错误。
3. 区分“目标达成”“不确定而停止”“策略阻断”和“执行失败”，避免把全部 finish 视为完成。
4. 增加有界、经过脱敏的模型响应与完整动作记录，保存可重放的定向测试脚本。
5. 修复后重新执行现有 10 项测试，并覆盖缺失坐标、混合 JSON、模型不确定结束、外层异常退避；使用真实模拟器验证目标页面实际到达。

复验通过应同时满足：模型输出合法、ADB 动作执行、截图确认目标页面、事件状态与画面一致。当前样本量不足以给出 GUI 成功率或可靠性指标。

## 8. 运行状态、复跑入口与证据边界

报告整理时的进程：

| 组件 | PID | 状态 |
|---|---|---|
| Android QEMU | 16948 | 存活，`emulator-5582` 在线 |
| API Python Agent | 34172 | `--mode api --device emulator --forever`，存活 |
| PowerShell 启动包装进程 | 2328 | 存活 |
| 原 mock Agent | 45652 | 已停止 |

PID、调度和画面只代表采样时状态，后续可能变化。当前是后台进程运行，未配置 Windows 开机启动或系统服务。

现有测试入口（PowerShell，在项目目录执行）：

```powershell
$env:PYTHONPATH = 'src'
$env:PYTHONUTF8 = '1'
python -m unittest discover -s tests -v

# 普通真实 API 单轮：会修改设备状态和数据库；主脑可能选择其他目标或休息。
.\data\start-api.ps1 -Once -Force
```

API 单轮应在设备空闲时运行；本次复测前确认租约为空，后台 Agent 尚未到唤醒时间。`--force` 只绕过唤醒时间，不绕过设备租约。CLI 退出码不能单独表示任务通过，需同时检查返回的 `status`、事件和实际画面。

证据目录：[test-evidence/2026-10-19](test-evidence/2026-10-19/)。核心文件：

| 文件 | 内容 |
|---|---|
| [unit-tests.log](test-evidence/2026-10-19/unit-tests.log) | 报告整理时重跑的 10 项测试输出 |
| [state-snapshot.json](test-evidence/2026-10-19/state-snapshot.json) | 只读导出的事件、记忆、调度、租约和完整性结果；不包含 API 密钥 |
| [retest-gui.json](test-evidence/2026-10-19/retest-gui.json) | 定向失败的补记结果与已执行动作记录 |
| [retest-after.png](test-evidence/2026-10-19/retest-after.png) | 定向复测最终画面 |
| [emulator-home.png](test-evidence/2026-10-19/emulator-home.png) | 初始 Android 主屏截图 |
| [settings-before-retest.png](test-evidence/2026-10-19/settings-before-retest.png) | 定向复测前的系统设置画面 |
| [api-service.stdout.log](test-evidence/2026-10-19/api-service.stdout.log) | 常驻进程输出快照，包含首轮 `blocked` 结果 |
| [api-service.stderr.log](test-evidence/2026-10-19/api-service.stderr.log) | 常驻进程错误流快照，采样时为空 |
| [SHA256SUMS.json](test-evidence/2026-10-19/SHA256SUMS.json) | 证据文件的 SHA-256 校验值 |

数据库保留原始墙钟时间戳。首轮相邻事件 20、21 的时间戳相差约 8 小时，缺少单调时钟记录，不能据此判断真实耗时或声称已稳定运行 8 小时；本报告按事件 ID 还原执行顺序，不提供链路延迟指标。

本报告只新增文档和证据快照，没有修改 Agent 源码、动作策略或模型适配逻辑；已发现问题仍保持未修复状态。
