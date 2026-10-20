# Phase 1.1 GUI Reliability 开发交付与复验报告

日期：2026-10-19（Asia/Shanghai）  
交付版本：0.1.1  
范围：用户提供的 Phase 1.1 GUI 执行可靠性规格；[原始规格归档](specs/PHASE1_1_GUI_SPEC.md)

## 1. 结论

**已实现本阶段四个核心工作包，并达到固定任务初步工程门槛：20 次真实模拟器复验，19 次独立验证成功（95%），1 次保护性失败；本样本观察到的假成功 0 次、未经授权高风险动作 0 次。**

交付版 41 项自动化测试全部通过，其中保留原有 10 项测试的测试意图，并新增 31 项测试。原 mock 闭环的成功状态断言随契约从 completed 更新为 succeeded。

这些结果仅适用于本机隔离 Android 15 模拟器上的固定“关于手机”任务，不表示长期无人值守自主 Agent 已验收。成功证据主要来自独立视觉请求，仍存在同一模型服务的共同判断偏差。

## 2. 实现清单与兼容性

| 工作包 | 已交付行为 | 对应文件 |
|---|---|---|
| A / DL-T01 | 唯一 JSON、严格判别联合 Schema、未知字段拒绝、重复键/非有限值拒绝；每步最多 3 次生成，格式最多 2 次修复 | models.py、llm.py、loop.py |
| B / DL-T02 | PNG IHDR 尺寸/CRC/哈希；整数与像素边界；观察绑定与执行前方向检查；坐标最多 1 次重生成；字段级错误 | models.py、devices.py、policy.py、loop.py |
| C / DL-T03 | finish 声明与成功分离；succeeded/uncertain/blocked/failed/step_limit/rested；独立 About phone 验证器；旧 completed 不升级为成功 | verification.py、loop.py、storage.py |
| D / DL-T04 | TaskRun、ActionAttempt、观察元数据、脱敏响应摘要、API ID、双时钟；统一定向包装、异常收尾、只读复放和固定任务复验 | storage.py、audit.py、replay.py、benchmark.py、cli.py |

另外更新 config.py、__init__.py、pyproject.toml、.env.example、README.md 和架构/运维文档；本地 data/start-api.ps1 增加定向和 benchmark 参数，继续沿用原 DPAPI 密钥加载方式。

现有 CLI 的 mode/device/cycles/force/forever 保留。成功状态、finish.outcome 和严格动作字段是明确的契约变化；依赖旧 completed 的消费者需要升级。实体机仍禁止坐标/文本操作，标成模拟器的设备必须通过 QEMU 身份检查。启动白名单不被解释为应用沙箱。

完整接口、重试预算、退出码、运维和故障排查见 [实现与运行说明](PHASE1_1_GUI_RELIABILITY.md)。

## 3. 自动化与兼容性验证

- 原有 10 项测试及新增测试全部通过，共 41 项，最终耗时 3.595 秒；该数字仅是离线测试耗时。
- P11-01—P11-20 均有具名用例：覆盖尾随文字、多个对象、数组、缺失/越界坐标、旋转、有限修复、finish 语义、搜索页假成功、设备超时、异常收尾、旧库、退避、脱敏、多进程竞争和离线复放。
- 新增独立视觉备用路径、伪造搜索页成功声明、备用验证预算、JSON 参数选择、非有限值、哈希保留、模拟器身份等检查。
- 多进程用例启动了独立 Python 子进程争抢同一租约，第二执行者未取得租约；这不是长时间并发压力测试。
- 对一条真实成功 TaskRun 执行 CLI dry-run 复放，输出 adb_actions_sent=0。脱敏文本及无原文的格式错误会明确标记复放限制，不冒充完整响应回放。
- API 超时/429/5xx、ADB 结果未知和异常退避主要采用故障注入验证；没有主动对真实服务制造限流或断网。

证据：[最终测试输出](test-evidence/phase11-2026-10-19/unit-tests-final.log)、[真实离线复放](test-evidence/phase11-2026-10-19/live-replay.json)。

