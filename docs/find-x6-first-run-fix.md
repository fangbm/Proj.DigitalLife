# Find X6 首次一小时运行修复 · 2026-10-23

本轮基于 `9fa6743`，只修改故障相关代码并离线验证。没有启动手机、真实 DeepSeek 会话、十分钟复测或24小时运行。原会话和生活库均保留原件。

后续用户十分钟实测及2026-10-26冷却修复见文末；以下2026-10-23交付结论保留当时口径。

## 已核实的真实基线

`data/find-x6-all-apps-1h-01`：`completed_duration / GLOBAL_DEADLINE`，3600.081643秒，14周期，无待审阅状态。实际确认的两条输入均为 **launch**：AIDA64、LibChecker；事后前台匹配请求且画面内容变化。不是 tap/swipe 成功，也不代表业务目标完成。

只读 Status 对原会话给出：41次API请求、2条确认输入、0个独立验证成功目标、2个被拒输入候选、4个格式无效候选、8个合法逻辑finish。格式无效候选也包含finish，不能把全部12个finish候选当作设备动作。三个 `GOAL_NOT_VERIFIED` 的实际状态已经是 `exploration_observed`，不改写历史为失败或成功。

## 两次具体拒绝

| 周期 / TaskRun | 原意与绑定 | 实际拒绝分支 |
|---|---|---|
| 8 / `cc553115-1b3a-401a-86d0-f017022222c3` | LibChecker Apps列表，已绑定RecyclerView `[0,363,1240,2772]`；swipe `(620,1920)→(620,882)`，400ms | `DedicatedPhone._check_touch_blockers` 的窗口frame相交分支抛出 `TARGET_OCCLUSION_OBSERVED`，上层归入 `PHYSICAL_PRE_DISPATCH_DENIED` |
| 9 / `dbccb928-6618-472a-bdd2-27617fe74afe` | LibChecker搜索按钮 `…:id/search`，bounds `[904,167,1072,335]`；tap `(988,333)` | 同一窗口遮挡分支。Schema与控件绑定、fresh重验证均已通过，输入未发送 |

当时仍有 back、open_app 等低风险替代动作；旧路径直接结束周期，没有把具体guard反馈给GUI改选。

**证据限制：原会话没有保存WMS原文或具体命中窗口，无法确定哪一个窗口、其真实可见性与层序。因此不能断言两次均为误判，也不能伪造当时完整遮挡复放。** 已确认的实现问题是：旧分支把 `mViewVisibility=0x0 + mHasSurface=true + frame相交` 当作遮挡，未区分后台保留surface、明确隐藏、层序。用合成WMS与真实动作几何可确定性复现误拒路径；这不是自然故障样本。

修复仅在全App专用模式：逐窗口检查可见/隐藏和层序证据；后台保留frame不能单独作遮挡依据，明确可见overlay、上层外部窗口、输入DROP区域仍拒绝，并检查完整swipe路径。最后一次窗口刷新后、每条实际输入前再次检查，防止非焦点overlay晚到而绕过先前检查。该晚到路径也有修复前失败的合成反例 `late-overlay-before.log`，不是自然样本。新ActionAttempt记录窗口索引、frame、可见标志、判定及原文哈希，不存窗口原文。真正执行前拒绝在原三次候选预算内重新观察、反馈原因、由模型改选；未知已分发结果仍立即停止，绝不重发。

另修正了toolbar坐标：旧12%内容区剪裁将搜索点击挤到bounds底边，新坐标为真实中心 **`(988,251)`**。两份匹配的历史XML均能离线重算；未据此宣称真机点击已经成功。有限WebView/Canvas继续只读观察、back/open_app，不伪造控件或开放任意坐标。

## 五次 EXECUTOR_UNCERTAIN 逐条分类

| 周期 / TaskRun前缀 | 分类 |
|---|---|
| 1 / `a56990c8` | AIDA64已启动，但摘要目标被更新弹窗挡住；模型finish uncertain，属目标未达到的逻辑结束；已有一次launch，不是未知输入 |
| 2 / `fa387e40` | 同目标continue仍被弹窗挡住，零输入；逻辑结束正常，但重复选择没有新进展 |
| 3 / `b06065fe` | 改选设备信息观察，仍停在同一弹窗；零输入，目标范围无法证实 |
| 4 / `22bcdcd5` | `readonly_fact(android_version)`，模型误以为必须通过弹窗才能读版本，过早finish uncertain，未调用独立事实验证 |
| 5 / `6ac2ef0e` | 同一readonly_fact问题重复；零输入，未独立验证 |

五次均没有ADB未知结果。finish保持 `not_executed`，新记录另标逻辑结束、无设备输入，不改成executed。恢复全App提示中遗漏的事实能力说明：finish achieved只是请求独立固定读取，不能自证成功。观察合同只产生 `exploration_observed`；遮蔽占位图不能充当内容证据。补充窄原生弹窗负按钮能力（button2、明确“否/取消”等、真实对照button1、完整控件复核），让更新弹窗可被安全拒绝；肯定按钮、敏感表单及风险动作继续禁止。没有预编排任何目标。

## MODEL_OUTPUT_INVALID

周期14 `df7522c6-b81b-4dad-89de-5ccf42016cb4`：**GUI Planner** 连续三个合法单JSON finish，`reason` 长474、441、456字符，超过Schema的400字符上限。不是Brain错误、重复键、App清单或枚举问题。原两次修复都收到字段超长提示，但仍重复列举应用版本信息；预算耗尽后诚实失败。周期11的445字符同类错误曾在一次修复后变为162字符而通过。

