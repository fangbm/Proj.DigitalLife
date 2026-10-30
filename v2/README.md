# DigitalLife V2 · M5 视频社交增量

当前版本 **2.0.0a18**，分支 `codex/v2-m6`。新增 QQ 静默原生语音入口与本地 GPT-SoVITS 角色音色适配器，见 [静默语音与角色 TTS](M6-SILENT-VOICE.md)。小酒狐角色音色已选用，真实中文合成及完整转写核验通过；源码和 wheel 各 318 项回归通过。QQ 已通过手机摄像头扫码登录，自发原生语音及 QQ 读回音频完整转写核验通过；中英混合语音仍待修。M6 仍在开发，正常自主语音选择、真人入站语音和微信静默通道未完成。[a16 声学路径证据](M6-SPEECH-DELIVERY.md) 保留。M5 本机功能完成与 a15 简化行为保持，见 [M5 验收](M5-FINAL-ACCEPTANCE.md) 和 [流程简化](SIMPLIFIED-FLOWS.md)。

M4 本机阶段验收保持完成：QQ / 主微信真实通知自动进入正确会话、现场身份核对及模型单次回复均已通过，主动联系、真人续聊及两端群 @ 回复证据齐全。见 [M4 最终验收](M4-FINAL-DELIVERY.md) · [M4 使用](M4.md)。按 v2.4 路线，M6 为 ASR/TTS 与语音消息，M7 为双向通话，M8 为长期运行。

默认完整手机 UI 操作与 DeepSeek `deepseek-flash` 视觉保持开启。M3 的真实视频摘要、长文文本读取/续读、QQ/微信通知判断和手机播放音频/本地转写验收保持；[M3 交付](M3-AUDIO-DELIVERY.md) 与 [M2 历史交付](M2-DELIVERY.md) 保留。按用户要求不导入 V1 数据；原源码、数据库、证据和密钥保留。

## 手动运行

需要 Python 3.11+、Pillow 和 PATH 中的 ADB。安装不会访问手机或模型：

```powershell
cd D:\codex\Proj.DigitalLife\v2
python -m pip install -e .
```

`find-x6` 优先读取 `v2/profiles/find-x6.private.json`，不存在时只读使用本仓库已绑定的 `data/phase123-find-x6-01/device-probe-02/pending-profile.private.json`。复制到其他机器时自行提供包含 `serial` 和完整 `identity` 的私有 JSON，并使用 `--profile 路径`。旧文件中的 V1 输入批准字段不用于 V2；手动执行 `run` 授权本次有限会话内的低风险动作。

先结束 V1 或其他手机自动化，连接专用测试手机并手动解锁、使用测试账号。实机会话须由你显式启动，或明确授权代理启动；安装、构建及离线测试不会自动开始会话。Doctor 只读取身份、显示状态和实时应用清单，不采集截图、不调用 API：

```powershell
.\start.ps1 -Command Doctor
.\start.ps1 -Command Run -Minutes 10 -Session data/sessions/find-x6-v2-10m-01
```

Run 在当前终端前台运行，最多十分钟，到期退出、不会续跑。可用 Ctrl+C，或在另一终端执行：

```powershell
cd D:\codex\Proj.DigitalLife\v2
.\start.ps1 -Command Status -Session data/sessions/find-x6-v2-10m-01
.\start.ps1 -Command Stop -Session data/sessions/find-x6-v2-10m-01
```

Status 只读本地状态。Stop 写停止标记，等待最多 15 秒；模型 HTTP 子进程和本进程创建的 ADB 子进程可被中断，不终止 ADB server 或其他进程。停止若发生在输入分发或动作后采集期间，则保留未知结果屏障。

`start.ps1` 的相对 Session 路径始终相对于 `v2/`。每次使用新的会话目录，例如后续 `...-02`，不恢复或覆盖旧会话。

等价 Python 接口（相对路径以当前工作目录为准；需自行准备 API Key 环境变量）：

```powershell
python -m digitallife_v2 doctor --profile find-x6
python -m digitallife_v2 run --profile find-x6 --minutes 10 --session data/sessions/find-x6-v2-10m-01
python -m digitallife_v2 status --session data/sessions/find-x6-v2-10m-01
python -m digitallife_v2 stop --session data/sessions/find-x6-v2-10m-01
python -m digitallife_v2 memory --profile find-x6
```

## 模型与费用

