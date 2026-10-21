# 跨设备连续性方案（仅设计/离线证明）

可信源仍是最新Stage C：5个TaskRun、34条life_memories、2份新增反思、当前兴趣、LifeState cycles=5及调度。21个旧memory ID和13个新增ID不丢、不重复；原uncertain/exploration_observed不升级。本轮没有从真源创建物理迁移副本。

当前阻断：LifeMemory/State/Goal/控制/调度按device/run_kind检索，PhoneLife.name和AutonomousLife.device又参与执行及租约。源命名空间是emulator。直接改成real检索不到原生活；批量改历史device会破坏审计；将实体机伪装成emulator更不可接受。新增synthetic离线测试证明空检索和原行不变。

建议另审life_subject_id（持续主体）与execution_device_id/profile（真实硬件/lease/审计）的分离。保留旧行和设备来源，通过显式映射读取同一主体；不迁移旧观察/坐标或重放动作。未完成Goal须在新设备重新观察、重审能力/合同/剩余预算；unknown仍审阅。这是核心接口提案，未在本轮实现。

未来获准候选才使用SQLite Backup API一致性快照，全新控制库、marker、起点和审批指纹；不复制旧PID/owner，不Resume已终结控制会话。保留NextWake含义，过期由获批后原调度器处理，不改now强制活动。

逐ID/行hash核对记忆、反思、权重、状态、Goal、日程、TaskRun、ActionAttempt和请求；不混benchmark/manual/mock。预算专用承接的旧分支请求不能当成新记忆；还须核对全滚动窗口，无累计Token/金额硬限和跨库原子计数的现有风险保留。真实迁移NOT EXECUTED，等待独立合同审查。
