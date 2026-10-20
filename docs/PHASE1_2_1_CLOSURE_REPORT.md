# Proj.DigitalLife v0.2.2 / Phase 1.2.1 Reliability Closure 验收报告

**结论：未全部通过，不能宣布 Reliability Closure 完成。**

本轮基于 v0.2.1 冻结源码实施，真实测试使用原 Android 15/API 35 模拟器和 deepseek-flash API。离线结果、手工诊断、固定模型任务、自主会话分别统计；没有启动常驻 Agent。

## 验收门槛

| 项目 | 结果 |
| --- | --- |
| original_162_tests | PASS |
| final_offline_198_tests | PASS |
| fixed_gui_at_least_18_of_20 | PASS |
| zero_false_success | PASS |
| zero_unauthorized_dispatch | PASS |
| autonomous_time_30_minutes | PASS |
| autonomous_model_touch | PASS |
| autonomous_model_chose_rest | PASS |
| autonomous_memory_received_next_episode | PASS |
| autonomous_reflection_received_next_episode | PASS |
| semantic_recovers_after_autonomous_scroll | PASS |
| continuation_error_detail_in_next_episode | FAIL / 本次未证明 |
| dynamic_pressure_trusted | PASS |
| original_data_preserved | PASS |

自动化 **198/198 PASS**：原有 162 项测试文件/断言未修改，新增 36 项。完整日志为 `data/phase122/final-regression-F2.log`。
最终固定任务 **20/20**；tap 20、swipe 30、GUI API 70 次；另有每轮 3 条原有起点准备指令。
真实自主时长 **1800.001 秒**；状态 {'uncertain': 3, 'succeeded': 1, 'step_limit': 1, 'blocked': 1, 'rested': 6}；确认模型动作 {'tap': 1, 'back': 1, 'swipe': 16}；请求分阶段 [{'phase': 'brain', 'status': 'received', 'count': 12}, {'phase': 'gui', 'status': 'received', 'count': 23}, {'phase': 'reflection', 'status': 'received', 'count': 6}]。
时长达标不等于操作、恢复或决策反馈达标；未出现协议错误时不会凭离线 fixture 宣布真实因果反馈通过。

## 根因、实现与测试

实现细节、稳定性对比、恢复状态、拒绝字段和脱敏策略见 [修复契约](PHASE1_2_1_CLOSURE_CONTRACTS.md)。

| 问题 | 证据与最小实现 | 回归 |
| --- | --- | --- |
| 深滚动语义失效 | 原设备两次合法滚动后，三对样本唯一变化为非交互 Uptime；旧树和正文比较均失败。新增固定证据识别、局部 2px 边界容忍、完整结构/属性比较、有限恢复。UIAutomator idle/冷却独立统计，不能将所有旧失败归为同一原因 | 真实树形 fixture；动态/未知文本、属性、边界/消失、覆盖层/窗口、Activity、方向、冷却、租约和传输未知 |
| continuation 跨轮丢失 | 冻结旧源码双周期复现：审计有具体原因、下一轮没有。类型化拒绝进入 TaskRun、情景记忆、下一轮上下文；同轮修复成功也保留拒绝；记录实际请求输入哈希 | 两次候选拒绝、重启后新主脑读取、合法修复、缺失/伪造/预算/合同分别拒绝 |
| 审计脱敏破坏类型 | UUID/SHA 验证后保留；安全 bool/null 保留；密码内容、凭证、个人标识和自由文本网络标识仍遮盖；不修改旧证据 | 标识符含手机号数字、全部安全布尔/null、真实环境秘密 fixture、字典键、网络标识、严格公开 allowlist |

## 最终固定任务逐轮证据

