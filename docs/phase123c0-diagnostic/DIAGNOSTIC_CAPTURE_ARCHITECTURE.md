# C0 Prep：独立人工诊断通道

本版本仅支持 offline_fixture。PhysicalDiagnosticCapture 默认构造拒绝；仅接受精确类型 MockAdbDiagnosticBackend 和 OfflinePermitVerifier。RealAdbDiagnosticBackend 无进程创建代码，调用恒定返回 PREP_REAL_DISPATCH_DISABLED；没有可启用它的 CLI、配置或 enabled/force 标志。

流程：验证人工授权协议 → 私有目录校验 → 读取显式安全来源 → 竞争共享 real 租约 → 身份及 Settings 内容范围检查 → 独立 SQLite 事务消费次数 → 一次固定采集 → 格式及尺寸验证 → 再次身份/场景/授权/租约校验 → 私有文件独占写入及 fsync → 独立审计 → 释放租约。

授权、证据、采集分别位于 diagnostic_authorization.py、diagnostic_evidence.py、diagnostic_capture.py。自主执行模块不调用这些组件；这些组件不调用主脑、GUI 模型、Reflection、Memory、LifeSubject、TaskRun、动作策略或 worker。无需创造生活主体、正式控制会话或 API 预算。

窗口诊断使用既有 native_windows 的纯解析器，建立独立的屏幕内容范围判断。没有调用完整自主执行准入，也不修改其参数。隐藏 OEM/壁纸输入通道可保留为未解决执行风险，但不能因此授予任何输入能力。

本轮真实后端是明确禁用的桩，未来启用须另行审查固定进程传输、OEM XML 输出和人工批准仪式；本轮交付不构成可立即执行真实采集的许可证。没有新增默认启动入口。
