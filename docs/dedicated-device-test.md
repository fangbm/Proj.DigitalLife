# Find X6 专用设备测试：手动启动，连续观察和导航

`dedicated_device_test` 是独立的可执行模式。用户运行启动命令即授权该有限会话内的低风险操作；不逐张批准截图、不签发普通动作的 DPAPI 批准、不提交 JAR 或冻结指纹。原严格模式保留。程序不会因安装、更新或本次开发自动运行手机。

## 使用

在仓库根目录打开 PowerShell。连接既有白名单 Find X6，手动解锁并打开 Settings 首页。先运行一次 **无模型 API** 的导航测试：

```powershell
.\data\start-dedicated-device.ps1 -Command Smoke -Session data\find-x6-smoke-01
```

这条命令会开始真实读取和一次真实点击；本次开发没有执行它。目标为当前可见的“显示与亮度”。只取当前截图/XML 中的导航行，未找到就停止，不自动滚动寻找。点击后重新截图/读取 XML，要求页面结构和截图内容区均改变，并存在页面顶部标题。输出 `settings_smoke.status=succeeded` 才算通过；没有变化不能成功。

随后直接由用户启动一小时自主会话：

```powershell
.\data\start-dedicated-device.ps1 -Command Start -Hours 1 -Session data\find-x6-life-1h-01
```

一小时走原 APILifeBrain/APIGuiPlanner、生活主体、记忆、兴趣、反思和预算；不使用 Smoke 控制器，不预置 GUI 目标。保留原模型休息和故障退避，可能一段时间只休息。前台保持运行；不会安装后台任务。

另一个终端查询或停止：

```powershell
.\data\start-dedicated-device.ps1 -Command Status -Session data\find-x6-life-1h-01
.\data\start-dedicated-device.ps1 -Command Stop -Session data\find-x6-life-1h-01
```

Status 不调用 ADB、模型或 DPAPI。Stop 使用已有粘滞紧急停止和有界 worker 收尾，只处理所属 worker。Ctrl+C 也会触发现有监督器取消收尾；建议优先使用 Stop。未知分发仍保守终止，不能 Resume 或重放。

一小时安全完成后，如用户决定继续，再独立手动运行：

```powershell
.\data\start-dedicated-device.ps1 -Command Start -Hours 24 -Session data\find-x6-life-24h-01 -AfterOneHour data\find-x6-life-1h-01
```

24 小时要求同设备、同生活库的一小时已正常完成，且无待审阅停止；不会自动接续，也不使用旧模拟器候选或批准指纹。未运行/被用户提前停止的一小时不能冒充完成。会话目录必须新建，保留旧证据；不是重新审批步骤。

## 生活和密钥

首次 Smoke 将显式历史库复制到 `data/dedicated-life/agent.sqlite3`，之后所有专用会话使用同一个库；不再次复制历史或重置请求计数、唤醒、兴趣。默认历史来源为原已结束的 `data/staged-final-24h-candidate-01/formal-24h-candidate/agent.sqlite3`。可在首次启动使用 `-SourceDatabase <当前生活库> -SourceDevice <原设备namespace>` 指定真实来源，已有专用生活库时拒绝重导入。

原生活库和已冻结候选保持原件；每次启动前对专用生活库做一致备份。迁移只增加主体/设备/会话绑定；历史记录的原设备不改。后续运行应继续使用专用生活库，不能把历史源副本也作为同一主体的活跃分支交替使用。共享 writer/subject/real 租约阻止并发控制。

原私有设备 profile 不改，在会话目录内启用本次会话的读取授权副本。只有 Start 加载既有 API DPAPI 密钥，Smoke/Status/Stop 不解密，不写新密钥。每次输入不需要 DPAPI 动作批准。私有截图/XML 留在会话 `screens/`，不提交 Git；GUI API 在自主会话内按原规划器处理截图。

## 操作范围与证据

