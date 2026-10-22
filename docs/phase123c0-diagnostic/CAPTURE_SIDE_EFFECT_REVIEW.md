# 采集副作用审查

本轮未调用 adb。以下为上游实现静态调查，不是 ColorOS 16 二进制实测。

PNG 固定候选为 adb -s AUTHORIZED_SERIAL exec-out screencap -p。所审 AOSP 实现无文件名时写 stdout；有文件名才落盘并走媒体扫描路径。候选不传设备文件名、不使用多屏 -a。但 SurfaceFlinger/Binder、缓冲区、进程和厂商审计仍可能产生状态/资源变化，不能宣称绝对零副作用。

XML 固定候选为 adb -s AUTHORIZED_SERIAL exec-out uiautomator dump /dev/tty。所审 DumpCommand 会连接 UiAutomation、调整层级服务标志、等待 idle、读取活动窗口并 finally disconnect；默认路径是外部存储 window_dump.xml，但显式参数会替代路径。Dumper 的显式 File 重载用 FileWriter 写指定路径；另一个默认重载可能创建 /data/local/window_dump.xml 及目录。不能混用不同重载推断所有版本不写临时文件。

/dev/tty 是设备特殊端点，不是普通临时文件。但 exec-out 通常没有交互式终端；ColorOS 是否可打开它、stdout 是否混入完成信息、命令是否返回成功但未输出 XML，均 NOT OBSERVED。当前解析器只接受纯 XML，混合诊断文本会拒绝；不静默截取任意内容。本轮未验证 Android 16 OEM 端实现；可访问的 AOSP API31 源码仅支持机制调查。UIAutomation 服务连接可扰动无障碍状态、触发事件/日志和耗时；timeout 杀 host adb 不保证设备端进程立即结束，真实实现须单独审查，禁止广域 pkill、自动 fallback、helper 安装或无限重试。

因此 XML 固定候选处于 COMPATIBILITY_NOT_OBSERVED，真实后端禁用。不得把旧 API35 helper 部署到 API36。需要改变路径/增加设备临时文件或 pull/cleanup 时，必须重审命令和隐私范围，不能由一次现有批准默许。

参考：[AOSP screencap](https://android.googlesource.com/platform/frameworks/base.git/+/47e1cd5673dc3f95e09456050b81715cb48a7143/cmds/screencap/screencap.cpp)、[DumpCommand](https://android.googlesource.com/platform/prebuilts/fullsdk/sources/android-31/+/refs/heads/androidx-savedstate-release/com/android/commands/uiautomator/DumpCommand.java)、[Dumper](https://android.googlesource.com/platform/prebuilts/fullsdk/sources/android-31/+/refs/heads/main/com/android/uiautomator/core/AccessibilityNodeInfoDumper.java)、[UiAutomation wrapper](https://android.googlesource.com/platform/prebuilts/fullsdk/sources/android-31/+/refs/heads/main/com/android/uiautomator/core/UiAutomationShellWrapper.java)。
