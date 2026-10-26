# V2 M1 交付记录

当前实现独立的 `digitallife_v2`：配置、观察解析、PhoneController、LifeBrain、ActionGuard、SessionLoop 和 SQLite/JSONL 状态记录；`doctor/run/status/stop` 和十分钟手动启动脚本可用。

交付范围为 M1 源码与离线验证。V1 运行源码与私有历史材料未修改，未实现 M2/M3，未启动真实手机或真实模型 API，没有 V2 真机 tap/swipe 或十分钟探索成功结论。

2026-10-27 在 Windows / Python 3.13.5 / Pillow 11.3.0 下验证：源码 **30/30 测试通过**；wheel 安装到隔离目录后，同一套测试 **30/30 通过**。全部模块另经 Python 3.11 语法解析，未在 3.11 解释器上运行测试。PowerShell 脚本语法、离线会话上的 Status/Stop、暂存内容隐私检查及 `git diff --check` 均通过。

验证包括单步完整闭环、固定命令构造、身份不符、控件跨页失效、风险按钮和嵌套标签、父控件隐藏、敏感页及截图后遮蔽、搜索焦点复核、单次模型修复、持久请求/金额限额、动作限额、设备锁、未知分发与动作后采集失败不重播、停止中断等待模型、紧急停止持久保留、只读状态和本地模拟 Chat Completions 请求。未运行 V1 测试；本次 V2 不依赖 V1 的运行模块。

已构建 `digitallife_v2-2.0.0a1-py3-none-any.whl`，SHA256 `47d000ba34a66ca5fd70b3086f972c8f504aec2ffadcb2f02590afe6830e993f`，位于本地忽略提交的 `v2/dist/`。离线会话样例位于 `v2/data/offline-delivery/`，明确为虚拟设备记录，不是真机证据。分支 `codex/v2-m1` 以原本地主干 `0faca7f` 为基线，保留该基线此前已有的 19 个未推送提交；未改动远端 main，也未创建发布或合并。

下一步由用户按 [README](README.md) 手动开启十分钟会话；无需先执行 Settings Smoke。真实结果按日志记录，不能用离线通过或单纯时长到期代替实机验收。
