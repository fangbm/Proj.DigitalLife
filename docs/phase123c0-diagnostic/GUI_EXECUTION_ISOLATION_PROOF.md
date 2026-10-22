# 隔离证明边界

三个新增运行文件，所有原44个运行文件保持字节一致，包括 physical_execution、ActionPolicy、CapabilityRegistry、longrun、DeviceLease、预算、休息及86400秒期限。未增加 CLI 默认采集入口。

静态依赖：新增模块不导入 brain/llm/loop/physical_execution/life_storage/life_subject/longrun/budgets/policy/capabilities。复用 device_profiles 身份数据、native_windows 纯解析、readonly_sources 只读协调角色审查，不走执行准入。

动态 offline_fixture：屏蔽 subprocess/asyncio subprocess/urlopen 后完成 Mock 采集；检查临时协调库 TaskRun、memory、events、schedule、run_schedules 行数和内容不变；正式控制测试文件逐字节不变；租约竞争、Unknown Dispatch、EmergencyStop、取消、重启均有单独测试。没有获取物理输入许可；结果始终 GUI_EXECUTION_SEMANTICS_TRUSTED=false。

本轮最终历史完整性报告直接核对原数据库、DPAPI、候选、旧证据和夹具字节；它比“未导入核心”更具体。Mock 成功不证明 Find X6 实际采集、输入或自主恢复；这些仍 NOT EXECUTED。
