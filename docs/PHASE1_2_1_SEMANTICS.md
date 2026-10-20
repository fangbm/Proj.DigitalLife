# v0.2.1 / Phase 1.2.1：语义观察、动作绑定与运行契约

本轮扩展 v0.2.0 的 PhoneLife、AutonomousLife 与独立验证组件；没有改成预编排自主任务。固定目标 Benchmark 和没有指定目标的自主会话分别验收。最终实测结果见 `PHASE1_2_1_DELIVERY.md`，本文件描述接口和限制。

实测未完成项：自主深滚动后，一致性检查无法持续恢复可信语义；主脑引用拒绝的当轮反馈与审计已有明细，但跨轮情景记忆仍只有泛化错误。这些问题阻断完整验收，不能把固定页面导航成功率等同于长期自主稳定性。

## 1. Android 15 idle 故障与后端选择

目标设备仍为隔离 AVD `digitallife-api35`，Android 15 / API 35，serial `emulator-5582`。本轮原路径实测约 11.92 秒返回 `ERROR: could not get idle state.`。AOSP DumpCommand 在读取根节点前调用 `waitForIdle(1000, 10000)`，连续事件使这一步超时；这不等于 UIAutomation 的控件节点不可用。

参考原始实现：[AOSP DumpCommand](https://android.googlesource.com/platform/frameworks/uiautomator/+/34f0bc324a145846e938b7737be0166dbf6b92fd%5E1..34f0bc324a145846e938b7737be0166dbf6b92fd/)。这解释了超时位置；没有证据把具体持续事件归因于某个应用或动画。

`SemanticObservationProvider.collect(device, observation)` 返回 `SemanticObservation`。默认 `BoundedSemanticProvider` 最多尝试两个后端：

| 后端 | 路径、预算 | 成功条件与限制 |
| --- | --- | --- |
| A `uiautomator_xml` | 现有 hierarchy；ADB 12 秒，整个后端 14 秒 | XML 禁 DTD/ENTITY；缺失 editable/visible 等属性仍为 null，不能据此授权触屏；不完整时切换 B |
| B `shell_ui_snapshot/1` | 只读 Java main，经现有 ADB/app_process/UiAutomation 读取；单命令 4 秒，整个后端 6 秒 | 跳过全局 idle 等待；两次树、前后 Activity、截图正文及方向/尺寸一致；每次校验 JAR SHA、响应 nonce；最多 1800 节点/60 层/20 窗口 |
| C 可选服务 | 接口允许接入只读 AccessibilityService，默认未实现、未安装、未授权 | 未来必须明确安装/授权审计、租约、最小权限与停用方法；绝不能加输入接口绕过围栏 |

B 的源码在 `android/semantic-snapshot/src/org/digitallife/ReadOnlySnapshot.java`，构建脚本生成 package-data 中的 `assets/ui-snapshot.jar`。它没有输入、安装、授权、设置修改或任意 shell 执行方法。`--prepare-semantics` 在有限共享租约中向 `/data/local/tmp` 部署哈希命名的 JAR；不是 APK 或后台服务。使用的是设备现有 ADB shell/UIAutomation 权限，不静默增加权限。未部署/哈希不符时拒绝使用，不自动下载安装。

各后端失败后有 60 秒有界冷却，切换和失败原因持久化。冷却内不反复等待 idle，也不产生 GUI 输入。两条路径均失败返回 `semantic_unavailable`，需要控件身份的动作禁止执行。瞬态页面变化也可能保守阻断，不用猜测来换取动作数。

## 2. 一致性和控件身份

观察包括截图 UUID/SHA/实际 PNG 尺寸、方向、前台包/Activity、provider/时间、controls、scrollable_regions、overlays、quality/failure、后端尝试时长、树及正文哈希。每个控件记录边界、文字/描述、类、resource ID、包、父子关系、来源，以及可空的 clickable/scrollable/enabled/checkable/editable/visible/password/long_clickable。

未知布尔属性保持 null。隐藏节点可能有反向裁剪边界，只有 `visible=False` 时保留这类原始边界；永不据其触屏。主应用窗口必须恰好一个；外来/非活动窗口和覆盖层拒绝。只有已识别 `com.android.systemui`、type=3 且完全位于顶部 12% 或底部 8% 的窗口按系统栏排除；展开通知等正文覆盖不能享用这个例外。正文截图比较沿用经过测试的顶部/底部 8%、侧面 5% 排除。

Android window ID 随 UiAutomation 连接重新分配。审计保留原始 ID，跨采集比较使用结构路径、属性、边界、窗口分类、Activity 和画面；不能把连接 ID 变化视为页面变化。目标控件 ID 根据观察 UUID/窗口/节点路径产生，仅在当前观察有效。

一致性采用严格正文相等，可能受局部动态内容影响而拒绝；没有宣称原子截屏或适用于所有 Android ROM。两个连续采集和 dispatch 前复查缩小竞态，ADB 外的人类触屏/不遵守共享租约的外部工具仍可能介入。

## 3. 动作与能力

保留 schema 1.1、严格单 JSON/重复键检查和原 ActionPolicy。增加严格语义意图：

```json
{"type":"tap_control","reason":"...","observation_id":"current UUID","control_id":"provided ID"}
```

其余为 `scroll_region`（scrollable_region_id、direction）、`navigate_back` 和 `open_allowed_app`（package）。方向使用 `toward_end`（手指向上，显示下方内容）/`toward_start`，兼容早期 up/down 的手指方向定义。程序计算最终点击中心或区域内竖向手势；模型坐标也必须先识别同一可信控件，再按程序坐标执行。

绑定记录观察、包/Activity、控件 ID/全部属性/边界、证据后端、capability 与最终 Action。执行前重新观察，复核正文、树、Activity、方向、尺寸、覆盖层和目标。失效不给 ADB，最多一次引用修复、两次格式修复、一次坐标修复，总生成上限仍为三。被拒候选/给模型的反馈均记录；不确定传输照旧先持久化 uncertain，再重新观察，禁止自动重发。

`CapabilityRegistry` 版本 `capabilities/1`：

| capability_id | 范围、证据 | 每任务使用上限 |
| --- | --- | --- |
| observe_screen | 实际 PNG，截图审计 | 200 次执行器观察 |
| inspect_controls | 两次一致树、画面、Activity；独立验证另有有限调用 | 200 次执行器观察 |
| navigate_readonly | Settings 已知 preference 导航行或安全返回；原围栏再加来源/属性检查 | 100 |
| scroll_readonly | 可信 Settings RecyclerView/ScrollView/ListView；端点不落在输入/开关/滑块 | 100 |
| read_public_device_fact | 固定读取 android_version/model/battery_percent/storage_free_kb | 20 |
| launch_allowed_app | 当前观察绑定、仅 Settings，额外原启动白名单 | 20 |

每项结构还有 allowed_packages、applicable_controls、risk_level、required_evidence、verification_strategy、device_constraints、maximum_usage。受有限任务动作/时间/API预算约束；使用统计进入 TaskRun。规则不从模型 `risk_class=low` 授权。触屏还要求明确 enabled/visible、安全属性全 False、精确导航行类/标题 resource ID、已知只读路由、无不安全子节点。默认仅 Settings，允许信息页导航并不允许修改这些页面上的设置。实体机未增加权限。

进展检测保留原两次无效分发预算，以稳定分布正文像素变化为基础，再要求可见语义内容/位置相应变化；系统时钟不算正文语义进展。下一步模型得到真实进展反馈。视觉变化不等于目标成功。

## 4. 主脑、记忆与验证

LifeContext 新增 semantic_environment：程序给出的安全区域/控件引用、能力、质量、最近同来源页面、重复内容摘要及详细失败。选题仍来自真实主脑；无安全机会可休息，不强制 GUI 操作。Benchmark/manual 页面不会进入自主 recent_pages，情景/兴趣继续采用原 run_kind 隔离。

未完成目标提供持久 goal_id、summary、status、remaining_action_budget、can_continue、continuation_reason、current_capability_available。动作预算沿 continuation 祖先累加已确认执行数；不能通过新子 ID 重置预算。继续只接受当前设备/来源的 eligible 持久引用，并保留验证契约；kind=continue 缺引用不能改成新任务。真实 API 主脑总共最多两次请求，第二次收到结构化错误/合法 ID；拒绝、修复和失败退避持久化。已 spent 的预算不会返还。

独立验证支持前台、精确公共页面标题、控件、公共事实、复合规则、新滚动内容。页面须有可信 header resource ID 的精确文字/描述，列表行不是页面打开证据；不凭执行器 finish 推断。`new_scroll_content` 需要已执行 swipe、绑定前态、新可见内容和画面变化，仅给 exploration_observed。

生产自主和新 Benchmark 都启用自然语言/验证契约范围检查；可识别的狭窄页面/事实目标才允许 verified 形成 succeeded，额外主张或未知描述降为 uncertain。该检查是保守词汇范围约束，不是任意自然语言语义理解；可能拒绝合理的自由描述。visual/observe 继续不能证明任意业务成功。低层 `.bind(..., require_consistency=False)` 仅用于独立谓词测试/兼容调用，不应作为新的完整任务成功接口。

## 5. 数据、隐私和运行

core `PRAGMA user_version=12`、life 子系统 schema=1 保持。新增独立 semantic_schema_migrations=1 和 semantic_observations，关联原 Observation/TaskRun；新增表/索引，原始数据不删除/重分类。ActionAttempt 使用原 payload 扩展字段，没有重建原表。只读 replay/status/export 不调用迁移、API、适配器或 ADB。

选择 evidence_dir 时保存私有原 PNG 与完整原控件 JSON，用 SHA 关联；它们可能含手机号/IMEI，不是公开脱敏材料。数据库和内部周期导出同样按私有证据管理。`--export-public` 使用 SQLite mode=ro，只导出哈希、有限动作参数、状态、绑定/不确定标志，丢弃任意页面/模型文字、原图、私有路径。公开包不能替代内部完整审计；不对原图做覆盖式修改。

Windows 示例（命令不含密钥）：

```powershell
$env:PYTHONPATH='src'
$env:PHONE_LIFE_EMULATOR_SERIAL='emulator-5582'
$env:PHONE_LIFE_DB='data/new-life.sqlite3'
python -m phonelife.cli --device emulator --prepare-semantics
# 复用已有 DPAPI 加载脚本；无固定 GUI 目标的自主入口
.\data\start-life.ps1 -Cycles 3 -Database data/new-life.sqlite3 -Output data/new-life-private
# 已配置 API 环境时，20 次固定多页面复验，自动租约内重置起点
python -m phonelife.cli --mode api --device emulator --benchmark-pages 20 --benchmark-output data/new-benchmark --evidence-dir data/new-benchmark-private
# 公开导出不需要 API/设备；必须使用新目录
python -m phonelife.cli --export-public data/new-public
```

真实执行者必须使用相同 `data/phone-life.sqlite3` 租约库和同一 device 名 `emulator`。租约不是对恶意外部 ADB 的系统级互斥。原启动白名单仍仅限制启动，不能称为应用沙箱。24h 入口没有自动启动，本轮只做授权的有限复验。

后续接口：新增包/浏览器需单独注册 capability、真实控件证据、只读动作和独立 verifier；可选服务须经过明确授权。长期 24h Soak、跨 ROM/多语言控件验证、任意第三方 App、长期记忆检索升级与高风险能力均不在本轮验收范围。
