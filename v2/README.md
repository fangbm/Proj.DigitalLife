# DigitalLife V2 · M1

独立 Python 实现，当前只交付 M1 的有限会话：观察 → 单个模型动作 → 风险检查 → 分发前复核 → 持久记录 → ADB 分发 → 再观察。V1 源码、数据库、证据和密钥保留，不导入 V1 运行模块。[设计原文](DESIGN.md)中的 M2/M3 尚未实现。

**状态：2026-10-27 已完成自主真机测试和复测，另通过真实搜索提交、运行中 Stop 的定向验收，M1 核心验收可结项。** 自主复测 9 次分发、0 次拦截、0 次未知结果；搜索/停止专项约 0.45 秒响应 Stop。证据与限制见 [DELIVERY.md](DELIVERY.md)。

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
```

## 模型与费用

默认兼容 DeepSeek 的 Chat Completions JSON 接口，模型 `deepseek-flash`；请求设置非思考模式，结构参照 [DeepSeek 官方接口文档](https://api-docs.deepseek.com/api/create-chat-completion/)。仅 Run 启动脚本会按需解密现有 `data/deepseek.key.dpapi`，退出时恢复环境；不复制密钥、不修改原文件。也支持现有 `PHONE_LIFE_API_KEY/API_BASE/BRAIN_MODEL` 或以下 V2 环境变量。

| 变量前缀均为 `DIGITALLIFE_V2_` | 默认值 / 含义 |
|---|---|
| `API_BASE`, `API_KEY`, `MODEL` | `https://api.deepseek.com` / 环境或启动脚本 / `deepseek-flash` |
| `VISION` | `false`；默认只发真实 XML 控件摘要，明确 `vision_available=false` |
| `MAX_INPUT_TOKENS`, `MAX_OUTPUT_TOKENS` | 16000 / 600；输入采用 UTF-8 字节上界加封装预留，输出由请求上限控制 |
| `REQUESTS_HOUR`, `REQUESTS_DAY` | 60 / 200；在同一 V2 数据库中跨会话计数，失败请求也占额度 |
| `MAX_ACTIONS` | 80 次设备输入分发；搜索选中、输入和回车分别计数 |
| `MAX_COST` | 不设置则金额未知；设置时必须同时提供以下价格 |
| `INPUT_PRICE`, `OUTPUT_PRICE` | 由你提供可靠的每百万 token 价格，币种必须与金额上限相同 |
| `ADB`, `DATA` | `adb` / `v2/data` |

金额预算发送前按最大输入与输出预留，失败/用量缺失不会退还预留额度。无定价时只报告请求/token 用量，不把费用未知写成零。不能利用模型输出修改预算。输入摘要过大时减少控件与应用列表，应用按请求次数轮换展示，最终启动仍校验完整实时清单；不存在静态 App 白名单。

仅在你已确认当前端点和模型实际支持图像输入时设置 `VISION=true`；请求将附带真实 PNG 的 `image_url`。图像预算预留 4096 token，需与提供商的 low-detail 图像计量相容；未核实的提供商继续使用默认 XML 模式。API 拒绝图像时不会冒充视觉成功。本轮真实 DeepSeek 请求使用 XML/文本模式，未验证该模型的视觉能力。

## 操作和安全边界

允许实时清单中的固定 MAIN/LAUNCHER 启动、当前控件绑定的普通导航 tap、可滚动容器中的垂直 swipe、back、受限搜索和 1–120 秒休息。没有逐截图人工批准或 V1 批准指纹。模型仅能输出严格 JSON，不能发送坐标、任意 shell、Intent 或网络请求。

搜索先通过 tap 聚焦已识别的搜索框，下一轮 `search` 在该框内发送固定 CTRL+A，再重新观察并复核目标，输入 1–80 字符 ASCII 查询；输入后重新观察，提交前再读取 XML，核对相同页面、唯一搜索编辑框、焦点和最终文字完全等于查询，才发送回车。支持 EditText 和标准 AutoCompleteTextView/SearchAutoComplete。XML 的提示文字保留原值，不当成已验证空文本。仅替换当前搜索框中的临时查询；焦点、角色或文字不符时跳过提交，不重放输入。中文输入尚未支持。

