# Phase 1.2.3-B 执行架构（v0.2.3）

基线59e3fe81b2c49dba0e835c5a8131f463dba911f4。新增life_subject.py与physical_execution.py；修改autonomy/memory/budgets/storage的身份检索接缝、loop的物理分发接缝及longrun三文件。人格、选题算法、continuation、独立验证器、模型协议和模拟器权限范围不改。

生活路径：显式一致性副本 → migrate_subject原子迁移 → LifeBinding四种ID → attach_subject → 同一AutonomousLife。GUI/TaskRun/ActionAttempt仍写execution_device_id；主体状态、兴趣与NextWake采用subject:<id>兼容命名空间。历史device列与ID不改，不把emulator伪装physical。

执行路径：未来经过审查的prepare_physical_session → 新awaiting_approval会话 → 四入口统一SessionLedger.status/physical_session_admission → operator DPAPI批准 → 全局自主账本写入者/subject/real三个有限租约 → 原AutonomousLife/PhoneLife → PhysicalRegistry/PhysicalActionPolicy → 单次permit → 预写审计 → PhysicalPhone.act → 独立新观察与原验证器。模型不能签发批准或改变manifest。

新增准备API仅在临时合成库测试；本轮不创建真实物理候选。公开CLI不提供解除物理限制的参数。真正的批准签发仪式不在本轮提供；后续必须先审查并另行授权。原通用real CLI仍保持原限制，不能用它绕过新执行合同。

所有物理执行默认关闭。原只读PhysicalDeviceProfile保持input_enabled=false，不因这一字段修改而启用执行。新执行器另需独立绑定的DPAPI批准。完整源码包保留未获公开例外的旧UI fixture，因此仅本地私有交付。不得推送。
