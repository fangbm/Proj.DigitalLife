# Find X6 全 App 自主探索：手动启动有限会话

`dedicated_device_test` 的普通 Start 使用 `app_scope=installed_launchable`。用户手动启动即授权该有限会话内的低风险观察和操作；无 App 白名单、每 App 批准、普通动作 DPAPI 令牌、JAR 或冻结批准步骤。原严格模式保留。程序不会因安装、更新或本次开发自动运行手机。

## 使用

首次真实一小时之后的修复及 `-Minutes 10` 复测命令见[本轮结果说明](find-x6-first-run-fix.md)。原 `-Hours 1/24` 继续兼容，十分钟不会替代一小时门槛。

在仓库根目录打开 PowerShell。连接绑定的 Find X6，手动解锁；不要求打开 Settings。直接运行：

```powershell
.\data\start-dedicated-device.ps1 -Command Start -Hours 1 -Session data\find-x6-all-apps-1h-01
```

这条命令会开始真实 API 与手机操作。本轮开发没有执行它。Brain 和 GUI 规划器使用相同生活主体、记忆、兴趣、反思和请求预算，环境提示改为动态 App/控件能力；continuation、独立验证和角色设定保持。没有指定 GUI 目标，不强制动作频率；模型可以休息，原唤醒和退避继续生效。前台保持运行，不安装后台任务。旧 `-Command Smoke` 仍是可选、无 API 的单次 Settings 导航诊断，普通 Start 不再依赖其结果。

另一个终端查询或停止：

```powershell
.\data\start-dedicated-device.ps1 -Command Status -Session data\find-x6-all-apps-1h-01
.\data\start-dedicated-device.ps1 -Command Stop -Session data\find-x6-all-apps-1h-01
```

Status 不调用 ADB、模型或 DPAPI。Stop 使用已有粘滞紧急停止和有界 worker 收尾，只处理所属 worker。Ctrl+C 也会触发现有监督器取消收尾；建议优先使用 Stop。未知分发仍保守终止，不能 Resume 或重放。

一小时安全完成后，如用户决定继续，再独立手动运行：

```powershell
.\data\start-dedicated-device.ps1 -Command Start -Hours 24 -Session data\find-x6-all-apps-24h-01 -AfterOneHour data\find-x6-all-apps-1h-01
```

24 小时要求同设备、同生活库、同全 App 范围的一小时正常完成，且无待审阅停止；不会自动接续，不使用旧候选/指纹。旧 Settings-only 小时不能替代全 App 小时。目录必须新建以保留证据，不是新审批步骤。

## 生活和密钥

首次 Start（或可选 Smoke）将显式历史库复制到 `data/dedicated-life/agent.sqlite3`，之后所有 App/会话共用同一生活库，不重置请求计数、唤醒、记忆或兴趣。默认来源为原已结束的 `data/staged-final-24h-candidate-01/formal-24h-candidate/agent.sqlite3`。首次可用 `-SourceDatabase <当前生活库> -SourceDevice <原设备namespace>` 指定来源，已有专用生活库时拒绝重导入。

原生活库和已冻结候选保持原件；每次启动前对专用生活库做一致备份。迁移只增加主体/设备/会话绑定；历史记录的原设备不改。后续运行应继续使用专用生活库，不能把历史源副本也作为同一主体的活跃分支交替使用。共享 writer/subject/real 租约阻止并发控制。

原私有设备 profile 不改，在会话目录内启用本次会话的读取授权副本。只有 Start 加载既有 API DPAPI 密钥，Smoke/Status/Stop 不解密，不写新密钥。每次输入不需要 DPAPI 动作批准。私有截图/XML 留在会话 `screens/`，不提交 Git；GUI API 在自主会话内按原规划器处理截图。

## 操作范围与证据

| 项目 | 专用模式行为 |
|---|---|
| App | 固定 `pm list packages` 与 Launcher query 的交集；每次观察刷新。模型选择 package 必须仍在当前实际清单内；无每 App 批准 |
| open_app | 固定 MAIN/LAUNCHER resolve 得到的组件须属于清单内同包；固定 `am start -W … -n` 启动，不开放 Intent URI/任意参数 |
| tap | 当前 clickable/enabled 控件的可识别浏览/导航/播放/搜索角色；有效 PNG/bounds、当前前台包/窗口、祖先、子树、重叠与页面上下文检查；未知效果跳过 |
| swipe | 当前已识别 RecyclerView/ScrollView/ListView 的有限垂直滑动；避开端点的输入框、开关、滑条等控件 |
| back | 当前已安装 App 中回退，可回桌面或跳过不支持页面；不要求 Settings 首页 |
| search_text | 仅结构和资源/描述明确为搜索的已聚焦空 EditText/AutoCompleteTextView；受限 ASCII 查询，不用剪贴板/新 IME/helper；提交前重新确认同页同搜索框。输入和 Enter 各计一个动作，continuation 跨 worker 仍按实际命令扣余量。支持长按的搜索框仍可短按，不开放长按 |
| 禁止 | 任意 shell/Intent、普通编辑器/消息输入、点赞/关注/收藏/评论/发布/上传、支付/购买、删除/安装/卸载、权限/安全设置及凭证处理。页面/表单/模态框、控件角色/原生状态、资源与文字共同判定 |
| visible/editable 缺失 | 原值仍为 `None`，不把 XML 推断成原生 true；在此专用低风险范围使用 PNG+单根 XML+前台包/页面的有限判断 |
| 遮挡/输入 | 锁屏/非交互拒绝执行；具体覆盖目标的 overlay/已知输入阻断拒绝。未知 OEM 输入层本身不一票否决。识别到凭证/付款/消息编辑/权限表面时先遮蔽、不把原 PNG/XML 送给模型，可 back/open_app |
| 时间 | 30 秒单次观察年龄上限，执行前重新采集/核对目标；不是旧原生 2 秒合同，也不是对所有 OEM 遮挡的证明 |
| 预算 | 原 SoakPolicy：120/h、500/day、32/goal、15 actions/goal、300s/task，以及原休息/退避；失败请求不退款 |
| 失败 | 输入前持久预写，传输或事后观察不确定则保留 unknown、停止、不盲目重试；原持久停止屏障保留 |

