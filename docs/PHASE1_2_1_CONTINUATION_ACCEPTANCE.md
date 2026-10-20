# v0.2.2 continuation 最后一项验收补充报告

**结论：按显式修订的 v2 受控故障验收定义 PASS；自然发生样本 NOT OBSERVED。原 v1 未证明结论及原始 FAIL 文件保持不变。**

本轮新增独立、默认关闭的验收工具和 14 项测试，未修改冻结的 36 个生产运行文件、原 198 项测试、角色、能力、请求预算或 SQLite 持久化路径。原 wheel 不重建；本轮进行聚焦真实复验，没有重新宣称完成一次固定 20 轮或 30 分钟自主验收。此前冻结 F 的 20/20、1800.001 秒证据继承引用并逐文件核验保留。

## 五个问题的回答

1. **代码级错误传递成立：PASS。** 原 198 项加新增 14 项，最终 **212/212 PASS**。从候选拒绝、TaskRun、情景记忆到重启后的上下文逐字段比较通过。
2. **真实 API 受控注入后下一轮收到具体错误：PASS。** 实际 HTTP 传输边界、冻结主脑输入审计、请求预约记录及下一轮 TaskRun 哈希关联一致；七个必需字段均实际提交并获得 DeepSeek 响应。
3. **真实模型自然产生相同错误：NOT OBSERVED。** 旧 30 分钟自然会话没有该错误，本轮未替换的后继主脑响应也未产生该错误。源周期原始提供商响应只保存哈希，被替换后的错误明确属于受控注入，不推断原响应是否碰巧包含同错。
4. **未经授权动作、假成功、旧动作重放：均为 0。** 源周期没有目标行、GUI 请求、action_attempt 或恢复分发。后继周期由模型选取 explore，实际执行 {'tap': 1}，经原安全绑定；最终仍为 uncertain / GOAL_NOT_VERIFIED，未将任务提升为成功。
5. **各门槛状态：见下表。** v2 是明示的验收口径修订，不是把原自然样本改称 PASS；自然出现与模型行为改变分别报告。

## 门槛定义修订与证据类别

| 类别 | 状态 | 含义 |
| --- | --- | --- |
| offline_fixture | PASS | 212 项离线测试，无真实网络行为声明 |
| live_api_controlled_injection | PASS | 两次真实响应被替换，严格拒绝落盘，另一进程真实 API 接收错误 |
| live_api_natural_occurrence | NOT OBSERVED | 没有观察到未替换模型自然产生同错 |

**v1**：自然模型错误源头以及其下一轮的真实因果链。本轮仍未证明，保留旧 `data/phase122/acceptance-gates.json` 的 false 和旧报告。
**v2**：允许明确分类的受控错误作为故障源，必须同时通过真实请求、现有预算、无源目标执行、逐字段持久化、进程退出/重开 SQLite、关闭注入、下一轮实际送达及全部安全检查。原因是罕见模型错误不能靠有限自然观察稳定触发，而其传递链路可以在真实 API 上确定性检验。v2 不宣称模型自然犯错、长期自主稳定或已学会避免错误；自然发生仍单列 NOT OBSERVED，留待审查。

