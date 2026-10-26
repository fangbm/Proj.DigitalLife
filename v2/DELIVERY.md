# V2 M1 交付记录

2026-10-27，经用户明确授权启动测试与自行修复，在绑定 Find X6 上完成十分钟真实会话及修复后的五分钟复测。M1 核心闭环已有真机证据：模型自主选择非 Settings App、真实分发普通 tap/swipe、动作后独立采集 PNG/XML。V1 运行源码、数据库、历史证据和凭据保持原样，M2/M3 尚未展开。

## 真实会话结果

| 会话 | 终态 / 时长 | 模型请求 | 真实分发 | 候选拦截 | 未知结果 |
|---|---|---:|---|---:|---:|
| `find-x6-v2-live-10m-20261027-01`，初版 `32d6d3a` | `completed_duration / GLOBAL_DEADLINE`，600 秒 | 29 | 14：7 open_app、5 tap、2 back | 13 | 0 |
| `find-x6-v2-live-fix-5m-20261027-02`，本次修复源码 | `completed_duration / GLOBAL_DEADLINE`，300 秒 | 9 | 9：5 tap、3 swipe、1 back | 0 | 0 |

第一轮模型选择了酷安、IT之家、维基百科、必应和便签，发出七次固定 Launcher 启动；不把启动命令退出码当成目标包确实出现的证明，例如维基百科启动后曾跳到应用商店。普通 tap 已打开 IT之家搜索浮层，Activity 名称保持 MainActivity；此结果通过真实截图和搜索编辑器 XML 确认。

第二轮不是预设动作脚本：模型自主选择搜索入口、返回、资讯列表滚动、热榜及后续导航。操作级独立证据包括：

- swipe `48098ad663c0421c89a38299ac56aa25`：前后 XML 中四个相同的长文本节点上移超过 100 像素；PNG 也确认资讯列表向下浏览，不能只用图片哈希变化解释为成功。
- tap `c83ceb4624414270a9b343e5fb95a6f7`：真实点击热榜文字，后续 XML 中热榜 `selected=false→true`、最新 `true→false`，并出现日榜/周榜/月榜控件。
- 两轮每次已分发动作均有独立后观察，未出现 unknown 或待重播动作；结束后设备锁已释放、无遗留 PENDING_ACTION/EMERGENCY_STOP，V2 SQLite `quick_check=ok`，无残留 V2 Python 进程。

上述两轮自主会话的 DeepSeek 实际用量合计：38 次请求，163348 输入 token、1861 输出 token。使用 XML/文本模式，`vision_available=false`；未验证视觉能力。金额未配置可靠计价，保持未知。两轮自主会话由期限正常停止；随后补齐了下节的搜索提交和活动会话 Stop 定向验收。没有一小时或二十四小时会话。

## 搜索与主动停止补验收

本节为用户明确授权的定向真机测试，复用生产 SessionLoop、ActionGuard 和 PhoneController，决策由 `acceptance/search_and_stop.py` 脚本提供，不调用模型 API，不混作额外自主性证据。

| 会话 | 结果 | 真实分发 |
|---|---|---:|
| `find-x6-v2-search-stop-20261027-03` | 69.8 秒后以 `ACCEPTANCE_NONEMPTY_SEARCH_OR_HINT` 停止，暴露标准搜索框兼容问题 | 2：open_app、tap |
| `find-x6-v2-search-stop-fix-20261027-04` | 83.4 秒内完成搜索并响应正式 Stop，终态 `stopped / STOP_REQUESTED` | 4：open_app、search_select、search、search_submit |

第一轮看到 LibChecker 使用 `android.widget.AutoCompleteTextView`，XML 的 `text` 为提示文字“搜索…”。旧版只识别 EditText 且要求空文本，导致正常搜索不可用。修复后识别标准 AutoCompleteTextView/SearchAutoComplete，保持原始 XML 提示文字，改用搜索框内固定 CTRL+A → 重观察/复核 → 输入 → 重观察/复核最终文字 → ENTER。每次分发单独持久记录、计入预算；焦点、角色或文字不符合时不会提交，不会自动重放。

第二轮的真实证据：

