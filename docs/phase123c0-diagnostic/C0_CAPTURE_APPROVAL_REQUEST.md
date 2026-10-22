# 下一轮授权审查请求（不是许可证）

本轮不签发、不执行真实采集。只有用户另行明确同意并完成真实后端/人工批准仪式审查后才能开始；本 Prep wheel 无启用开关。

未来建议：仅指定 USB Find X6，绑定八项身份摘要和私有 serial；用户手动解锁、打开 Settings 主界面、检查通知与账号显示。最多 PNG 两次、XML 一次；每个命令只发一次，未知结果不重发。授权窗口最多10分钟；每次诊断15秒上限、单步3秒。上游 UIAutomator idle 可需10秒，若3秒不足须重新审查时限，不在运行时扩大。

采集：必要前后身份、锁定/窗口/输入元数据，及候选 screencap -p、uiautomator dump /dev/tty（完整固定列表见 FIXED_ADB_OPERATIONS.json）。没有 app 切换、输入、安装或 helper。XML /dev/tty 在 ColorOS 尚未验证，不能以其他路径/任意 shell 自动替代。

原始目录建议 D:\codex\Proj.DigitalLife\data\phase123c0-find-x6-capture-01\private，须当次创建并验证 owner-only，不写当前 dist。PNG/XML/元数据均私有；只生成白名单哈希摘要。不上传 GitHub，不发给 DeepSeek，不进入生活记忆。可能含通知、账号、设备信息、文本、完整 UI 层级；未经再次脱敏审查不公开。

副作用：截图经过图形服务；UIAutomator 会连接无障碍自动化服务、调整标志、等待/日志，特殊输出端点 OEM 兼容性和设备端残留未验证。不能承诺零临时文件或强制结束 host 后设备端无尾进程。详见 CAPTURE_SIDE_EFFECT_REVIEW.md。

目的：核对 PNG 实际尺寸、ColorOS 原生 XML 属性缺口和窗口关联。采集完成不能证明控件可安全操作、GUI 输入成功、API自主任务完成或1小时/24小时门槛。

用户未来应明确说：批准指定设备的本次 Settings 主界面本地私有诊断，PNG≤2、XML≤1、10分钟窗口，并确认目录、隐私风险及 UiAutomation 副作用；还需当次 audit_id/nonce 和操作人引用。此处文字、旧批准指纹和 enabled 参数都不能生成有效 artifact。若要求改变命令、时间或采集异常屏幕，应提交新的授权范围。