| 门槛 | 当前状态（v2） | 证据范围 |
| --- | --- | --- |
| original_162_tests | PASS | 冻结 F 证据；离线及本轮相关安全另有复核 |
| final_offline_198_tests | PASS | 冻结 F 证据；离线及本轮相关安全另有复核 |
| fixed_gui_at_least_18_of_20 | PASS | 冻结 F 证据；离线及本轮相关安全另有复核 |
| zero_false_success | PASS | 冻结 F 证据；离线及本轮相关安全另有复核 |
| zero_unauthorized_dispatch | PASS | 冻结 F 证据；离线及本轮相关安全另有复核 |
| autonomous_time_30_minutes | PASS | 冻结 F 证据；离线及本轮相关安全另有复核 |
| autonomous_model_touch | PASS | 冻结 F 证据；离线及本轮相关安全另有复核 |
| autonomous_model_chose_rest | PASS | 冻结 F 证据；离线及本轮相关安全另有复核 |
| autonomous_memory_received_next_episode | PASS | 冻结 F 证据；离线及本轮相关安全另有复核 |
| autonomous_reflection_received_next_episode | PASS | 冻结 F 证据；离线及本轮相关安全另有复核 |
| semantic_recovers_after_autonomous_scroll | PASS | 冻结 F 证据；离线及本轮相关安全另有复核 |
| continuation_error_detail_in_next_episode | PASS | 本轮受控真实 E2E |
| dynamic_pressure_trusted | PASS | 冻结 F 证据；离线及本轮相关安全另有复核 |
| original_data_preserved | PASS | 冻结 F 证据；离线及本轮相关安全另有复核 |

## 两个真实周期

| 周期 | TaskRun | PID | 注入 | 结果 | 单调秒 |
| --- | --- | --- | --- | --- | --- |
| source | `3ecc99d9-7775-4063-927b-f8079dc9f51f` | 12092 | 2 次 | failed / MISSING_CONTINUATION_REFERENCE | 25.417 |
| successor | `a4f76b26-b391-4abb-a1d3-10614ecd97d0` | 31060 | 0 次 | uncertain / GOAL_NOT_VERIFIED | 45.471 |

两次候选请求是**首次生成 + 一次修复**，generation_attempts 为 1、2，repairs_used 为 0、1。保持原上限，不增加第三次请求；对应两次真实 DeepSeek 调用，原始响应与返回替换内容分别留哈希。仅替换一个源周期，不修改角色或下一轮目标。
第一进程关闭后的 SQLite SHA 与第二进程打开前一致：`edc003383396a3e13a0653bf6105f72dc63273ea693830fb7334b2836b613b25`。第二进程 injection=false、替换次数 0。
独立测试库保持核心 run_kind=autonomous 以覆盖真实检索路径；实验类别通过关联事件明确标注为 live_api_controlled_injection，物理数据库与普通生活库隔离，不导入受控经历到正常生活库。
采用原 run_once(force=True) 语义定向提前唤醒下一周期；这是验收调度，不是模型选择目标，不绕过租约或 API 配额，不手工修改调度行。没有 `--goal` 或预编排路线。
真实 API 请求账本：[{'phase': 'brain', 'status': 'received', 'count': 3}, {'phase': 'gui', 'status': 'received', 'count': 2}, {'phase': 'reflection', 'status': 'received', 'count': 1}]。受控源周期没有 GUI/反思/验证模型请求。

## 实际下一轮输入与 SQLite 关联

下一轮请求 ID：`a9e40eb5-8a80-4777-a038-74b0c65321d6`；TaskRun：`a4f76b26-b391-4abb-a1d3-10614ecd97d0`；关联事件 ID：13。
实际 HTTP JSON 体 SHA：`ccb72c038a0597face4eaf174a183dab7a7e76b3b0d0c5fe7b4fba37a48513f6`。
实际用户消息 SHA：`aaf1af7049ce227547b21d8f037fa2f05d8059601abf65ea7aa0b468afc3c912`。
LifeContext SHA：`a671634fb6498c09ad1c65b86d0f5df5bc31309be5f226059fd5ea9a9714d772`。
DeepSeek 后继响应 SHA：`3da8415e5a1ce3b004dca6fbc5fc3fad88b42983ebdae6ba7ba82d8d28807d41`。
完整敏感提示词不落盘。传输子类在 frozen ChatAPI._request 入口使用同一序列化规则对实际 payload 计算哈希；离线 HTTP 边界测试另核对真实 Request.data 字节。真实运行的主脑审计中用户消息/上下文/响应哈希再次吻合，life_requests 为 received，event_sources 和 life_memory_sources 关联原 TaskRun。

