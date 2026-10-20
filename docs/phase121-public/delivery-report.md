# Proj.DigitalLife v0.2.1 / Phase 1.2.1 验收报告

**结论：部分门槛未达到，不能宣布 Phase 1.2.1 全部验收通过。**
代码已实施；以下数字来自最终冻结版本、真实 API 和原 Android 15 模拟器。24 小时无人值守验收未执行，也没有启动长期后台 Agent。

## 验收结果

- 自动化：162/162 PASS；其中原有 123 项测试方法保留，新增 39 项。离线自动化不调用 LLM/ADB。
- 最终固定任务：20/20，成功率 100%；真实 deepseek-flash GUI API 70 次；tap 20、swipe 30。
- 最终批次观察到的假成功 0；未经授权的已分发动作 0；不确定传输 0，无盲目重发。
- 自主会话：1800.001 秒，3 个正式周期；实际确认动作 {'swipe': 2}，其中有进展证据的 GUI 动作 1；API 请求分阶段 {'brain': 5, 'gui': 5, 'reflection': 1}。
- 自主任务结果单独统计：{'failed': 3}。有证据的安全滚动不等于整个自主目标成功；本次短测未证明稳定的自主任务完成能力。
- 自主会话没有 --goal，使用原角色、真实主脑/GUI API、独立生活数据库和持久化唤醒；模型可以休息，没有强制安排探索路线。
- 固定任务与自主行为分别计数，每轮另有 3 条起点准备 ADB 指令（停止 Settings、唤醒、启动设置），计为每批 60 条 setup 指令，不混算模型 GUI 动作。真实模型测试与离线 fixture 明确区分。

| 门槛 | 结果 |
| --- | --- |
| automated_tests | PASS |
| final_fixed_gui | PASS |
| autonomous_30_minutes | PASS |
| autonomous_confirmed_gui | PASS |
| semantic_recovers_after_autonomous_scroll | 未通过 |
| continuation_error_detail_in_next_episode | 未通过 |
| real_memory_feedback | PASS |
| no_observed_false_success | PASS |
| no_unauthorized_dispatch | PASS |
| historical_integrity | PASS |
| key_unchanged | PASS |
| runtime_frozen | PASS |
| raw_safety_attributes_verifiable | PASS |

## 根因、实现与测试

| 问题 | 根因与实现 | 验证 |
| --- | --- | --- |
| M1 语义采集 | Android dump 在 waitForIdle(1000,10000) 失败；新增有界只读 shell UIAutomation 后端、独立超时、两树/画面/Activity/方向复核；未知属性为 null | idle/超时/切换/旋转/Activity/树变动/覆盖层回归；原模拟器真实采集 |
| M2 控件绑定 | 旧坐标无当前控件引用；增加四种严格语义意图、程序坐标、完整属性绑定、dispatch 前检查和一次引用修复；旧坐标仍过原政策 | 伪造/过时/移动/消失/状态控件、像素和时长、传输未知、租约审计回归 |
| 滚动反馈 | 开发批次中模型把 down 理解为“向下看”，实际手指向下拉回顶部；正文未变时被正确阻止 | 改为 toward_end/toward_start、明确手指方向；每步给实际进展反馈；像素和可见语义双信号；没有提高重复阈值 |
| M3 能力 | 旧硬编码路由扩展为 capabilities/1，程序配置来源/风险/证据/包/控件/设备/预算 | 仅 Settings 信息行和安全列表；未知/敏感/状态修改硬拒绝，原 ActionPolicy 保留 |
| M4 主脑和 continuation（部分完成） | 增加语义环境、同来源页面、GUI 失败明细、持久引用/累计预算及当轮两次结构化修复；主脑拒绝审计完整，但跨轮情景记忆仍仅保留泛化错误 | 离线引用/预算/隔离回归通过；真实模型四次缺引用，跨轮反馈缺口仍待修 |
| M5 验证范围 | 仅验证一个谓词不能证明更宽泛目标；新增精确语义 header/control、新滚动内容、自然语言范围保守检查 | 列表行不算页面；Android version 不证明 security check；观察仅 exploration_observed |

一次原有因果 fixture 的自由描述改为准确的 “Read Android version”，原兴趣影响选题、来源、结果及反思断言均保留；没有删除原测试或让生产范围检查接受更宽目标。

