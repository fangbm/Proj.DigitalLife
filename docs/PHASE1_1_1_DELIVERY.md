# Proj.DigitalLife v0.1.2 / Phase 1.1.1 验收报告

交付日期：2026-10-19。状态：本轮实现与验证完成，等待用户审查；Phase 1.2 开发暂停，尚未启动。

本机自动化测试 **69/69 PASS**，包含原 41 项和新增 28 项。原 Android 15 模拟器固定目标正式复验 **20/20 独立验证成功**；观察到的假成功、未经授权高风险动作、正常滚动误阻断均为 **0**。这些结论限于本轮固定目标和离线故障回归，不代表多天自主运行验收。

## 1. 交付内容与架构

先阅读并核对当前源码、README、架构、Phase 1.1 规格、交付报告及历史失败证据，再备份 SQLite 和源码。修改前确认没有正在控制设备的自主进程，原 AVD 离线；通过已有脚本启动原 `digitallife-api35`，没有重建 AVD，没有清库或覆盖旧材料。

| 文件 | 交付变更 |
|---|---|
| src/phonelife/progress.py（新增） | ProgressAssessment、ActionProgressDetector、StagnationGuard；应用内容区、多帧稳定性及变化分布 |
| src/phonelife/loop.py | 基于反馈的阻断与事件；统一有时限的任务/最终截图/审计会话；来源、记忆及调度传递 |
| src/phonelife/storage.py | v11→v12 保留式迁移；来源表/列和隔离调度；只读连接；租约过期 owner 保护 |
| src/phonelife/benchmark.py | 审计回调放入租约；返回后仅查询持久结果；逐轮指标和源码指纹 |
| src/phonelife/models.py | ExecutionResult 增加兼容性可选 post_audit 字段 |
| src/phonelife/cli.py | --replay 使用只读 Store；mock 明确 mock_test 来源；保留旧参数 |
| src/phonelife/__init__.py、pyproject.toml | 版本 0.1.2；新增 Pillow>=11.3,<13 |
| tests/test_hardening.py（新增） | 28 项离线回归，包含真实子进程竞争与异常退出 |
| tests/test_phase11.py | 两处旧定向任务退避断言改查 manual_targeted 命名空间；原 41 个测试保留，test_core.py 无修改 |
| README.md、docs/ARCHITECTURE.md | 当前版本、依赖、能力和文档入口 |
| docs/PHASE1_1_1_HARDENING.md（新增） | 当前架构、来源策略、运行命令、只读限制、后续接口建议 |
| 本报告、docs/test-evidence/phase111-2026-10-19（新增） | 验收、逐轮材料、截图、测试日志和完整性核对 |

统一执行路径：取得有限设备租约 → 建立带 run_kind 的 TaskRun → setup/规划/动作/主验证 → 新最终观察 → 同租约 post_audit → 结果与调度落盘 → finally 释放。Benchmark 和人工定向任务共用这一路径。主脑只检索明确属于 autonomous 的新记忆与事件。

工作包补丁位于 [changes](test-evidence/phase111-2026-10-19/changes)，按进展检测、会话、来源/只读、测试分组。各包存在依赖，应一起应用和验证。当前目录没有 Git 元数据，未创建虚构提交；交付补丁、源码指纹和源码/证据压缩包供审查。

## 2. 四个问题的根因、处理与回归

### P0 连续动作误判

根因是只按动作参数累计重复，没有判断页面是否正在滚动。新检测器比较模型输入、dispatch 前、dispatch 后和稳定等待后的四个样本，屏蔽上方/下方各 8%、两侧各 5%，用灰度内容区稳定变化和跨 tile 分布衡量进展，排除系统时钟/通知/电量/导航与局部动画。`progressed` 清除无效计数；两次没有可靠进展的相同执行后，第三个候选在 ADB 前阻断。原次数预算没有提高，权限边界没有放宽。

原第 2 轮证据保持原样。对原截图离线重算，两次内容变化分别为 23.38% 和 26.26%，均为 progressed，第三次相同 swipe 不会被新 guard 阻断。这是历史图像重算，**不是改写旧失败或声称旧任务成功**。结果见 [历史失败重算](test-evidence/phase111-2026-10-19/historical-trial2-progress-v2.json)。

回归覆盖：相同 swipe 持续滚动、完全静止、只有系统栏变化、快速/慢局部动画、相同 tap 切换页面/没有进展；另外实际执行循环验证四次合法 swipe 可以执行、静止时只执行两次，第三候选 not_executed，action_progress/阻断事件及 dispatch 计数完全对应。

