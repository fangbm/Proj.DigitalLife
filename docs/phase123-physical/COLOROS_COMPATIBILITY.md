# ColorOS兼容性

实际为ColorOS V16.0.0、Android16/API36。不能将API35模拟器helper、坐标、标题或类名直接当成可信实体机语义。本轮没有push JAR、APK、安装服务、授予权限或变更系统设置。

只读兼容覆盖wm physical/override尺寸、密度、power交互状态、具名锁屏字段，以及严格限定KeyguardServiceDelegate段内的showing/mIsShowing。冲突锁屏取true；缺失/冲突方向unknown。内部displayId=0 viewport可用于方向；不把其他服务的showing=false当成解锁，不默认竖屏。

window windows/displays仅作前台/覆盖层元数据；权限、身份认证、通知或未知系统窗口保守阻止观察。overlay_absence_proven始终false，不声称已证明全窗口可信。两次真实探测均未执行截图/树捕获：首次状态未知，第二次前台/覆盖层不满足。

实际PNG/UIAutomator树、控件安全属性、导航手势区域、旋转/缩放后的动作准确性均NOT OBSERVED。用户之后可另行授权并手动打开Settings做只读语义验证；缺editable/visible等属性仍partial，不猜可点击按钮，不改系统保护。
