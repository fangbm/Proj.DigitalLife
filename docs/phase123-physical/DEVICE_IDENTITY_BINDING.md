# 身份绑定

入口proj-digitallife-device-probe（也可python -m phonelife.device_profiles）必须显式--serial、--output、--consent-read-only。多设备不自动取第一台，只接受精确匹配且state=device的目标；重复目标、unauthorized/offline/缺失均拒绝。输出目录独立且不覆盖。

绑定manufacturer、brand、model、product.device、Android release/API、build fingerprint、实际QEMU属性及serial。私有pending-profile保存原值，公开只出SHA；profile默认关闭，系统升级/身份变化需重新审阅。QEMU缺失不单独授信；物理profile拒绝emulator/IP/mDNS目标，且不能混用EmulatorDeviceProfile。

观察前后重新检查身份/几何/安全状态，任何输入目前直接拒绝。未来审查通过的执行必须在原租约内、分发前再次核验当前身份/观察/语义，不能复用旧截图坐标。

legacy物理lease键保留real，避免新增serial哈希键绕过旧CLI的owner；未来按设备细化须同时更新所有执行者，本轮不改租约。USB调试及RSA确认必须用户手动完成，参考[Android官方ADB文档](https://developer.android.com/tools/adb)。本轮不启用无线ADB、root或修改授权。