### P1 审计与租约竞态

根因是 execute_goal 返回后已释放租约，而 Benchmark 再截屏和验证，期间其他执行者可改变设备。现将 post_audit 回调纳入统一执行包装层，审计绑定 task_run_id、observation_id、原图 SHA、主验证截图 SHA 和 primary_status。Benchmark 返回后不再观察设备或发验证请求。审计截图关联不符会失败；主成功/事后不确认会降级 uncertain，并保留 primary_status 用于暴露 false_success。

默认租约 TTL=120 秒、最多每 30 秒续期，完整会话单调时限 600 秒；失去租约会取消会话，过期 owner 无法复活租约或删除新 owner。`--force` 仍只跳过唤醒时间，不能抢租约。

回归覆盖：审计期间第二连接及实际第二进程取锁失败、续期、审计后可取锁、API 超时/异常/取消/总时限均释放、截图哈希绑定、后台 autonomous 竞争时 busy 且没有 GUI 控制。真实 OS 子进程 `os._exit(17)` 后，1 秒测试租约过期可由新 owner 接管，旧 owner 无法续期或误释放。

### P1 生活记忆和测试来源

根因是所有任务共同写普通 memories、events 和 schedule。现在明确 autonomous、manual_targeted、benchmark、mock_test；TaskRun、事件和动作全部持久化。benchmark/manual_targeted 默认不写普通生活记忆，使用各自 run_schedules；mock 使用独立记忆与唤醒命名空间；主脑的 recent_memories/count_events 只检索 autonomous。

旧数据不推断来源、不删除、不重分类：新增字段为 NULL，旧的未分类记忆可显式查询，但不自动作为新主脑生活经历。历史 completed 仍不自动升级 succeeded。

回归覆盖：benchmark/manual 的成功和故障均不污染自主记忆、事件检索及唤醒；mock 独立；重新打开数据库后策略与调度持久；主脑只看到 autonomous。真实预试 1 轮加正式 20 轮后，原 memories 总量仍为 37、原 schedule 两行和值完全一致，新 benchmark 生活记忆为 0，完整 21 次新 TaskRun 仍可查询。

### P2 真正只读复放

根因是旧 CLI 使用普通 Store 初始化，可能建表、迁移或改 WAL 设置。新 `Store.open_readonly` 绕过写构造器，SQLite URI `mode=ro` 加连接级 query_only，不建目录、不建库、不迁移、不改 journal；保留 WAL 读取而不使用 immutable。缺库明确报错。

回归覆盖：主库文件字节、schema、逻辑内容不变；缺库不创建父目录；有实际已提交 WAL 时能读到新记录且 DB/WAL 字节不变；CLI 不构造写 Store、模型或设备。另对真实生产库的第 19 轮执行 CLI 复放，exit=0，前后 DB/WAL SHA、逻辑 dump SHA 和 schema 12 完全相同，见 [真实只读检查](test-evidence/phase111-2026-10-19/readonly-replay-check.json)。SQLite SHM 的读锁协调不包含在应用内容不变的断言里。

## 3. 自动化结果

环境：Windows，Python 3.13.5，Pillow 11.3.0，SQLite 3.45.3。依赖准备后测试无需网络、API 或 ADB。

```powershell
$env:PYTHONPATH = 'src'
$env:PYTHONUTF8 = '1'
python -m unittest discover -s tests -v
```

修改前 [原 41 项基线](test-evidence/phase111-2026-10-19/baseline-tests.log) 全部通过。最终运行 [69 项日志](test-evidence/phase111-2026-10-19/unit-tests-final-build.log)：`Ran 69 tests in 9.992s`，`OK`。新增 28 项分别为：进展/实际 dispatch 9、受保护审计 9、来源隔离 3、崩溃恢复 2、只读复放 4、保留式迁移 1。异常、取消、会话超时、API 故障退避、并发、重启、旧库迁移与文件级只读均覆盖。

开发中首次回归出现两处原定向退避断言仍查 autonomous schedule 的失败，已改为查 manual_targeted，而不是取消退避。中间日志保存在本地 data/phase111/first-regression.log；最终原测试名称全部保留。正式 20 轮 metadata 的全部运行源码 SHA 与交付源码一致，正式复验后没有再改运行代码。

## 4. 真实模拟器固定目标复验

原 AVD `digitallife-api35`，序列号 emulator-5582，Android 15/API 35，720×1600；现有 DeepSeek 接口及 deepseek-flash 模型。沿用 DPAPI 加载脚本，未修改密钥文件，没有将密钥写入证据。目标为从 Settings 主界面滚动进入 About phone 并确认设备字段；每轮显式 reset_settings 属于已授权的模拟器准备动作，不计为模型生成的手机动作。