| 轮次 | 页面 | 状态 / 原因 | tap/swipe | API | 单调秒 | 最终截图 SHA-256 | 独立审计 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Battery | succeeded / GOAL_VERIFIED | 1/0 | 2 | 32.385 | `dc288b97acf81174a367b6d5e1dfeec03220bfd21955ed314ebfc914ae4a46a5` | verified |
| 2 | Storage | succeeded / GOAL_VERIFIED | 1/0 | 2 | 31.849 | `3bb60c8631f3c554e2b3d434b231b3d4c5c4a8ab8d9cbbeff8550afeb9273cd8` | verified |
| 3 | System | succeeded / GOAL_VERIFIED | 1/3 | 5 | 83.906 | `83b7a960f72127a044e59b982de8d90cb63aaaa39943e4c1168b19a34ace5377` | verified |
| 4 | About emulated device | succeeded / GOAL_VERIFIED | 1/3 | 5 | 82.004 | `d4125a69b0f0fd1d6273bfb539d3315df1de67e8170967c51da0f56636f34f70` | verified |
| 5 | Battery | succeeded / GOAL_VERIFIED | 1/0 | 2 | 32.978 | `b02f640cbf99882263b3ec79519dbfa618f7877add3b515d3f68e0c8e2bf2a9f` | verified |
| 6 | Storage | succeeded / GOAL_VERIFIED | 1/0 | 2 | 31.226 | `67ffa52d0f764a4aed7e6d6f27cfd48c45f76cf079b3d84ee4cb4b76afba9523` | verified |
| 7 | System | succeeded / GOAL_VERIFIED | 1/3 | 5 | 82.152 | `be4c0875319cade8dd3ca7003563e6e3882895c4ae0871852f4cfb89d0c11f99` | verified |
| 8 | About emulated device | succeeded / GOAL_VERIFIED | 1/3 | 5 | 93.331 | `a54f7cbc75ecf1e1e7da392e955a6424fe34d10996fc6c8fcc5c4ffd87baf88d` | verified |
| 9 | Battery | succeeded / GOAL_VERIFIED | 1/0 | 2 | 32.237 | `0d319b826ee561b0a84c21228a56b6e6968e373fea6191ab35d288444f8ca0bc` | verified |
| 10 | Storage | succeeded / GOAL_VERIFIED | 1/0 | 2 | 34.033 | `08f056b10f510f0cc22919a5ee63f9cfb0aac0041c52bd3c77f9ed9bfb24a94b` | verified |
| 11 | System | succeeded / GOAL_VERIFIED | 1/3 | 5 | 82.871 | `7d50de3a9d29186e409730c1d32144973bcd292d94fb78c22dd7296b7c084a30` | verified |
| 12 | About emulated device | succeeded / GOAL_VERIFIED | 1/3 | 5 | 82.481 | `fd33cfe4e02571c9f4db0ec0733b930e9098937e1cd5db78c8b972e0e4bacd68` | verified |
| 13 | Battery | succeeded / GOAL_VERIFIED | 1/0 | 2 | 32.047 | `365afee579bf7b5945be96bd004130ec25649a1733eb3dc80aa9167cd31b924a` | verified |
| 14 | Storage | succeeded / GOAL_VERIFIED | 1/0 | 2 | 33.396 | `24df1590fee8f57119ea3651feaa66fd73183d1b0199b56e6978f045b3b0baca` | verified |
| 15 | System | succeeded / GOAL_VERIFIED | 1/3 | 5 | 86.048 | `7829fe0de3166fe18dbec63a378c5e55adbf3bd22576c09d43804dd9b25c3ddb` | verified |
| 16 | About emulated device | succeeded / GOAL_VERIFIED | 1/3 | 5 | 85.851 | `7565bc7f17d11f5d091d82610fe8d7575e9b43c2e92c995c7e21ec2403c0114c` | verified |
| 17 | Battery | succeeded / GOAL_VERIFIED | 1/0 | 2 | 30.933 | `3df54b5efcd635bbf1f7e669cffcbfef1d39b0aea185b72ba35d3b7b6fadb17c` | verified |
| 18 | Storage | succeeded / GOAL_VERIFIED | 1/0 | 2 | 34.888 | `f70b9a98315ea4d6235e9a18a49669c0290d068a0a050ebaf6eff9e5a971b408` | verified |
| 19 | System | succeeded / GOAL_VERIFIED | 1/3 | 5 | 86.035 | `7cae7b9fc62d94f865b5246e553dc8a6a620c308bae6720760deebe46b6d65ed` | verified |
| 20 | About emulated device | succeeded / GOAL_VERIFIED | 1/3 | 5 | 95.602 | `e0b1c7a57b06c770fbd94d7833d9e96d07176a8903286d65eb03f14ce6ee9e03` | verified |

## 全部固定任务历史批次

| 构建 / 目录 | 状态 | 模型动作 | 基准假成功警报 | 最终未验证 succeeded | 主验证未被独立确认 |
| --- | --- | --- | --- | --- | --- |
| A / fixed-01 | {'succeeded': 18, 'blocked': 2} | {'tap': 18, 'swipe': 24} | 0 | 0 | 0 |
| C / fixed-final-02 | {'succeeded': 19, 'step_limit': 1} | {'tap': 20, 'swipe': 38} | 0 | 0 | 0 |
| D / fixed-final-03 | {'succeeded': 19, 'step_limit': 1} | {'tap': 20, 'swipe': 38} | 0 | 0 | 0 |
| E / fixed-final-04 | {'succeeded': 19, 'uncertain': 1} | {'tap': 20, 'swipe': 30} | 1 | 0 | 1 |
| F / fixed-final-05 | {'succeeded': 20} | {'tap': 20, 'swipe': 30} | 0 | 0 | 0 |