## 4. 真实复验配置与执行方法

| 项目 | 配置 |
|---|---|
| 主机 / Python | Windows 11 / Python 3.13.5 |
| 设备 | digitallife-api35，emulator-5582，Android 15 / API 35，720×1600 |
| 模型路由 | api.deepseek.com / deepseek-flash |
| 服务端格式参数 | json_object；本地严格解析始终启用 |
| 模型行为 | DeepSeek thinking 显式关闭 |
| 固定目标 | 从设置主界面进入关于手机/设备信息页，观察至少一个有效设备字段 |
| 步数 / 修复预算 | 15 步；每步最多 3 次生成；格式最多 2 次、坐标最多 1 次修复 |
| 起点重置 | 每轮在租约内 force-stop Settings、唤醒、以 NEW_TASK|CLEAR_TASK 打开设置、等待 2 秒并核对主 Activity |
| 允许启动包 | Chrome、系统设置；本轮唯一模型 launch 为系统设置 |
| 证据 | 显式开启隔离模拟器截图；每轮脱敏 TaskRun、最终图像、双时钟与参数清单 |

前期 3 次预检分别发现了跨包设置搜索页未被原重置方式清掉、UIAutomator 无法取得 idle state，并确认了独立视觉备用路径成功。这些使用开发中代码的预检不计入正式 20 次；没有删除或挑选正式失败轮次。

API 35 上设备信息页的标题实际为 About emulated device。UIAutomator 在该页面持续报告 could not get idle state；实现采用有界读取/冷却及独立视觉备用验证，不将控件树失败当成功。20 次正式复验中的成功及事后审计均标注独立视觉证据。

DeepSeek json_object 能力探测中参数被接受，但一次探测响应不符合 JSON；不能据此依赖服务端强约束。[探测记录](test-evidence/phase11-2026-10-19/capability-probe.json)

### 逐次结果

| 次数 | 任务结果 | 模型 API 总次数 | 已执行模型动作 | 单调耗时/秒 | 事后独立审计 | 最终画面 |
|---|---|---:|---:|---:|---|---|
| 01 | succeeded | 6 | 3 | 26.593 | verified | [截图](test-evidence/phase11-2026-10-19/trial-01-final.png) |
| 02 | failed | 4 | 2 | 13.470 | not_met | [截图](test-evidence/phase11-2026-10-19/trial-02-final.png) |
| 03 | succeeded | 7 | 4 | 20.502 | verified | [截图](test-evidence/phase11-2026-10-19/trial-03-final.png) |
| 04 | succeeded | 6 | 3 | 15.500 | verified | [截图](test-evidence/phase11-2026-10-19/trial-04-final.png) |
| 05 | succeeded | 6 | 3 | 25.746 | verified | [截图](test-evidence/phase11-2026-10-19/trial-05-final.png) |
| 06 | succeeded | 6 | 3 | 15.744 | verified | [截图](test-evidence/phase11-2026-10-19/trial-06-final.png) |
| 07 | succeeded | 7 | 3 | 13.988 | verified | [截图](test-evidence/phase11-2026-10-19/trial-07-final.png) |
| 08 | succeeded | 6 | 3 | 12.865 | verified | [截图](test-evidence/phase11-2026-10-19/trial-08-final.png) |
| 09 | succeeded | 6 | 3 | 25.913 | verified | [截图](test-evidence/phase11-2026-10-19/trial-09-final.png) |
| 10 | succeeded | 6 | 3 | 14.118 | verified | [截图](test-evidence/phase11-2026-10-19/trial-10-final.png) |
| 11 | succeeded | 6 | 3 | 14.134 | verified | [截图](test-evidence/phase11-2026-10-19/trial-11-final.png) |
| 12 | succeeded | 6 | 3 | 13.136 | verified | [截图](test-evidence/phase11-2026-10-19/trial-12-final.png) |
| 13 | succeeded | 6 | 3 | 12.900 | verified | [截图](test-evidence/phase11-2026-10-19/trial-13-final.png) |
| 14 | succeeded | 6 | 3 | 12.956 | verified | [截图](test-evidence/phase11-2026-10-19/trial-14-final.png) |
| 15 | succeeded | 6 | 3 | 16.068 | verified | [截图](test-evidence/phase11-2026-10-19/trial-15-final.png) |
| 16 | succeeded | 6 | 3 | 13.800 | verified | [截图](test-evidence/phase11-2026-10-19/trial-16-final.png) |
| 17 | succeeded | 6 | 3 | 26.880 | verified | [截图](test-evidence/phase11-2026-10-19/trial-17-final.png) |
| 18 | succeeded | 6 | 3 | 13.690 | verified | [截图](test-evidence/phase11-2026-10-19/trial-18-final.png) |
| 19 | succeeded | 6 | 3 | 15.098 | verified | [截图](test-evidence/phase11-2026-10-19/trial-19-final.png) |
| 20 | succeeded | 7 | 3 | 13.973 | verified | [截图](test-evidence/phase11-2026-10-19/trial-20-final.png) |

