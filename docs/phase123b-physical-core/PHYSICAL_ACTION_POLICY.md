# 物理动作安全合同

原ActionPolicy(real)对坐标/文本的硬禁保持。新PhysicalActionPolicy只在独立授权合同下接受tap、swipe、back、finish；text/home/launch/任意Intent始终无能力。finish不表示成功。

PhysicalRegistry继承现有CapabilityRegistry的保守Settings已知只读路径，还要求精确App版本、页面完整结构摘要、控件resource/class/动作类别及所有安全属性。App白名单不是App沙箱。未知页面不可以靠视觉坐标猜测；legacy坐标须先语义落地并由程序重新生成。scroll区域含开关、编辑器或未知危险属性时拒绝。back需单页明确批准；没有物理恢复启动。

PhoneLife保留原动作/请求预算、租约心跳、原验证器与审计。新接缝在分发前设置一次性permit；executor重新读取身份、PNG、双树及安全窗口，复核控件、坐标/方向、授权、控制状态、期限、真实device/subject/单写者租约和预写ActionAttempt。permit只消费一次。没有预写审计不得输入。分发后再查身份和安全状态；未知结果停止并绝不重发。

系统观测与外部手机之间不存在原子事务。用户操作、USB热插拔或紧急停止可能紧邻最后核验到实际ADB启动的极小窗口；通过末次同步停止/租约核验、有限单输入、分发前不确定性预写和事后观察保守处理，不宣称消除此物理TOCTOU边界。

当前APP_CAPABILITY_MANIFEST enabled=false、pages为空，无真实可执行控件。未来必须在独立审查后为具体App/版本/页面/控件授予最小权限；不开放QQ消息、点赞评论、关注、支付、账号修改、上传、安装卸载或系统权限。