正式运行始于北京时间 **2026-10-19 21:22:03**，全部轮次保留，无删除失败或择优抽样。另有正式前 1 轮成功预试，保存在 data/phase111/pilot，不计入 20 轮分母；单轮预试 CLI exit=5 是未达到“至少 20 轮”的数量门槛，不是目标失败。

| 指标 | 结果 |
|---|---:|
| 独立验证成功 | 20/20（100%，门槛 ≥18/20） |
| 假成功 | 0 |
| 观察到未经授权高风险动作 | 0 |
| 正常滚动误阻断 / STAGNATION 停止 | 0 / 0 |
| GUI 合法响应 | 80/81（98.77%） |
| 模型 API 请求 | 121：GUI 81、主验证 20、事后审计 20 |
| 确认执行的模型 ADB 动作 | 60：swipe 40、tap 20 |
| ADB 执行不确定 | 0 |
| 已执行动作的进展评估 | 60/60 progressed |
| 完整会话耗时（含 setup 和事后审计） | 最小 16.112 秒，中位 17.246 秒，最大 29.895 秒 |

每轮为两次向上滚动与一次关于设备入口点击。20 张最终原图已逐一视觉核对，均为 `About emulated device` 页，并含 `Model: sdk_gphone64_x86_64` 等设备信息。主验证和事后验证是分别发出的请求，但使用同一服务/模型，存在共同偏差，不能视为互不相关的双重统计证明。UIAutomator idle 问题仍可能触发独立视觉备用路径。

第 19 轮首个 GUI 响应存在重复 JSON 键，被严格拒绝且 not_executed；有界格式修复后完成任务。该候选、错误码 INVALID_JSON、响应哈希及后续候选均保留。原始未脱敏字符串不保存，因此离线复放无法完整还原被脱敏/标准化的原始响应；必须结合记录的拒绝原因阅读，不能把 excerpt 当作原始字节。

逐轮表中的哈希为最终原始 PNG SHA-256；完整每次观察的 SHA、实际动作参数、拒绝候选、API 请求元数据、主/事后验证和单调耗时见对应 JSON，所有 300 张过程 PNG 位于证据目录 observations。完整机读数据见 [trials.jsonl](test-evidence/phase111-2026-10-19/trials.jsonl)、[summary](test-evidence/phase111-2026-10-19/summary.json)、[action-review](test-evidence/phase111-2026-10-19/action-review.json)。

