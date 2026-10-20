# v0.2.2 continuation 的独立有限验收

此目录不属于 `phonelife` wheel，正常 CLI 不导入它。冻结执行器、主脑校验、持久化、角色设定及现有预算均不修改。默认入口不打开数据库、不创建设备、不发请求。

```powershell
.\acceptance\run-continuation.ps1
.\acceptance\run-continuation.ps1 -Live -InjectMissingContinuation -Output data/continuation-NEW
```

输出目录必须不存在。真实入口只读加载既有 DPAPI，限定原隔离模拟器及共享租约，两个独立 Python 子进程各最多一个周期，不启动后台 Agent。第一进程收到两次真实 DeepSeek 响应后，将这一个周期的两次候选替换为合法 JSON 的 `continue` / `continuation_of=null`。现有预算是首次请求加一次修复，共两次请求；不添加第三次。原响应只记录哈希，不冒称为模型自然错误。

第一进程关闭 SQLite 后，第二进程重新打开同一新库、新建主脑、默认关闭注入，真实提交含上一周期拒绝的 LifeContext，下一步由模型自由决定。仅使用已有 `force` 语义提前唤醒来避免等待失败退避；不手工改写持久化调度记录、预算、目标或 continuation 引用，仍不能抢占租约。测试记忆仅留在独立新库，不能导入正常生活库。

`acceptance_api_exchange` 将 SQLite 请求预约、TaskRun、实际 HTTP 序列化体哈希、用户消息/上下文哈希、类型化历史错误、原响应与返回候选哈希关联。没有完整提示词或凭证。传输超时不会被替换为伪响应，没有额外 API 重试。注入两次后自动关闭；已消费注入的数据库不能重新武装。

证据类别固定为 `offline_fixture`、`live_api_controlled_injection`、`live_api_natural_occurrence`。后继响应由真实模型生成，但处于受控实验的历史条件中，不把整个实验改称自然发生。

门槛 v1 要求自然错误的真实因果链，保留原 FAIL／未证明。修订门槛 v2 测量“真实传输 + 受控错误或明确分类的自然错误 + 持久化重启 + 具体错误实际送达下一轮 + 原权限预算不变”。受控注入适合确定性检验罕见故障路径，不能证明模型自然犯错或自主学习；自然样本另列 NOT OBSERVED，理由提及历史仅为启发式观察，不是协议通过条件。最终结果以新的补充报告及逐字段证据为准，不覆盖旧报告或原门槛 JSON。
