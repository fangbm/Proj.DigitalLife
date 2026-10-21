# 四入口统一准入

|入口|真实调用链|拒绝边界|
|---|---|---|
|Status|SessionLedger.read_status → status → safety_admission → physical_session_admission|只读SQLite与固定只读ADB；未知/离线/未批准不允许worker|
|Resume/Control|control_session → ledger.status|effective_resume_allowed=false时明确拒绝；先保留unknown审阅屏障|
|Supervisor|LongRunSoakController.run → ledger.status，在start及循环/最后期限审计前|同一身份、授权、预算、租约、历史判断；不通过QEMU伪装物理设备|
|Worker|run_worker → ledger.status → PhysicalContract.authorize → LifeBinding/有限租约|未验证前不创建SoakAPI或PhysicalPhone；PID须为监督器记录的所属worker|

Status的physical_device区分bound、online（未查为null）、identity_verified、observation_allowed、execution_allowed、user_authorized。基础lease/安全证据/worker_launch_allowed保留；权限不等于业务成功或正式启动批准。未批准合同在ADB前拒绝。批准后的同步只读检查总子进程超时上限8秒，worker取消/输入客户端仍有限；此延迟须在真实冒烟中量测。

EmergencyStop最高；持久unknown审阅屏障次之；不可读安全证据/身份与授权/资源故障均不能由自然到期覆盖。预算和自然NextWake仍阻止新worker，不缩短等待。worker持有全局单写者/subject/real有限租约，异常finally释放；监督器只终止Popen所属子进程，不杀ADB server或其他设备控制者。

没有本轮真实物理worker。四入口集成证据是离线临时控制库、合成profile、mock读取/输入和mock API客户端；不可当真实ColorOS验收。