## 固定任务逐轮结果

最终批次每轮从 Settings 主界面出发，动作由模型提议；程序绑定控件后执行，独立验证和事后审计都在同一有限租约内。最终观察与审计观察分别记录，可能具有相同画面哈希但不是复用旧截图。

| 轮次 | 页面 | 状态/原因 | tap/swipe | API | 单调秒 | 最终截图 SHA-256 | 审计 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Battery | succeeded/GOAL_VERIFIED | 1/0 | 2 | 24.796 | 89017c1ee76da985cd41ef606d441c307ca125c8263babe8bcff265edc3de492 | verified |
| 2 | Storage | succeeded/GOAL_VERIFIED | 1/0 | 2 | 23.041 | f04a4be49237494611617219d53caf7c00a6f4177793e2fecc53acd365557d17 | verified |
| 3 | System | succeeded/GOAL_VERIFIED | 1/3 | 5 | 58.855 | fd5e4e387c100ffafa3d662afbdb29de1dfbb7d07f0e895d8188edf6e3305312 | verified |
| 4 | About emulated device | succeeded/GOAL_VERIFIED | 1/3 | 5 | 58.496 | 22e077ac7b0835b7f8a31f653c8ecfb9749f809ce9062df32fe73d79c644dc33 | verified |
| 5 | Battery | succeeded/GOAL_VERIFIED | 1/0 | 2 | 22.039 | 9c8cc7830edd2389e1af46192e642ade4875716cb386b68e972367f98c11fb85 | verified |
| 6 | Storage | succeeded/GOAL_VERIFIED | 1/0 | 2 | 24.236 | d09716752f42e84e470cd61355eb32c4376939507e94b4a6d9a7794ab8241f21 | verified |
| 7 | System | succeeded/GOAL_VERIFIED | 1/3 | 5 | 58.942 | 90023e0dde4ba90558d75a5a8334ebabb67ed548d81e89b7a46727e1c6c9fb1d | verified |
| 8 | About emulated device | succeeded/GOAL_VERIFIED | 1/3 | 5 | 68.729 | c93c70486bfd40aaf049f50f42449341a455ce5ff101b787d5da72d56dd5111d | verified |
| 9 | Battery | succeeded/GOAL_VERIFIED | 1/0 | 2 | 22.592 | 8ee4c415f3d99a23c28ecf8a6459206a0004cdca280da0248ed5113c8914fe57 | verified |
| 10 | Storage | succeeded/GOAL_VERIFIED | 1/0 | 2 | 23.724 | 5c08eeb93cbefcb1c9fde0c7c71bc4cc11cc6c3050ea40167c9fb250f132cf14 | verified |
| 11 | System | succeeded/GOAL_VERIFIED | 1/3 | 5 | 58.436 | 04fc784a1e61f900ecd232622326db635e3b6b3df23977bf6c7088556f7aa7c6 | verified |
| 12 | About emulated device | succeeded/GOAL_VERIFIED | 1/3 | 5 | 61.384 | 80cc02145d65ef450b9d7110db59e4f9fc8079194444f6115c1f8efe9a5c7328 | verified |
| 13 | Battery | succeeded/GOAL_VERIFIED | 1/0 | 2 | 25.166 | d1511b8d5d03c59995d672b6f5319ef0d4ef959db9a1a5a7a41e85f1aa1a853b | verified |
| 14 | Storage | succeeded/GOAL_VERIFIED | 1/0 | 2 | 22.612 | b5103cd2a68ab991f799f29095aeb076e8ed3f5f8c2ec8d27c5b2a0a34323014 | verified |
| 15 | System | succeeded/GOAL_VERIFIED | 1/3 | 5 | 58.096 | efdf1a1b5a0e7a95b440b6dfe22ec357675dc33ecb1024b1404a2390e257b677 | verified |
| 16 | About emulated device | succeeded/GOAL_VERIFIED | 1/3 | 5 | 58.059 | ffa3dc1678327391c78b6e278c8e004c0b983be1c4e0e9fa0d9e6ae0dfa809d3 | verified |
| 17 | Battery | succeeded/GOAL_VERIFIED | 1/0 | 2 | 23.060 | 1de5badcbc4653c1eaf5b2f15e0db2671dcf0f9ff6cc7436827f170d9383e0f7 | verified |
| 18 | Storage | succeeded/GOAL_VERIFIED | 1/0 | 2 | 25.570 | 453d5f551ea03fe68e7e2982e9ef31771b4f434b0099c3da5c5d7fc4de8ada12 | verified |
| 19 | System | succeeded/GOAL_VERIFIED | 1/3 | 5 | 56.185 | f68d8e6b7fc1079f78f76df9169b938fada5383ab700df8c1f0838d99e1b30db | verified |
| 20 | About emulated device | succeeded/GOAL_VERIFIED | 1/3 | 5 | 59.331 | f2b01292a8b9c48b8eec57b42e413937ba04cf71179fb4e241fc7461d950bd38 | verified |

