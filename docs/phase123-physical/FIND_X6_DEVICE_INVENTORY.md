# Find X6只读设备清单

2026-10-21两次只读探测绑定同一设备指纹：`7c05ae8fa99749de880765c43754601d1c9b3a4fe861cfa34ed41882e90521b8`。实际serial、product.device、build fingerprint和包名/版本均仅保存在私有inventory，公开只保留组合SHA-256；这不是密码学硬件认证。

| 项目 | 实测 |
| --- | --- |
| manufacturer / model | OPPO / PGFM10 |
| Android / API / ColorOS | 16 / 36 / V16.0.0（oplusrom属性） |
| wm尺寸 / 密度 / 方向 | 1240×2772 / 560 / 0 |
| 交互 / 锁屏 | true / false，限第二次探测时点 |
| QEMU属性 | 未定义，不能以此单独证明物理身份 |
| 连接 | 每次末尾3个get-state=device，非长期稳定证明 |
| 电量 / USB供电 | 100 / true |
| 电池原始temperature / health / status | 326 / 2 / 5，不代表长期热安全 |
| 已安装包 | 831，均有versionCode；原清单私有 |
| PNG实际尺寸 / UI树 | NOT OBSERVED，捕获被保守拒绝 |

初次解析无法确认锁屏/方向，拒绝捕获；依据实测字段补充严格的KeyguardServiceDelegate/KeyguardStateMonitor与内部displayId=0 viewport解析后，第二次确认瞬时状态。但当前前台不是获准Settings观察页，并有保守覆盖层线索，仍未执行截图/XML命令。没有自动开App、点亮、解锁或切换页面。

真实GUI输入、模型请求、自主周期和安装均0；真实USB拔插、授权撤销、锁屏恢复、权限弹窗、语义树及长时供电均NOT OBSERVED。pending-profile仍默认关闭。
