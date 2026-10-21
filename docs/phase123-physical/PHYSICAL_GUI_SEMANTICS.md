# 物理GUI语义与未来工具接口

当前仅诊断观察，所有真实输入关闭。未来坐标必须绑定当前实际PNG、方向、观察ID及可信语义；wm元数据不能替代PNG，不能套用720×1600或缩放旧坐标。原JSON/Schema/重复键、坐标、独立验证与失败重试预算不变。

标准XML兼容摘要只计算属性缺失及hash，始终trusted_for_physical_input=false。实际ColorOS UI树还未取得；覆盖层、厂商控件、密码/编辑/状态控件须另审，缺失属性不推定安全。已有helper保持仅QEMU部署。

新PhysicalCapabilityPolicy是非授予草案。Settings只读信息是未来观察范围；launcher/browser/bilibili/reader均需具体App/版本/页面/动作批准。已安装不等于批准，App白名单不等于按钮白名单；点赞、关注、收藏、评论等不属于浏览权限。

未来open_app/open_public_content/open_chat/prepare_message应共用能力请求：调用来源、候选ID、设备/观察指纹、App/版本、风险、批准引用、预算及独立合同，统一由Harness判定。本轮没有实现可调用的新工具、Deep Link、QQ消息或B站互动。真实GUI能力NOT OBSERVED，不以mock输入充数。
