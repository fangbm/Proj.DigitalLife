# Find X6 最小真实闭环

阶段A使用新增 manual_diagnostic.py 的人工监督入口，复用 C0 Prep 的精确身份、Settings元数据准入、real设备租约和私有文件存储。原47运行文件、原801测试未改。不接入Agent，不签发生产DPAPI批准，无CLI或自动触发。

现场操作人须在对话中明确批准一次具体请求摘要，再由监督者调用 capture_manually_approved；请求绑定设备身份、ADB文件SHA、运行文件SHA、Settings范围、1PNG/1XML、目录和nonce，10分钟内使用。现场批准引用进入私有审计。它不是独立签发身份或密码学人类证明；同OS账号能伪造参数，因此不能用于无人值守Agent权限。

固定采集向量：adb -s AUTHORIZED_SERIAL exec-out screencap -p；adb -s AUTHORIZED_SERIAL exec-out uiautomator dump /proc/self/fd/1。仅允许附带固定devices/get-state、八个getprop、五类dumpsys元数据。无输入/切页/安装/helper/root或任意shell。输出端点使用进程stdout，不主动创建设备普通文件；OEM日志/内部临时状态不能保证为零。

每个命令3秒，整个流程15秒，拥有的host客户端收尾1秒。要求已有兼容本地ADB server，先查host:version，不主动发送start-server/kill-server，不全局终止ADB。外部server若在检查与启动间退出/替换，标准ADB客户端内部可能尝试重连/启动；这属于host并发限制，不宣称能从本组件控制共享server或设备端尾进程。命令超时/USB异常/非零退出立即停止，次数不返还，不自动重试；记录未知结果供人工检查。

同一私有目录以O_EXCL建立session启动marker，防重复运行；PNG/XML各有分发前spent marker。没有自动重建/清除入口。起始、PNG后、XML后均核对USB身份和Settings场景；原生字节先私有保存，后检失败留作隔离异常证据，不输出私人内容。只剥离严格匹配的AOSP完成尾行，保留原始字节及哈希；不任意截取XML或补属性。

ColorOS的window windows样本可缺少重复的mCurrentFocus字段；WMS display的focus/obscuring与Input focus必须一致。缺失全部焦点证据或已知冲突仍拒绝。隐藏OEM实例及输入消费者的未解释语义仍不授权自主触屏。

阶段B必须从真实XML选择具体控件，再用现有PhysicalPhone/ActionPolicy/CapabilityRegistry执行，并另行请求一次动作授权；不得手写adb input冒充核心成功。没有真实A/B成功前，不创建1小时候选。旧模拟器候选冻结，原生活记忆/任务/预算保留。1小时和24小时须分别批准。

本轮进度与实际证据见本地 dist/find-x6-minimal-live-01，不以Mock成功代替真实完成。
