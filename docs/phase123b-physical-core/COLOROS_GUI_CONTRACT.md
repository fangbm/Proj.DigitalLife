# ColorOS16/API36 原生GUI语义合同

只使用原生uiautomator XML与只读PNG；不推送API35 JAR、不安装helper、不授予权限。UIAutomator缺editable/visible等字段时保留None，不能推断安全。两次原生树、实际PNG尺寸、方向、前台Activity及应用内容必须精确稳定；不应用API35 Uptime动态忽略特例。未知树、动画或缩放变化保守停止。

原生window合同要求完整枚举的显式可见性/Surface/type/package。仅一个Settings应用窗口；仅精确systemui状态/导航栏原生type可排除。未知ColorOS dumpsys格式、权限窗口、覆盖层、锁屏、息屏、旋转未知均不授予输入。该解析规则目前只有合成离线证据，真实ROM上的匹配性尚待只读验证，不能把失败当成功。

本轮只读复查：身份与上轮一致，PGFM10、Android16/API36、ColorOS V16.0.0，wm1240x2772。设备解锁且interactive，但非Settings前台，potential_overlay标记为true；这是保守启发式，不证明实际存在某种权限弹窗。因此截图和UI树NOT OBSERVED，未用模型代替语义证据，未发送开Settings指令。

后续由用户手动打开Settings后另行采集；若系统原生XML持续缺安全属性，应提交独立helper安全评估，而不是本轮部署或放宽属性。