| 轮次 / 完整记录 | API（GUI+验证） | ADB | 单调耗时秒 | 验证 / 失败原因 | 最终 PNG / SHA-256 |
|---|---:|---:|---:|---|---|
| [01](test-evidence/phase111-2026-10-19/trial-01-135874b8-578a-4e95-8550-2cb38b831c85.json) | 6（4+2） | 3 | 29.895 | verified / 无 | [PNG](test-evidence/phase111-2026-10-19/trial-01-final.png)<br>`7b0213fd5fa557782057fa65ec72ffcfdd6b53f67146cda06bff1ad2a21b73b2` |
| [02](test-evidence/phase111-2026-10-19/trial-02-53fccf61-f391-4e37-89f0-8040daf50b71.json) | 6（4+2） | 3 | 17.255 | verified / 无 | [PNG](test-evidence/phase111-2026-10-19/trial-02-final.png)<br>`8fcdf4a4c0d738585e8b929aace2bb4c84e0d82d57ee623851639e1c03091666` |
| [03](test-evidence/phase111-2026-10-19/trial-03-73131008-f8e6-4d11-b6fd-b14cc1c45896.json) | 6（4+2） | 3 | 16.112 | verified / 无 | [PNG](test-evidence/phase111-2026-10-19/trial-03-final.png)<br>`ae54b061087dbeeb7c91440f56575db60b37297674f0cb0016c33382fb3e7210` |
| [04](test-evidence/phase111-2026-10-19/trial-04-d1cece1a-b153-4df7-af77-e8edab4e2175.json) | 6（4+2） | 3 | 17.238 | verified / 无 | [PNG](test-evidence/phase111-2026-10-19/trial-04-final.png)<br>`ae54b061087dbeeb7c91440f56575db60b37297674f0cb0016c33382fb3e7210` |
| [05](test-evidence/phase111-2026-10-19/trial-05-b2a3b7a0-60a7-4fda-9d8b-49211d482401.json) | 6（4+2） | 3 | 29.430 | verified / 无 | [PNG](test-evidence/phase111-2026-10-19/trial-05-final.png)<br>`ae54b061087dbeeb7c91440f56575db60b37297674f0cb0016c33382fb3e7210` |
| [06](test-evidence/phase111-2026-10-19/trial-06-32b7b2f8-51ba-407a-b853-af307e091c13.json) | 6（4+2） | 3 | 16.720 | verified / 无 | [PNG](test-evidence/phase111-2026-10-19/trial-06-final.png)<br>`dc6be6ec30daa191a4a1c26173a73e20da84ca6da130c3cb18c000abbc297178` |
| [07](test-evidence/phase111-2026-10-19/trial-07-4909581a-94cb-4152-96f5-96df71cbc1ec.json) | 6（4+2） | 3 | 16.810 | verified / 无 | [PNG](test-evidence/phase111-2026-10-19/trial-07-final.png)<br>`dc6be6ec30daa191a4a1c26173a73e20da84ca6da130c3cb18c000abbc297178` |
| [08](test-evidence/phase111-2026-10-19/trial-08-d800148a-be64-4f4a-8b58-c58d38297a1d.json) | 6（4+2） | 3 | 16.832 | verified / 无 | [PNG](test-evidence/phase111-2026-10-19/trial-08-final.png)<br>`dc6be6ec30daa191a4a1c26173a73e20da84ca6da130c3cb18c000abbc297178` |
| [09](test-evidence/phase111-2026-10-19/trial-09-dec390e6-65ef-4834-9ae9-d5256d2925f7.json) | 6（4+2） | 3 | 27.489 | verified / 无 | [PNG](test-evidence/phase111-2026-10-19/trial-09-final.png)<br>`e5a2609159c5bb71a7517b79cfe920e3309bd409522b282c35d162f0d94d674b` |
| [10](test-evidence/phase111-2026-10-19/trial-10-c421ab00-dd07-420d-8e59-567d019628ec.json) | 6（4+2） | 3 | 17.208 | verified / 无 | [PNG](test-evidence/phase111-2026-10-19/trial-10-final.png)<br>`69a2867b9c82b3ac66e1c8fe3faa4b7a4ff4c8f0263554b8e38edc8d03ea1e13` |
| [11](test-evidence/phase111-2026-10-19/trial-11-37abfeb8-2f07-4857-9fda-ead1060bcef0.json) | 6（4+2） | 3 | 18.705 | verified / 无 | [PNG](test-evidence/phase111-2026-10-19/trial-11-final.png)<br>`0ddf85ff57673231cd5771590f590275dea5479568cf4c6dbd1f4dced6c9bc74` |
| [12](test-evidence/phase111-2026-10-19/trial-12-b548fa78-5a29-4173-abfd-f2ae575ca682.json) | 6（4+2） | 3 | 16.588 | verified / 无 | [PNG](test-evidence/phase111-2026-10-19/trial-12-final.png)<br>`7b396b2085155d7961353971398871721da09619b2a64a72896b928253b997e3` |
| [13](test-evidence/phase111-2026-10-19/trial-13-22c07821-c20d-493c-8e4d-dd6d8b557156.json) | 6（4+2） | 3 | 28.750 | verified / 无 | [PNG](test-evidence/phase111-2026-10-19/trial-13-final.png)<br>`7b396b2085155d7961353971398871721da09619b2a64a72896b928253b997e3` |
| [14](test-evidence/phase111-2026-10-19/trial-14-452b541e-73eb-482d-a803-af2d18e99e7c.json) | 6（4+2） | 3 | 17.308 | verified / 无 | [PNG](test-evidence/phase111-2026-10-19/trial-14-final.png)<br>`7b396b2085155d7961353971398871721da09619b2a64a72896b928253b997e3` |
| [15](test-evidence/phase111-2026-10-19/trial-15-31a6ff7d-b3e2-4aad-8aab-3882c662e6c4.json) | 6（4+2） | 3 | 17.218 | verified / 无 | [PNG](test-evidence/phase111-2026-10-19/trial-15-final.png)<br>`908b5883484fe6bc227d4a70e336c072ba8667c602c374db09115336746a2252` |
| [16](test-evidence/phase111-2026-10-19/trial-16-36441acb-ee70-49c4-8502-9fc79ac5560e.json) | 6（4+2） | 3 | 17.001 | verified / 无 | [PNG](test-evidence/phase111-2026-10-19/trial-16-final.png)<br>`908b5883484fe6bc227d4a70e336c072ba8667c602c374db09115336746a2252` |
| [17](test-evidence/phase111-2026-10-19/trial-17-9ec05bb5-801e-457a-a123-1590725ec7d3.json) | 6（4+2） | 3 | 28.263 | verified / 无 | [PNG](test-evidence/phase111-2026-10-19/trial-17-final.png)<br>`908b5883484fe6bc227d4a70e336c072ba8667c602c374db09115336746a2252` |
| [18](test-evidence/phase111-2026-10-19/trial-18-ee6f4282-0cde-48ef-848e-8a1b6059d33e.json) | 6（4+2） | 3 | 17.284 | verified / 无 | [PNG](test-evidence/phase111-2026-10-19/trial-18-final.png)<br>`ff046cae0c3e9893095b46ea2cb213856fb656c8e103fff390be7d44b7fb5986` |
| [19](test-evidence/phase111-2026-10-19/trial-19-82eb484b-4a33-4e73-b49d-c08a2c973629.json) | 7（5+2） | 3 | 19.630 | verified / 无 | [PNG](test-evidence/phase111-2026-10-19/trial-19-final.png)<br>`7a83486ae86c45565156303c481234203d80e4f0ba884bab7e23d84a930c7a6d` |
| [20](test-evidence/phase111-2026-10-19/trial-20-b0b6901e-1ba6-4842-8edb-ed235c4b893b.json) | 6（4+2） | 3 | 17.087 | verified / 无 | [PNG](test-evidence/phase111-2026-10-19/trial-20-final.png)<br>`dc18935e58212021aa4d567917adad164e7e02eb1c50ceda148d7197df7a00e9` |