- query 为 `settings`。`search_select`、`search`、`search_submit` 均有真实分发和动作后观察。输入后的控件文字精确为 `settings`，随后才发送回车。
- READY 观察 `d1324e965e5b47798be547923fa11287` 的 PNG 明确呈现筛选结果；XML 包含 `com.android.settings`、`com.oplus.wirelesssettings`、`com.android.providers.settings` 等匹配项。结果判断结合真实 UI，未把单纯请求或哈希变化当成成功。
- 在会话仍为 `running`、正处于 120 秒 rest 等待、无 pending 输入时，从另一个进程执行正式 `python -m digitallife_v2 stop`。命令退出码为 0，约 **0.448 秒**返回 `STOP_REQUESTED`。
- 停止前分发数为 4，停止两秒后仍为 4，无后续输入；设备锁可重新获得，无遗留 pending/emergency 标记、无残留测试进程，数据库 `quick_check=ok`。

私有的 `READY.json`、`STOP_PROOF.json`、原始 PNG/XML 和 JSONL 位于上述会话目录，全部忽略 Git。两次定向测试没有模型 API 请求，均无 unknown。M1 最小功能与核心实机验收已可结项；Stop 在模型/ADB 请求中途、紧急停止持久标记、断连和强制关闭仍主要依赖离线故障测试，未宣称这些情景全部完成真机演练。

## 已修复问题

第一轮拦截为 `UNKNOWN_ACTION_ROLE` 10 次、`TARGET_NOT_USABLE` 2 次、`MODEL_REPAIR_EXHAUSTED` 1 次。主要问题是模型把仅用于阅读的标签 ID 当成可点击候选，以及搜索浮层和后台首页同存于 XML，旧的前段截取会展示后台控件。旧日志未保存无效模型正文，不能确定那次格式修复耗尽的具体字段原因。

修复使低风险 TextView 可绑定当前 XML 中最近的可点击父控件，并在重观察中复核；未知 visible/editable 不补 true。整屏滚动容器仍须存在，手势裁剪到其内部安全区域并核对实际路径遮挡。聚焦编辑器、区域覆盖和较高 sibling drawing-order 同时支持浮层识别时，后台节点标记为 covered，拒绝输入并从模型正文中排除。模型动作摘要由同一个 ActionGuard 生成 `allowed_actions`，不再把未知按钮和后台正文当成可操作候选。

支付、账户写入、发布、评论、点赞/关注/收藏、安装/卸载、删除、授权、密码/验证码和未知动作仍拒绝。搜索仅在已识别且聚焦的搜索编辑框内选中替换临时查询，最终文字完全匹配后才提交。实测改进只覆盖上述设备和页面，不代表所有 App 都能操作或所有隐私都可自动识别。

## 离线与安装包验证

Windows / Python 3.13.5 / Pillow 11.3.0：源码 **37/37 通过**；wheel 安装到隔离目录后，同一套测试 **37/37 通过**。包含父控件绑定及变更拒绝、整屏滚动裁剪、浮层后台隔离、准入候选、AutoComplete 搜索兼容、选中后焦点变化拒绝输入、错误或追加文字拒绝回车等回归。原固定命令、身份、风险/隐私、预算、锁、未知分发、停止与本地 HTTP 模拟测试继续通过。未运行 V1 全量测试。

当前本地 `v2/dist/digitallife_v2-2.0.0a1-py3-none-any.whl` SHA256 为 `8c614b7d8dc4263e2032b4c0d0e0fd5cbf4361d73b5b677c59ef5efda21ff99e`。先前的 wheel 已被本次搜索兼容修复构建替换；未创建公开发行。

真实截图/XML、原始 JSONL、状态和数据库留在忽略提交的 `v2/data/sessions/<会话名>/` 与 `v2/data/life.sqlite3`。离线样例 `v2/data/offline-delivery/` 仍是虚拟设备材料，不能混作真实证据。公开源码与报告不含设备序列号、凭据、截图或原始 UI 树。

修复继续在 `codex/v2-m1`，基线为原本地主干 `0faca7f`，保留此前已有的 19 个提交；不修改远端 main、不合并、不自动续跑。