| 字段 | 原记录与实际 API 上下文一致的值 |
| --- | --- |
| reason_code | `["MISSING_CONTINUATION_REFERENCE", "MISSING_CONTINUATION_REFERENCE"]` |
| field | `["continuation_of", "continuation_of"]` |
| expected | `["kind=continue requires selected.continuation_of from eligible_goal_ids", "kind=continue requires selected.continuation_of from eligible_goal_ids"]` |
| source_task_run_id | `["3ecc99d9-7775-4063-927b-f8079dc9f51f", "3ecc99d9-7775-4063-927b-f8079dc9f51f"]` |
| candidate_sha256 | `["1c47e4098933667048adafe2173a3348dc50610f328c62e4322b36d2f77dbb0f", "db4878f988ba6d36ad5bf07bb467352a03550f6b9d86e1e9ec401b95928065ad"]` |
| executed | `[false, false]` |
| confirmed_actions | `[0, 0]` |

原情景记忆 ID：`71c498ac-5498-48b1-a89d-e4067249f16c`。
逐次 candidate SHA、完整七字段、请求行、事件行和记忆 ID 在 `causal-proof.json` 中可独立复核。

## 模型行为与送达分别统计

后继真实模型选择 `explore`，同类错误再次发生：`False`。
理由中明确提及此前 malformed continue：`True`；理由哈希 `9143dc3c6e83ff9b6f32749f085e0872be3a1024a4d7eb71f89998c72023fe1f`。人工核对的脱敏含义为：避开此前格式错误的 continue，转而选择具体、可验证的只读页面目标。该表述不作为送达门槛条件，也不证明学习或反事实因果。
后继实际目标仍因自然语言声明比独立验证谓词更宽而 uncertain；该真实限制及合法 tap 均保留，不为提高验收结果改写合同或目标。

## 文件、测试、完整性与限制

- `acceptance/continuation.py`：默认关闭、实际传输边界哈希、单周期替换、两进程生命周期。
- `acceptance/run-continuation.ps1`：既有 DPAPI 只读加载、Settings 原白名单、有限入口。
- `acceptance/verify_continuation.py`：SQLite 只读因果核对、版本化门槛。
- `acceptance/report_continuation.py`：只读完整性核验与派生报告。
- `acceptance/README.md`、本报告、新增 `tests/test_continuation_acceptance.py`：接口说明和 14 项离线回归。
- 原 198 项断言/文件未改。新增覆盖默认关闭、JSON/引用分离、预算、HTTP 字节哈希、重启字段传递、一次性注入、重新武装拒绝、来源隔离、超时/取消/审计 I/O 失败释放、凭证/提示词不落账本。
- 全量日志 `data/continuation-regression-final-212.log`；首次开发语法错误日志与所有源 failed 样本保留，没有删除失败轮次。
- 新库 quick_check=ok；核验原图 9、原始树 9、内部一致性帧 9、追加独立采样 1；升级前备份共享协调库及原自主库，两库原行多重集完整。原私有清单的 5373 个文件逐项哈希保持。
- 原 36 文件生产 SHA、既有 wheel、旧报告、旧门槛、原 198 测试及角色/DPAPI 均保持。新源码/公开证据包和私有逐文件清单另命名，不覆盖旧 ZIP 或哈希清单。
- 公开包不包含 PNG、完整树、SQLite、未经筛选模型自由文本或完整提示词。发布前对实际凭证 UTF-8/UTF-16 精确内存扫描，检查结果单独留存。
- `force` 只跳过本次验收唤醒时间，不突破 API/动作预算或租约。取消/异常的离线回归证明正常清理；进程被强杀仍依赖原有限租约超时恢复，未新增常驻守护。
- 未观察自然错误，未做新 24 小时或长期稳定性声明，也未扩权限、安装 App 或开发 Phase 1.3。所有本轮测试进程退出、租约释放，保留原模拟器，停止开发等待审查。