现在要求简短reason（建议120、硬上限仍400），修复反馈给出实际长度并明确不得抄应用清单，删除反馈中的整段错误原值。不截断后强行接受，不宽松解析，不增加三次候选/请求预算。

## 记忆与上下文

旧设备经历只在当前专用模式的检索视图中降为原分值的0.15；当前设备证据与兴趣支持排序优先。原记忆、数值权重、来源、SQLite行不重写。查询减少旧兴趣词的自我强化；主脑仍可选任何低风险目标或休息。没有强制视频、搜索或动作频率。

请求视图保留记忆ID/来源、结果、具体拒绝字段和continuation错误，去除递归反思、重复长失败文本、重复App清单和过长动作历史。历史真实账本的prompt_tokens：Brain 14次 **29,737–45,563**；GUI 16次 **14,824–15,478**；Reflection 11次 **9,548–13,556**。

使用[DeepSeek官方离线tokenizer](https://api-docs.deepseek.com/quick_start/token_usage/)对固定历史输入作可复算文本比较：

| 文本样本 | 原视图tokens | 新视图tokens |
|---|---:|---:|
| 14份Brain user上下文 | 427,649 | 190,685 |
| 11份重构初始GUI user视图 | 28,092 | 18,489 |
| 11份重构Reflection user视图 | 119,092 | 45,988 |

**这些不是修复后的真实API用量，不含图片、系统提示和聊天封装。GUI/Reflection是明确重构样本；未声称原请求哈希完全一致或实际账单节省比例。** tokenizer SHA-256：`89085f12ef79460ac5f66d1119325ddfc694b4ab209d80bbd81d35f081dc9614`。脚本与逐条计数在私有证据目录，未保存新的完整敏感提示词。

## 修改与验证

- `app_exploration.py`：有证据的遮挡判断、toolbar中心、窄负按钮、规划/事实能力提示；严格模式guard不变。
- `loop.py`：具体guard审计与有界改选、超长字段修复提示、finish逻辑标记；未知传输停止不变。
- `memory.py`、`autonomy.py`、新 `request_context.py`、`llm.py`：专用模式环境相关性及精简发送视图，原API/动作预算、休息退避不变。
- `verification_registry.py`：遮蔽图不得当作真实内容或交视觉验证。
- `dedicated_device.py`、启动脚本：只读Status统计、可选 `-Minutes 10`，原 `-Hours 1/24` 不变；600秒不能替代24小时所需的一小时。

全量 **964/964 PASS**（193.092秒），原949项及原测试源码/断言保留，新增15项仅针对本轮问题。测试全为 `offline_fixture`，运行时源码哈希保持一致。原1小时会话、PNG/XML、生活库、租约库、DPAPI和旧fixture哈希/SQLite检查见私有 `integrity.json`。十分钟真机tap/swipe、持续探索和新兴趣尚未观察，必须由下次真实记录证明，不能用这些离线结果代替。

仅一份本轮结果说明。必要原始证据保存在 `data/find-x6-first-run-fix-dev-01/`：`baseline.json`与3份一致SQLite备份、`historical-classification.json`、`target-replay.json`、`new-regression-final.log`、`full-offline-final-r2.log`、`context-measurement.json`和`integrity.json`。未覆盖原失败日志，也不上传私有数据库、截图/XML或模型正文。

## 用户手动十分钟复测

连接并解锁原Find X6。沿用已经存在的专用生活库，不重新导入：

```powershell
& 'D:\codex\Proj.DigitalLife\data\start-dedicated-device.ps1' -Command Start -Minutes 10 -Session data\find-x6-all-apps-fix-10m-01
```

另一个终端使用同一脚本 `-Command Status` 或 `-Command Stop`，Session不变。Status只读统计，不调用ADB/API。保留现有唤醒、休息和预算，所以短测可能包含等待；没有有效操作时如实报告。完成十分钟不等于探索成功；本轮没有启动24小时运行。

## 2026-10-26：十分钟实测后的冷却计时修复

用户实跑 `find-x6-all-apps-fix-10m-01` 正常到期600.066秒：两个休息周期、一次模型请求、零输入。第一轮模型休息60秒；休息更新 `last_update` 却保留连续失败3，第二轮把此前旧故障重新作为近期故障，强制退避526秒。这轮没有验证tap/swipe恢复。

仅修正 `life_state.py` 与 `autonomy.py`：新增持久 `last_failure_at`，只在真实失败时更新；休息、观察和普通状态更新不延长冷却。原600秒窗口、最低休息、请求/动作预算、未知分发和紧急停止策略保留。上下文给出冷却剩余秒数与有效标志，失败计数不人为清空。旧JSON优先从同主体、同运行来源的已结束失败TaskRun恢复时间；无源证据时固定原 `last_update` 作一次保守回退，后续正常周期才写入新增字段，无SQLite表结构迁移。

真实生活库副本恢复的最后失败时间为 `1792722449.31515`，而旧状态更新时间为 `1792986767.8782678`；旧失败已过冷却，保留计数3但剩余冷却为0。读取未改写原JSON。原库、两次真实会话、截图、密钥、原测试文件均保持原字节，不重置唤醒或预算。

原问题有修复前失败日志；新增6项覆盖真实时间线的离线复现、原期限到期、新失败、来源隔离、旧JSON及SQLite重开/独立子进程。全量 **970/970 PASS**（230.086秒）。证据位于 `data/find-x6-cooldown-fix-dev-01/`。本次仍为离线修复，没有调用真实模型或ADB；主脑仍可自主休息。

下一次由用户手动运行新目录，不覆盖已结束会话：

```powershell
& 'D:\codex\Proj.DigitalLife\data\start-dedicated-device.ps1' -Command Start -Minutes 10 -Session data\find-x6-all-apps-fix-10m-02
```