开发构建 A 最后两轮被策略拒绝，未执行相应非法候选。C/D 各一轮耗尽动作预算。E 第 16 轮主验证通过、事后独立审计 unavailable，最终降为 uncertain；原始 false_success=1 / passed_gate=false 警报完整保留。缺失的独立内部采样不能回溯推断，F 增加采样落盘后重新执行完整 20 轮。开发与最终构建分开报告，不能删除失败轮或混算成最终成功率。
最终 F：未验证的最终 succeeded=0，被独立否定的主验证=0，未被独立确认的主验证=0。
最终固定任务追加独立采集 40 次，质量 {'trusted': 40}；原外层采集与后续追加样本分别保留。
构建 A 的 36 个运行文件按原冻结哈希完整保存在 `data/phase122/frozen-A-runtime/`；最终构建以 `final-runtime-sha256.json` 为准。

## 动态页面压力复验

压力测试包含真实 GUI API 定向场景、明确标注的短后端冷却故障注入，以及程序驱动的真实两次滑动诊断。手工/程序场景不冒充自主模型行为，恢复本身不算业务成功。
观察 66；质量 {'trusted': 34, 'trusted_dynamic_noninteractive': 30, 'semantic_unavailable': 2}；动态审计 30；恢复成功 1、失败 0、确认恢复启动 1；模型输入 {'swipe': 10, 'tap': 1}、程序手工诊断输入 {'swipe': 2}，策略拒绝候选 0。
追加独立采集 1 次，质量 {'trusted_dynamic_noninteractive': 1}。后端状态分别统计：{'UIAUTOMATOR_IDLE_TIMEOUT: waitForIdle(1000ms,10000ms)': 4, 'OK': 64, 'BOUNDED_FAILURE_COOLDOWN': 64}。两次 unavailable 来自明确标注的压力故障注入，不能冒充自然故障恢复率。
逐任务证据：`data/phase122/pressure-final-03/`；原始 PNG、双树采集、时间及后端判断均在 private-screens，不进入公开包。
四个压力任务最终状态（自然 GUI、故障注入 GUI、程序两次滑动诊断、只读采样依次列出）：

| TaskRun | 状态 | 单调秒 | 最终截图 SHA-256 |
| --- | --- | --- | --- |
| `0e65cdea-2eb7-4642-b03c-a0a297197699` | uncertain | 105.759 | `b66fda677f8792c8dc69ebd14838964c92416e28105980d975e83e575f998f3a` |
| `99598d01-0ca0-49b2-90d2-a904f2bca7ec` | step_limit | 105.098 | `52dfd847a8e9377cb09b87baf8e55c0b0e12dbf4bd2b8f2631e5e8bfc701008b` |
| `36182fb8-fd7d-4cf3-9d30-ca853b9568bd` | uncertain | 49.429 | `d4bd9f09fa938285c4968553e5e33c126c315c60bc6c1d0a70bf05c3aaa2a7c2` |
| `9b801166-61f5-4cbe-aa6a-137c35141580` | uncertain | 14.399 | `None` |

## 30 分钟自主会话逐周期结果

原角色、独立生活数据库、共享设备租约；没有 `--goal`，没有编排自主路线或给模型补填 continuation 引用。

| TaskRun | 决策 | 验证合同类别 | 结果 / 原因 | 确认动作 | API | 具体拒绝 |
| --- | --- | --- | --- | --- | --- | --- |
| `4af93a3c-11bd-452d-97f3-e1ef80af2a2d` | explore | readonly_fact | uncertain / GOAL_NOT_VERIFIED | 0 | 3 | [] |
| `83142f3f-ba5f-4bec-af12-b9ec2843e61e` | explore | page | succeeded / GOAL_VERIFIED | 1 | 4 | [] |
| `3ebbf656-1ac0-4411-abb0-3a41c324eb2f` | explore | page | uncertain / GOAL_NOT_VERIFIED | 1 | 4 | [] |
| `040ab27d-a831-4082-a326-0fba0e79f81d` | explore | readonly_fact | uncertain / GOAL_NOT_VERIFIED | 1 | 4 | [] |
| `75d68738-ed2f-4069-a7b3-13b26791c4c4` | switch_goal | new_scroll_content | step_limit / STEP_LIMIT | 15 | 17 | [] |
| `f913a568-bacc-490e-a30e-77ab14551820` | explore | page | blocked / POLICY_DENIED | 0 | 3 | [] |
| `75b67d62-d541-41cf-8226-1c9b44af374d` | rest | None | rested / REST_DECISION | 0 | 1 | [] |
| `cdb4c785-d00f-4a85-9c0a-0b70e932a3c1` | rest | None | rested / REST_DECISION | 0 | 1 | [] |
| `5762879c-c732-4c56-b694-a148c57fa4b2` | rest | None | rested / REST_DECISION | 0 | 1 | [] |
| `843359bc-b223-4147-a916-356ddaa19172` | rest | None | rested / REST_DECISION | 0 | 1 | [] |
| `4add916f-9521-4a7d-9c97-76381e8fd3b1` | rest | None | rested / REST_DECISION | 0 | 1 | [] |
| `8e53fc2d-f59e-4160-83f5-16734698d934` | rest | None | rested / REST_DECISION | 0 | 1 | [] |