| 项目 | 专用模式行为 |
|---|---|
| App | 仅 `com.android.settings`；不是所有 App 或应用沙箱 |
| tap | 当前 XML 中 clickable/enabled 的已知只读导航行；有效 PNG 尺寸/bounds、Settings 包/窗口根、祖先及安全子节点检查 |
| swipe | 当前已识别 RecyclerView/ScrollView/ListView 的有限垂直滑动；避开端点的输入框、开关、滑条等控件 |
| back | Settings 内回退；已识别首页不发 back；离开允许 App 后停止 |
| 禁止 | text、home、launch/Intent、任意 shell、修改开关/滑条/权限、消息/评论/发布/关注/支付/删除等 |
| visible/editable 缺失 | 原值仍为 `None`，不把 XML 推断成原生 true；在此专用低风险范围使用 PNG+单根 XML+前台包/页面的有限判断 |
| 遮挡/输入 | 锁屏、非交互、前台不符、显式安全窗口、覆盖目标的可见外部 overlay/已知输入阻断证据拒绝；未知 OEM 输入层本身不自动拒绝所有操作 |
| 时间 | 30 秒单次观察年龄上限，执行前重新采集/核对目标；不是旧原生 2 秒合同，也不是对所有 OEM 遮挡的证明 |
| 预算 | 原 SoakPolicy：120/h、500/day、32/goal、15 actions/goal、300s/task，以及原休息/退避；失败请求不退款 |
| 失败 | 输入前持久预写，传输或事后观察不确定则保留 unknown、停止、不盲目重试；原持久停止屏障保留 |

程序仍接受原固定 JSON/语义动作协议；像素动作必须落到当前安全控件，坐标由程序计算。模型没有 ADB argv、shell 或权限发放接口。单纯 `finish` 或 ADB 退出码不算业务成功。独立验证保留；专用模式的页面证据不被标为全局 trusted/native visibility。

## 停用项与隔离

| 专用模式不再经过的流程 | 保留位置/用途 |
|---|---|
| 一次性 PNG/XML 诊断申请、诊断批准账本 | 历史/严格诊断工具保留，非普通会话依赖 |
| 普通 TAP 的 DPAPI 批准加载/校验 | `PhysicalContract` 严格模式保留；DedicatedContract 不读取批准文件 |
| 每页面哈希及控件签批清单 | 严格 PhysicalRegistry 保留；专用模式根据当前只读导航控件判定 |
| 自定义 JAR 原生 visible、两次 XML 必须小于两秒 | JAR 保留为可选诊断工具；正常路径只使用 screencap 与 `/proc/self/fd/1` XML |
| Prepare→批准指纹→Run 和逐次冻结审批 | 专用 Start 一条命令创建并启动有限会话；配置完整性校验不再是人审批准 |

未删除数据库、marker、旧批准、失败日志、旧 JAR 或完整 UI fixture。未改严格 ActionPolicy/PhysicalRegistry；新模块及明确 mode 分支隔离。真实 unknown/紧急停止不属于冗余审批，不随模式简化而清除。

## 验收边界

开发验证仅限 offline_fixture。真实 Settings 首次点击、真实页面变化、真实一小时/24 小时本轮均未执行。离线结果和实际启动命令在本轮交付说明中给出；不能把合成 ADB 的成功计作真机成功。

2026-10-23 最终验证：完整回归 **921/921 PASS**（286.083秒；原903项文件/断言未改，新增18项），专用模式聚焦 **18/18 PASS**。最终全量期间生产源码 SHA-256 始终不变。日志：`data/dedicated-device-test-dev-01/full-offline-final.log`；实际来源/耗时/源码哈希：同目录 `full-offline-final-result.json`。

合成设备通过实际 `run_worker → PhoneLife → DedicatedPhone` 路径收到一次 tap，并由后续截图内容区及 XML 页面标题/结构变化验证。另有“ADB返回但页面不变”反例，结果不成功；输入抛出未知传输时只发送一次，持久停止并释放租约。修复了新模式最后一次方向/前台检查：方向在 XML 核验后变化时输入为0，故障用例修复前失败日志保留为 `rotation-before.log`，修复后在聚焦及最终全量中通过。这些都是 offline_fixture，不是已观察到的真实设备故障或真实点击。

原903项覆盖的严格物理模式、DPAPI批准、安全停止/重启/双介质、原API与动作预算均通过。原数据库、一致备份前后原件、密钥、旧profile、旧helper及失败响应、原fixture均保持字节不变；没有生成真实专用生活库或设备会话目录。

当前六项结论：可手动启动专用模式；仅Settings中上述低风险动作；第一次真机点击未执行；独立真机页面变化未观察；一小时还需用户运行Smoke成功后执行Start；相关离线回归已通过。不存在新的批准阶段或普通动作许可申请。