默认兼容 DeepSeek 的 Chat Completions JSON 接口，模型 `deepseek-flash`；请求设置非思考模式，结构参照 [DeepSeek 官方接口文档](https://api-docs.deepseek.com/api/create-chat-completion/)。仅 Run 启动脚本会按需解密现有 `data/deepseek.key.dpapi`，退出时恢复环境；不复制密钥、不修改原文件。也支持现有 `PHONE_LIFE_API_KEY/API_BASE/BRAIN_MODEL` 或以下 V2 环境变量。

| 变量前缀均为 `DIGITALLIFE_V2_` | 默认值 / 含义 |
|---|---|
| `API_BASE`, `API_KEY`, `MODEL` | `https://api.deepseek.com` / 环境或启动脚本 / `deepseek-flash` |
| `VISION` | 官方 DeepSeek + `deepseek-flash` 默认 `true`，其他端点/模型默认 `false`；可显式覆盖 |
| `IMAGE_TOKEN_RESERVE` | 每图预留：上述官方模型 1024，其他默认 4096 |
| `CONTENT_SAMPLES`, `CONTENT_INTERVAL` | 最多 3 个样本、采样间等待 5 秒；另计实际设备采集耗时 |
| `NOTIFICATION_PACKAGES` | 默认 Companion；M4 启用时加入已绑定账号的 QQ/微信；环境变量可覆盖，逗号分隔 |
| `FULL_ACCESS` | `true`；完整 UI 操作，保留全部页面内容和可点击控件；`false` 可使用原有限制模式 |
| `MAX_INPUT_TOKENS`, `MAX_OUTPUT_TOKENS` | 16000 / 600；输入采用 UTF-8 字节上界加封装预留，输出由请求上限控制 |
| `REQUESTS_HOUR`, `REQUESTS_DAY` | 60 / 200；在同一 V2 数据库中跨会话计数，失败请求也占额度 |
| `MAX_ACTIONS` | 80 次设备输入分发；搜索选中、输入和回车分别计数 |
| `MAX_COST` | 不设置则金额未知；设置时必须同时提供以下价格 |
| `INPUT_PRICE`, `OUTPUT_PRICE` | 由你提供可靠的每百万 token 价格，币种必须与金额上限相同 |
| `ADB`, `DATA` | `adb` / `v2/data` |

金额预算发送前按最大输入与输出预留，失败/用量缺失不会退还预留额度。无定价时只报告请求/token 用量，不把费用未知写成零。不能利用模型输出修改预算。输入摘要过大时减少控件与应用列表，应用按请求次数轮换展示，最终启动仍校验完整实时清单；不存在静态 App 白名单。

当前官方 DeepSeek Flash 使用真实 PNG 的 `image_url`，high detail，每图最多 1024 token 的计量依据见 [官方图像文档](https://api-docs.deepseek.com/guides/vision/)。其他端点/模型仍需确认能力和图像预算后开启。`VISION=false` 时明确标记 `vision_available=false`；API 拒绝图像不会冒充视觉成功。M2 的文字模式历史记录保留。

## 操作和安全边界

允许实时清单中的固定 MAIN/LAUNCHER 启动、当前控件绑定的 tap、可滚动容器中的垂直 swipe、back/home/recent_apps、搜索、通用文本输入和 1–120 秒休息。完整模式不按按钮名称或业务角色拒绝操作，包括权限弹窗、账号、设置、发送、保存、删除等控件。没有逐截图人工批准或 V1 批准指纹。模型仅能输出严格 JSON，不能发送坐标、任意 shell、Intent 或网络请求。

搜索先通过 tap 聚焦已识别的搜索框，下一轮 `search` 在该框内发送固定 CTRL+A，再重新观察并复核目标，输入 1–80 字符 ASCII 查询；输入后重新观察，提交前再读取 XML，核对相同页面、唯一搜索编辑框、焦点和最终文字完全等于查询，才发送回车。支持 EditText 和标准 AutoCompleteTextView/SearchAutoComplete。XML 的提示文字保留原值，不当成已验证空文本。仅替换当前搜索框中的临时查询；焦点、角色或文字不符时跳过提交，不重放输入。中文输入尚未支持。

完整模式保留表单、密码/验证码页面、权限对话框和多任务的真实 XML/PNG，启用视觉时按普通页面附图。`type_text(control_id,text)` 选中替换已聚焦通用编辑框，不自动回车；当前 ADB 输入支持 1–160 个可打印 ASCII 字符，含空格、邮件地址和标点，不支持中文、换行和字面量 `%s`。标点始终作为一个正确引用的文本参数发送。

仅在 `FULL_ACCESS=false` 时，原有支付、账号写入、发送、删除、权限等业务拦截、敏感表单遮蔽与多任务图片保护生效；通用输入关闭。两种模式都在 XML 缺失或不完整时禁止目标控件输入，仍可用系统导航离开。

UIAutomator 缺少 visible/editable 字段时保留未知。标签文字可以通过当前 XML 中最近的可点击父控件绑定，仍保留原始 clickable=false，并在新观察中核对父控件和标签。整屏滚动容器的手势裁剪到内部区域。模型只获得通过同一 ActionGuard 的 `allowed_actions`；正文摘要不作为可点击候选。聚焦编辑器所在的大面积高绘制顺序浮层存在时，被覆盖的后台控件不进入候选或正文摘要。

完整模式无需低风险角色白名单，但目标仍须来自当前可用控件，未暴露可点击语义的自绘界面仍可能无法绑定。窗口检查使用可见证据，后台保留 surface 本身不代表遮挡；明显遮住目标的外部窗口仍会中止该次输入。权限/安全窗口成为当前前台时可正常操作。XML 与 PNG 并非原子采集，操作期间避免人工操作或其他自动化。

每台设备使用用户目录 `.digitallife-v2/<serial>/` 下的 OS 锁，防止同一用户的多个 V2 会话并发。V1 不参与此锁，须先手动停止 V1。输入前 `PENDING_ACTION.json` 和 SQLite/JSONL 审计落盘；输入不确定、进程被终止或动作后观察失败均保留屏障，后续 Run 不会重播。Stop 的 `--emergency` / 脚本 `-Emergency` 会额外保存设备级 `EMERGENCY_STOP`，新会话不会清除。若遇屏障，先检查其指向的会话和实际设备，再由人工处理标记；不要无条件删除标记后重试。

## 数据与验收

私有数据默认位于忽略提交的 `v2/data/`：单份 `life.sqlite3`、会话 `events.jsonl`、原子更新的 `status.json`、PNG/XML。状态包含前台、最近动作、分发/拒绝次数、用量、错误、下一次唤醒及约两秒的心跳。`stale=true` 表示心跳超过 20 秒，不能据此认定进程已正常结束。源码不附带真实截图、设备标识、密钥或数据库。

事件区分 `observed`、`pending`、`action_dispatched`、`changed/unchanged`、`blocked`、`unknown`，另有内容摘要与通知记录。哈希变化只代表页面数据变化；所有业务 `achieved` 留空。时长到期使用 `completed_duration`，不能据此宣称探索成功。M2 的跨会话机制见 [M2.md](M2.md)；目标不是可执行动作队列。按新版路线，自动恢复、一小时与二十四小时留至 M6。

手动十分钟后检查同一会话的真实事件和文件：

1. 模型自主选择非 Settings App，并真实尝试固定 Launcher 启动。
2. 至少一次普通 tap 或 swipe 的真实分发，后跟独立的新观察。
3. API 用量、真实设备身份、屏幕来源与停止结果有相应记录；失败应保留具体阻断原因。

## 离线验证

```powershell
cd D:\codex\Proj.DigitalLife\v2
python -m unittest discover -s tests -v
python -m pip wheel --no-deps --no-build-isolation --wheel-dir dist .
```

测试仅用虚拟手机、临时 SQLite、模拟 ADB 返回和 loopback HTTP 服务；不会访问真实 ADB、DPAPI 或提供商。当前交付验证详情见 [M3-DELIVERY.md](M3-DELIVERY.md)。

已授权的定向真机专项可从 `v2/` 运行：

```powershell
python -m acceptance.search_and_stop --session data/sessions/find-x6-v2-search-stop-new --query settings
# 在另一终端检查该会话 READY.json 和实际 PNG/XML 结果，然后在会话仍运行时执行：
python -m digitallife_v2 stop --session data/sessions/find-x6-v2-search-stop-new
```

这是仓库内的脚本驱动验收，复用生产 SessionLoop/PhoneController，不调用模型 API，不代表新的自主模型测试。默认使用实时清单中的 LibChecker；需要已安装且已解锁的绑定测试设备。会话有三分钟硬期限，只有搜索 READY 且正式 Stop 生效后专项入口才返回成功；到期本身不算通过。

Android 导航专项同样使用生产控制器、无模型请求：

```powershell
python -m acceptance.navigation_keys --session data/sessions/find-x6-v2-navigation-new
```

顺序为 HOME → 多任务 → BACK → LibChecker → HOME。成功时生成 `NAVIGATION_READY.json`，需核对其中各步的实际 PNG/XML；分发次数本身不证明界面正确。脚本不训练生命记忆或兴趣。
