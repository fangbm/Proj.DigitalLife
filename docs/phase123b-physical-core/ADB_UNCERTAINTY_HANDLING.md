# USB断连与未知分发

|阶段|处理|重放|
|---|---|---|
|观察/分发前身份或USB失败|不输入；已确认未发送时attempt=not_executed|无|
|ADB输入调用已进入，失败/超时/取消|预写保留uncertain_after_transport_error；当前executor锁死|禁止|
|输入返回后、结果确认前断连或身份变化|结果未知，进入原人工审阅流程|禁止|
|重新连上但身份变化|拒绝当前执行|禁止|
|worker/监督器重启|先查原TaskRun/ActionAttempt/租约/control/marker|禁止消费旧动作|

PhoneLife仍在ADB前预写不确定状态，PhysicalPhone._unknown拒绝后续permit。原SessionLedger.latch_review双介质实现不改：marker失败仍尝试SQLite、SQLite失败由marker保守保护、部分/损坏marker仍阻止运行、双介质失败由原始动作审计再检查。无法在所有存储都不可写且原始审计又丢失的系统崩溃后提供不可丢失事实保证；不得声称消除这一工程边界。

本轮故障全部offline_fixture，临时SQLite和mock输入；未知分发不是真实越权事件。原安全停止回归与断电/强杀证据保持原分类。