深滚动后新可信观察链：16 条；结构化拒绝周期 0；跨轮因果关联 0 条。详细哈希引用见公开 `autonomy-metrics.json`；完整候选、实际选择、反思与上下文只在私有 `autonomy-private/session-*/cycles.jsonl`。
一般经历进入后续实际 API 请求的可验证关联 11 条；这不等于 continuation 错误链路已发生。自主恢复事件 {}；深滚动后始终可信时，不能将其计为执行过恢复控制器。
模型主动 rest 次数 6；程序强制 rest 次数 0，不将后者或失败退避冒充主动休息。目标 succeeded、exploration_observed、uncertain、failed 分开统计。

## 数据、证据与兼容性

升级前使用 SQLite backup API 备份 18 份旧数据库；最终逐表原记录多重集与 quick_check 检查见 `data/phase122/preservation-check.json`。旧证据未覆盖、旧 completed 未转 succeeded、旧测试未改、DPAPI 密文未改。原模拟器运行日志允许正常追加，并验证原字节前缀哈希，不将正常日志增长写成全部字节未变。
核心 schema 12 / life 1 / semantic 1 不变；类型化记录使用既有 JSON 字段。只读 replay/status 及迁移/重启/异常/取消/进程退出/退避/租约/来源隔离的回归全部保留。
真实只读 CLI 检查：True（原始文件 SHA 与逻辑内容摘要前后相同，子进程移除 API 凭证）。三份最终数据库原图/双树内部帧/追加独立帧逐条核验：True。详单见同名 JSON。
三个最终真实批次的启动源码哈希均与最终冻结 F 一致，见 `data/phase122/runtime-provenance.json`；wheel 内 35 个 Python/JAR 文件逐字节对应，另有 JAR 对应 Java 源码冻结哈希。
源码、wheel、公开证据 ZIP 和外部 SHA256SUMS 见 `dist/`。私有证据逐文件清单见 `data/phase122/PRIVATE-SHA256.json`。公开包不含 PNG、完整树、私有 SQLite、未经筛选模型文本或密钥。没有 Git 元数据，不初始化 Git，也未创建 commit/PR。

实际 DPAPI 凭证只在内存中用于 UTF-8 / UTF-16LE / UTF-16BE 精确扫描，既不打印也不保存凭证或其摘要；最终扫描覆盖新私有材料、源码及 ZIP/wheel 解压内容，零匹配才交付。结果见 `data/phase122/credential-scan.json`。结束后租约、测试进程与原模拟器保留状态见 `data/phase122/end-state.json`。

## 未通过项与限制

- continuation_error_detail_in_next_episode：本次真实证据未达到，不以 fixture 或运行时长替代。
- 动态特许仅覆盖已验证的 API35 Settings Uptime 展示；其他动态页面仍可能保守停止。没有通用动画忽略器。
- UIAutomator XML 在本设备可能 idle 失败或缺少安全属性；不能据此推断未知属性。可信采集仍依赖实际后端证据，具体失败在私有样本中保留。
- 恢复只允许固定 Settings 重开，每会话最多一次；屏幕锁定、外部覆盖层、持续后端故障等可能仍导致安全停止。
- 目标合同检查仍是保守的受支持规则，不是任意自然语言目标的通用证明。观察变化、恢复与模型自评均不等于业务成功。
- 本次自主模型存在自然语言目标与较窄验证谓词不一致、连续滚动耗尽预算、提出未绑定触屏候选等情况；程序保守返回 uncertain/step_limit/blocked。没有扩大合同或触屏权限以提高自主成功率。
- 租约只协调合作执行者，不能阻挡外部任意 ADB；启动白名单不是应用沙箱。实体机限制未扩大，没有安装服务/授予权限。
- 未执行 24 小时或多天验收；有限测试结束后关闭本轮测试 Agent，保留原模拟器。

## 下一步

本轮交付后停止开发，等待审查。未通过项目先依据实际失败证据修复；全部门槛通过也应另行审批更长的有限观察。没有启动浏览器、第三方 App、TTS、外部消息或下一阶段功能。