三个批次均完整保留：首批 11/20（改进前方向接口），第二批 20/20（补格式类型边界前），最终冻结批次 20/20。各批次分别绑定源码哈希；全部 60 轮合计 51/60，仅用于完整历史，不能声称累计成功率达到最终门槛。还保留首次 Battery 预试验。失败没有删除、排除后重算或重写。

## 真实自主会话

| 周期 | 选择 | 目标 | 结果 | 实际动作 | 下一次唤醒（Unix） |
| --- | --- | --- | --- | --- | --- |
| 1 | explore | goal SHA a8c43171c6721ba9 | failed | {'swipe': 2} | 1792427083.9002595 |
| 2 | unavailable | goal SHA e3b0c44298fc1c14 | failed | {} | 1792427703.37515 |
| 3 | unavailable | goal SHA e3b0c44298fc1c14 | failed | {} | 1792428322.618133 |

模型的完整候选、原局部 ID 与持久 ID 的绑定、理由、引用经历、反思、兴趣权重变化和请求账本保存在内部 cycles.jsonl / 自主 SQLite 中。报告表格仅列已配置公共规则范围或目标哈希。
真实记忆影响可追溯：首轮反思建议在 GUI 格式失败后改用公共只读事实；第二轮主脑的两次原始提案均明确引用这一调整，改选电量事实。该轮仍输出 kind=continue 且 continuation_of=null，两次结构化修复未成功，因此被拒绝、没有执行，也没有偷偷转换为新任务。这里证明的是实际模型候选受经历影响，不是第二轮目标已完成或反思内容已变成已验证事实。
不能把离线因果测试直接冒充真实模型的因果证明。

## 语义可用性边界

以下是持久化执行器观察，不包含独立验证器额外的有限采集。深滚动后的自主周期出现持续不可用，不能仅凭固定页导航通过就宣布语义观察完全稳定。不可用期间没有猜测性触屏；具体后端切换/失败原因保存在 semantic_observations.payload.attempts。

| 自主周期 TaskRun | 观察数 | 可信数 |
| --- | --- | --- |
| 0d012adc-d18b-40f0-b0fb-1f6c2da13ceb | 10 | 7 |
| 155f0ef0-c5f2-4f61-891d-31240c205e41 | 2 | 0 |
| 044118e6-cad0-48c1-9c0d-ff90117d66c8 | 2 | 0 |

## 数据库、证据和源码完整性

- 原有 506 个 docs 文件 SHA 不变；历史 core/life SQLite 原始记录全部保留，quick_check=ok。主协调库允许正常租约写入，不能用 DB 文件字节不变替代逻辑保留检查。
- 修改前的两个 SQLite backup、基线源码包、123 项测试日志和历史清单均在 data/phase121/；没有清空 DB、重建 AVD 或重分类旧记录。
- core schema 12、life feature schema 1 保持；semantic feature schema 1 只新增表/索引并关联 TaskRun/Observation。迁移/重开/只读导出在离线测试实际检查数据保留及 DB 内容。
- DPAPI 文件字节不变；读取沿用现有脚本。没有在新报告、日志或证据保存 API 密钥。
- 最终固定批次和自主会话的所有运行时 Python 哈希均与最终冻结清单一致；JAR 另有 SHA，原始截图和 JSON 各自列入私有清单。
- 通用脱敏会遮盖 JSON 中名为 password 的布尔安全属性；交付复核通过原观察 UUID、控件 ID 和 SHA 关联的私有原 UI JSON 读取该布尔值，没有把遮盖值当成 False。
- 本目录无 Git 元数据，没有生成提交或 PR。交付包含作用域内 diff、源码 ZIP、公开证据 ZIP 和 SHA 清单。