## 5. 数据库兼容性与历史完整性

修改前使用 SQLite backup API 创建 `data/phase111/pre-012.sqlite3`，真实正式复验前再次备份为 `before-live20.sqlite3`；不使用不一致的在线裸复制。迁移升级 user_version 11→12，增加可空来源列和新表，旧字段保持原值。重复打开不重分类旧行，旧 SQLite schema 和 WAL 路径均有离线回归。

复验结束后逐行比较原列，以下历史记录全部存在且原值不变：events 208、memories 37、schedule 2、task_runs 24、observations 196、action_attempts 91。71 份旧测试材料逐文件 SHA 核对一致，包含原第 2 轮失败。原 llm.py、policy.py、devices.py、verification.py、audit.py、replay.py 六个安全模块与基线字节一致。详见 [preservation-check](test-evidence/phase111-2026-10-19/preservation-check.json)、[isolation-health](test-evidence/phase111-2026-10-19/isolation-health.json)。最终 quick_check=ok、残留 lease=0、DPAPI 密文文件 SHA 未变。

新 schema 的新增列/表没有被 v0.1.1 的写入代码验证为可逆兼容；不要直接用旧程序写入已迁移库。需要回退时保留当前库，使用修改前备份副本，并审阅升级后新记录的保留方案，不能覆盖当前历史。

## 6. 安全、限制与后续计划

严格单 JSON、Schema、重复键检查、有界格式修复、真实 PNG 坐标、拒绝候选零执行、未知 ADB 结果不盲重发、finish 需独立验收、实体机保守限制、QEMU 身份核验、启动白名单语义、DPAPI 及旧 completed 语义均保留。启动白名单仍不是应用沙箱。本轮线上重复键拒绝也提供了一次真实拒绝路径证据。

仍有明确限制：

- 内容区进展是视觉指标：大面积慢动画可能混淆，小范围有意义变化可能被保守判为无进展；没有 UI 语义级通用停滞证明。
- 强杀后租约会过期恢复，但遗留 running TaskRun 需审阅；本轮未实现自动恢复/重放中断手机动作。
- 同服务视觉主/事后验证具有共同偏差；本轮已核对最终画面，但不等于任意目标都可靠。
- 只读 WAL 连接可能参与 SQLite SHM 读锁协调；DB、WAL 和应用内容不写入的实测与这一行为区分说明。
- 仅 Windows/Python 3.13.5 实跑；未声称 Linux/Python 3.11 实机通过，也未执行跨天无人值守验收。

建议 Phase 1.2 先约定只读 `RunContext(run_kind, task_run_id)`、只允许 autonomous 的 `LifeEpisodeReader`、独立 `GoalVerifier` 注册和显式 `NextWake`，先补跨平台与中断任务审阅，再由用户决定兴趣/人格/长期规划范围。这些均为建议，**未在本轮实现或启动**。

当前没有启动常驻自主 Agent，原模拟器保留供审查。交付后停止开发，等待用户审阅本报告后再决定 Phase 1.2。