### 汇总与失败样本

- 成功 19/20，95%；门槛为至少 18/20。
- 总模型请求 121 次：GUI 82 次、任务验证及事后独立审计共 39 次。运行前探测、预检和恢复常驻服务时的主脑请求不在该总数内。
- GUI 合法响应 80/82，97.56%。第 7、20 轮各出现一次重复 JSON 键，候选被拒且没有执行；同一步下一次生成合法，最终目标成功。这是实际在线重生成证据，不是仅靠 mock 推断。
- 已确认执行的模型动作 60 次：40 次 swipe、19 次 tap、1 次 Settings launch；传输不确定动作 0 次。每轮 setup 另有 3 条改变设备状态的命令，共 60 条，与模型动作数分开。
- 任务单调耗时最小 12.865 秒、中位数 14.126 秒、最大 26.880 秒；包含起点重置、GUI 和任务内验证，不含随后额外的事后独立审计。
- 第 2 轮失败原因 REPEATED_ACTION：连续第三次提议相同滑动，被保护逻辑停止；只执行前两次滑动，最终仍在设置列表。事后审计 not_met，未误标成功。该策略可能拦住合法的连续滚动，保留为后续改进点，未为提高统计值删除该轮。
- 已检查全部 20 张最终原图对应的五张 QA 总览，以及已执行动作参数：19 张确实显示 About emulated device 与型号/Android 版本；失败图仍是设置列表。本样本没有文本输入、账号/支付/权限操作，唯一额外启动为允许的系统设置。

原始逐次数据：[trials.jsonl](test-evidence/phase11-2026-10-19/trials.jsonl)；汇总：[summary.json](test-evidence/phase11-2026-10-19/summary.json)；图像/动作审阅记录：[review.json](test-evidence/phase11-2026-10-19/review.json)。

## 5. 数据库与历史证据保留

升级前已使用 SQLite backup API 保存 data/phase11/pre-phase11.sqlite3，另保存 baseline-source.zip 和源码 SHA-256。迁移只新增表、索引和 schema version，不重写旧表结构或旧结果。

逐行比较升级前备份和当前库：50 条既有 events、13 条 memories 的原始值保持一致；旧 completed 值未转为 succeeded。原 docs/test-evidence/2026-10-19 的 8 个被校验文件哈希一致。正常新运行会追加日志、记忆并更新当前 schedule，未清空数据库。

复验收尾快照：quick_check=ok、WAL、user_version=11、23 个 TaskRun、91 条候选尝试、0 个遗留租约；随后恢复常驻前的自主休息检查又追加了独立 TaskRun。

证据：[历史保留检查](test-evidence/phase11-2026-10-19/preservation-check.json)、[迁移后数据库健康快照](test-evidence/phase11-2026-10-19/database-health.json)。

## 6. 源码版本与交付证据