全 App GUI 使用独立、严格单 JSON 的语义 Schema，增加 `open_app(package_name)` 和 `search_text(control_id,text,submit)`；原严格模式协议不变。像素动作也必须落到当前安全控件，坐标由程序计算。未知/风险候选最多在原三次提议预算内改选，然后诚实记 uncertain，不终止整场监督会话。实际分发后不确定仍停止待审阅。每次动作后独立重读 PNG/XML/前台并关联 ActionAttempt；`finish`、ADB 退出码或“打开 App”不能证明读完文章等更广目标成功。

限制：OEM/Canvas/WebView 未暴露可靠控件、目标歧义、中文输入、复杂手势暂不支持，可换 App/休息。没有承诺每个 App 的每个控件都可操作。风险策略是保守的语义规则，不是对任意恶意 App/伪造 XML 的形式化副作用证明；识别到敏感表面先遮蔽，无法识别的敏感信息仍是视觉采集边界。仅使用专用手机上的公开/测试账号内容。缺少 XML 时返回明确的合成遮蔽图，不能计为真实页面内容或验证成功。

## 停用项与隔离

| 专用模式不再经过的流程 | 保留位置/用途 |
|---|---|
| 一次性 PNG/XML 诊断申请、诊断批准账本 | 历史/严格诊断工具保留，非普通会话依赖 |
| 普通 TAP 的 DPAPI 批准加载/校验 | `PhysicalContract` 严格模式保留；DedicatedContract 不读取批准文件 |
| 每页面哈希及控件签批清单 | 严格 PhysicalRegistry 保留；专用模式根据当前只读导航控件判定 |
| 自定义 JAR 原生 visible、两次 XML 必须小于两秒 | JAR 保留为可选诊断工具；正常路径只使用 screencap 与 `/proc/self/fd/1` XML |
| Prepare→批准指纹→Run 和逐次冻结审批 | 专用 Start 一条命令创建并启动有限会话；配置完整性校验不再是人审批准 |
| Settings-only App/控件列表、必须 Settings 首页、Smoke 成功前置 | 普通 Start 已移除；旧 Smoke/严格模式仍保持原范围，原始历史不改 |

未删除数据库、marker、旧批准、失败日志、旧 JAR 或完整 UI fixture。未改严格 ActionPolicy/PhysicalRegistry；新模块及明确 mode 分支隔离。真实 unknown/紧急停止不属于冗余审批，不随模式简化而清除。

## 上一轮开发验证（历史；用户随后已手动完成首次一小时）

本轮仅 offline_fixture：没有发送真实 ADB 或模型请求，没有启动真实一小时/24 小时，也没有创建真实专用生活库。真实任意 App 启动、Settings 首次点击、搜索和跨 App 行为仍为 NOT OBSERVED，等待用户运行上述命令后的实际记录。

本轮覆盖实际 worker→Brain/GUI→执行器→独立验证的两个自主周期、跨 App 记忆引用、搜索、预算和高风险/未知分发路径；API 和设备响应均为合成离线 fixture。最终全量日志为 `data/all-app-exploration-dev-01/full-offline-final-r5.log`，结果及运行期源码哈希在 `full-offline-final-r5-result.json`。开发过程中搜索框长按属性、导航伪装写按钮、复合搜索跨轮预算、前台验证范围四项反例分别保留在 `search-editor-before.log`、`write-effect-before.log`、`continuation-budget-before.log`、`foreground-scope-before.log`，不是自然故障。

2026-10-23 最终结果：**949/949 PASS**，206.939 秒；原921项运行测试保留，原22个测试源码文件及断言字节不变，新增28项。生产源码在全量期间未变。合成两个周期实际发送四条固定命令（打开 reader、搜索输入、Enter、切换 video），前一周期三条消耗持久进入下一轮余量与情景记忆；观察目标记 `exploration_observed`，窄前台目标经独立重读才记 `succeeded`。这些结果不代表真机验收。

上一轮基线921/921及原Settings Smoke证据保留在 `data/dedicated-device-test-dev-01/`，本轮不覆盖。历史测试文件和安全断言不改；数据库/DPAPI/profile/helper/fixture完整性对比见本轮 `integrity.json`。旧严格模式、预算、停止/重启/双介质故障的全量回归继续保留。

当前一小时启动只需手机连接、解锁和用户执行 Start；模型仍按原请求/动作预算与休息规则运行。本轮没有新的审批阶段。