支付、账号写入、发消息、评论、发布、点赞、关注、收藏、安装/卸载、删除、权限和安全设置修改默认拒绝。敏感表单、密码、验证码、未知编辑框及已识别的风险对话框遮蔽；XML 不完整时也遮蔽。遮蔽观察没有伪造 PNG，不保存/发送该轮 UI 内容。截图后再次检查 XML，发现新敏感表单则丢弃采集内容。

UIAutomator 缺少 visible/editable 字段时保留未知。标签文字可以通过当前 XML 中最近的可点击父控件绑定，仍保留原始 clickable=false，并在新观察中核对父控件和标签。整屏滚动容器的手势裁剪到内部安全区域，不点击系统栏。模型只获得通过同一 ActionGuard 的 `allowed_actions`；正文摘要不作为可点击候选。聚焦编辑器所在的大面积高绘制顺序浮层存在时，被覆盖的后台控件不进入候选或正文摘要。

只有明确可识别的低风险角色可点击；自绘界面、无语义控件、未知按钮和某些 OEM 窗口可能被跳过。窗口检查使用可见证据，后台保留 surface 本身不代表遮挡；明显可见的遮挡层仍拒绝。XML 与 PNG 并非原子采集，不能证明所有隐私或副作用都能自动识别，因此真机使用专用测试账号，操作期间避免人工操作或其他自动化。

每台设备使用用户目录 `.digitallife-v2/<serial>/` 下的 OS 锁，防止同一用户的多个 V2 会话并发。V1 不参与此锁，须先手动停止 V1。输入前 `PENDING_ACTION.json` 和 SQLite/JSONL 审计落盘；输入不确定、进程被终止或动作后观察失败均保留屏障，后续 Run 不会重播。Stop 的 `--emergency` / 脚本 `-Emergency` 会额外保存设备级 `EMERGENCY_STOP`，新会话不会清除。若遇屏障，先检查其指向的会话和实际设备，再由人工处理标记；不要无条件删除标记后重试。

## 数据与验收

私有数据默认位于忽略提交的 `v2/data/`：单份 `life.sqlite3`、会话 `events.jsonl`、原子更新的 `status.json`、PNG/XML。状态包含前台、最近动作、分发/拒绝次数、用量、错误、下一次唤醒及约两秒的心跳。`stale=true` 表示心跳超过 20 秒，不能据此认定进程已正常结束。源码不附带真实截图、设备标识、密钥或数据库。

事件区分 `observed`、`pending`、`action_dispatched`、`changed/unchanged`、`blocked`、`unknown`。哈希变化只代表页面数据变化；所有业务 `achieved` 留空。时长到期使用 `completed_duration`，不能据此宣称探索成功。M1 只保留最近五条即时上下文和简短稳定人格提示；跨会话记忆、兴趣演化、V1 历史导入、恢复、一小时与二十四小时属于后续 M2/M3。

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

测试仅用虚拟手机、临时 SQLite、模拟 ADB 返回和 loopback HTTP 服务；不会访问真实 ADB、DPAPI 或提供商。交付验证详情见 [DELIVERY.md](DELIVERY.md)。

已授权的定向真机专项可从 `v2/` 运行：

```powershell
python -m acceptance.search_and_stop --session data/sessions/find-x6-v2-search-stop-new --query settings
# 在另一终端检查该会话 READY.json 和实际 PNG/XML 结果，然后在会话仍运行时执行：
python -m digitallife_v2 stop --session data/sessions/find-x6-v2-search-stop-new
```

这是仓库内的脚本驱动验收，复用生产 SessionLoop/PhoneController，不调用模型 API，不代表新的自主模型测试。默认使用实时清单中的 LibChecker；需要已安装且已解锁的绑定测试设备。会话有三分钟硬期限，只有搜索 READY 且正式 Stop 生效后专项入口才返回成功；到期本身不算通过。