## 文件和运行文档

组件、后端、能力、绑定、迁移、运维命令及后续接口见 [PHASE1_2_1_SEMANTICS.md](PHASE1_2_1_SEMANTICS.md)。全部修改文件和逐文件 SHA 见 data/phase121/changed-files.json；文本改动见 source-changes.patch。README、包版本和 __version__ 已更新至 0.2.1。

内部材料（含原始截图/控件树，不能直接公开）：

- data/phase121/fixed-final-03/trials/：最终 20 轮逐次 TaskRun、审计、双时钟和 API/动作计数。前两个批次另行完整保留。
- data/phase121/fixed-final-03/private-screens/：原 PNG 与完整语义 JSON。
- data/phase121/autonomy-private/session-c7eed750-c2eb-4ace-955f-8dbdcd39ed51/：无指定目标的真实自主周期、截图、元数据和 summary。
- data/phase121/final-tests-162.log、preservation-check.json、final-runtime-sha256.json、PRIVATE-SHA256.json。

公开材料：docs/phase121-public/ 与 dist/Proj.DigitalLife-v0.2.1-public-evidence.zip；不含原图/树/私有 DB。源码：dist/Proj.DigitalLife-v0.2.1-source.zip。

## 明确限制与下一步

- 仅原 Android 15 隔离模拟器验证；不是跨 ROM、跨语言或实体机验收。UiAutomation 使用平台接口，未来 Android 升级可能失效；返回 unavailable，不退回猜测触屏。
- 截图/树一致性采用保守正文比较；动态页面可能阻断。租约只协调遵守约定的执行者，不能阻止外部直接 ADB/人手输入，检查与 dispatch 之间也不是系统级原子操作。
- 自主深滚动后，后续周期无法恢复可信语义采集，触屏恢复路径受限。需继续 Phase 1.2.1，研究经过明确证据约束的非交互动态区域处理/安全恢复；没有把未知控件或整页差异默认视为安全。
- 只读服务 C 未安装/授权。当前仅 Settings 已知信息导航；不支持任意第三方 App、账号、输入、设置修改、支付或消息。
- 自主实测仍出现重复 JSON 键和 continuation 缺少引用。程序有界拒绝并退避；结构化反馈不能保证模型服从。不能把固定 GUI 通过率当成自主目标完成率，这些拒绝均未伪装成成功。
- 主脑引用拒绝明细保存在 life_decision_rejected 审计和当轮修复反馈中，但最终抛出的错误/下一轮情景记忆仍泛化为 MODEL_OUTPUT_INVALID。这是跨轮错误反馈的实现缺口，可能使同类错误持续；需将结构化决策拒绝原因送入后续 LifeContext，并补真实复验。
- 通用手机号脱敏正则会误遮盖少量含连续数字片段的 SHA/UUID 副本，password 布尔属性也被遮盖。原生 semantic_observations 标识/哈希列和原始文件可补证，本次已据此核对全部触屏安全属性；下一轮需改为有类型的审计字段脱敏，不能依赖自由文本正则处理所有元数据。
- 自然语言范围检查采用保守词汇约束；自由描述即使合理也可能 uncertain。原 CLI 参数保留，但复杂旧目标可能需改用明确的页面/事实契约，不能靠弱验证放行。
- 30 分钟会话不能证明多天稳定性；24h Soak、真实设备权限、AccessibilityService、浏览器/App 新能力均需另行审查与授权。
- 下一轮先继续 Phase 1.2.1：复现动态页面不可用并建立有证据约束的动态区域/恢复策略；把主脑拒绝明细送入下一轮情景记忆；为哈希、UUID、布尔安全属性建立有类型的脱敏规则。补回归后重跑 20 轮与 30 分钟，不能直接跳到更大自主能力。
- 后续 App/浏览器仍可通过 CapabilityRegistry、SemanticObservationProvider 与独立 verifier 扩展，沿用相同租约/预算/审计；待本轮缺口关闭并经审查后再推进，不改成固定自主任务表。
