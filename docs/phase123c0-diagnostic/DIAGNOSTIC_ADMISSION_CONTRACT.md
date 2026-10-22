# 独立诊断准入

diagnostic_capture_admission 精确比对 PhysicalDeviceProfile 的 serial 与全部八项身份属性；仅接受设备列表恰有这个 USB 目标且 state=device。多设备即拒绝，不猜选。USB 授权依赖手机操作人的既有调试确认，不能作为采集同意。

独立授权绑定设备哈希、数据类型及次数、settings_home、输出绝对目录、短期窗口、随机 nonce、操作人批准引用、原因和 audit_id。真实 profile 无法进入 Mock 后端，真实分类许可亦被拒绝。

diagnostic_scene 要求有效原生头、锁定明确为 false、交互明确为 true、唯一主屏、Settings 主 Activity 焦点及遮挡源一致、完整原生窗口属性。当前前台必须为 Settings 主界面；未知可见窗口、其他 App、分屏、权限窗、身份/页面变化拒绝。已知系统栏仅接受 SystemUI 类型与边缘范围；通知/账号仍可能进入画面，需操作人事前检查。

隐藏窗口或未知输入消费者不能直接证明当前画面含私人内容，也不满足自主输入证明；两套结论分别保存。解析不完整时仍拒绝普通采集，不存在异常现场自动升级许可。

DiagnosticSafetySources 只读检查显式共享协调库及已有审计/控制库。共享库没有 life_controls 是正常角色。无需新 LifeSubject；若已有相关控制/审计来源必须明确列入来源清单。unknown、待审阅 marker（包括损坏文件）、EmergencyStop 或来源不可读均拒绝。不可通过传入空库替代现有协调库。

DiagnosticLease 使用原表 real 键、随机独立 owner、20 秒 TTL；总异步流程 15 秒、单步 3 秒，无 force/抢占/续无限锁。只释放自己的租约。真实激活前须独立审查来源清单完整性和多进程协调关系。