正式复验开始时记录了每个 Python 源文件 SHA-256，复验期间没有修改执行代码。复验后只将 __init__.py 的旧 __version__ 常量从 0.1.0 同步为 0.1.1；执行、校验、数据库和验证器源码未变，随后再次运行全部 41 项测试通过。

[复验时源码与参数](test-evidence/phase11-2026-10-19/metadata.json) 和 [交付时源码清单及唯一差异说明](test-evidence/phase11-2026-10-19/delivered-source-sha256.json) 均保留，未把两个不同清单冒称为完全同一字节版本。目录没有 Git 仓库，因此没有创建提交或 PR；可用备份和清单审查变更。

证据入口：

- [实现与运行说明](PHASE1_1_GUI_RELIABILITY.md)
- [正式 20 次逐条结果](test-evidence/phase11-2026-10-19/trials.jsonl) 及同目录 trial-XX-TaskRun.json
- [QA 总览 1](test-evidence/phase11-2026-10-19/qa-finals-1.png)、[2](test-evidence/phase11-2026-10-19/qa-finals-2.png)、[3](test-evidence/phase11-2026-10-19/qa-finals-3.png)、[4](test-evidence/phase11-2026-10-19/qa-finals-4.png)、[5](test-evidence/phase11-2026-10-19/qa-finals-5.png)
- 全部原始隔离 AVD 观察图片仍在 data/phase11/live20-screens，文档证据目录另保存逐轮最终图。原始 JSON 响应仅保留哈希与脱敏摘要，不保存完整自由文本。
- 新证据的 SHA256SUMS.json 为归档后完整性清单；不含 API 密钥或 DPAPI 密钥文件。

## 7. Definition of Done 核对

- [x] 原有测试意图保留，41 项测试全绿。
- [x] 多对象/尾随文字/重复键严格拒绝；有限生成修复不重复执行上一动作。
- [x] 缺失及越界坐标定位到字段，实际 PNG 尺寸，方向变化拒绝旧候选。
- [x] 新 succeeded 有独立 verified 证据；uncertain/rested/历史 completed 不冒充成功。
- [x] 每个定向任务有独立 TaskRun，关联候选、执行、最终哈希和验证；异常统一收尾并释放租约。
- [x] 旧库和原证据保留，迁移、退避、模拟器身份和实体机权限有自动测试。
- [x] 20 次固定任务复验达到门槛，含失败样本、事后审计及实际图像审阅。
- [x] 阶段标注限于 Phase 1.1 GUI Reliability，不扩展 QQ/TTS/长期自主性。

## 8. 仍存在的限制与 Phase 1.2 建议

1. UIAutomator idle 问题尚未根治；多数验收依赖独立视觉请求，执行与验证使用同一模型服务，存在共同偏差。优先提供稳定控件检查，再扩展更多目标。
2. 连续相同动作保护可能误伤有效滚动。本次第 2 轮展示了该限制；后续可结合画面变化判定，但应重新固定源码并完整复验。
3. 20 次样本没有覆盖跨天稳定性、真实限流/断网、进程强杀后恢复或高强度多进程争用。
4. 通用目标没有独立规则时只记 uncertain，成功率统计必须按目标/验证器区分，不能拿固定任务的 95% 推广。
5. 脱敏摘要不能完整复放敏感 text 或未保存的异常原文。原始截图默认为关闭，隔离测试证据需按本地保留策略管理。

## 9. 服务恢复

常驻前以完整主脑 run_once 做了一轮真实检查，返回 rested / REST_DECISION，无 GUI 动作，状态已独立落盘。新版后台 API Agent 已重新启动，继续使用原有 DPAPI 配置和 emulator-5582；不启用 Windows 开机自启动。

恢复时 Python PID：`49384`；包装进程 PID：`50664`。PID 和运行状态是采样值，详见 [恢复状态](test-evidence/phase11-2026-10-19/service-status.json) 与 [主脑检查输出](test-evidence/phase11-2026-10-19/service-restart-check.log)。
