# M3 首个真实切片交付

日期：2026-10-27。版本 `2.0.0a4`，分支 `codex/v2-m3`，基于 M2 的 `d6b2b4f3c47c4f947a278110b538fce313517d68`。使用方式见 [M3.md](M3.md)。

**本次完成 M3 的最小真实切片，M3 整体仍在进行中。** 已连接有来源的视频/文章采样、真实视觉模型和只读通知事件；尚未宣称任意视频 App、长文章连续阅读、QQ/微信真实来信或音频理解全部验收。M1/M2 沿用；不导入 V1，完整手机 UI 模式保留。M4/M5 社交发送与 M6 长时间运行没有提前展开。

## 已交付能力

- 官方 DeepSeek Flash 默认视觉，通过独立真实图片挑战；其他模型仍显式配置。
- `watch_video` / `read_article` 有界采样；保存观察 ID、时间、图像/XML 哈希、可见文字、字幕标记和仅覆盖实际样本的摘要。当前无音频采集。
- schema 3 内容表与事件队列，一次性一致性备份升级 M2。只有生产自主会话形成生命记忆；脚本与 fixture 不训练。
- 最小 Android NotificationListenerService、本机认证通道、会话租期、来源过滤、持久去重、撤销来源后不再检索旧事件、过期预览清除。
- 独立短模型请求判断通知，只读/忽略/延后/恢复等意图均不发送聊天或评论；休息期间仍可入队。原有停止与未知输入屏障有效。

## 离线与构建

源码及隔离安装后的 wheel 各通过 **80/80**。新增回归覆盖通知去重/重复文案分离、来源撤销、到期清除、事件分类及一次修正、无对外动作、样本去重与来源校验、图片篡改拒绝、迁移备份，以及日志保留字段不可被 payload 覆盖。测试使用临时数据库、虚拟手机与本机 HTTP，不联系手机或提供商。

wheel：`digitallife_v2-2.0.0a4-py3-none-any.whl`，SHA-256 `19020fb070ff68139fb9f4b51e625fe959e8c43dabc48ff5580e75363b2579fe`。安装后确认从 `v2/data/package-m3-final-smoke/` 加载模块。

Android 应用使用本地 Android 35 / build-tools 36.0.0 / JDK 17 构建并验签；开发 APK SHA-256 `995fba91631c9882bf9269485725d5812344e1d1ad17c33ba7b65fd1ced4e6f0`。设备安装的 APK 已按字节哈希核对，并实际启用系统监听。构建与安装分开，产物、开发签名密钥和配置不提交。

25 个 Python 文件通过 Python 3.11 语法解析，启动脚本通过 PowerShell 语法解析。wheel 内全部运行模块与最终源码逐字节相符。

## 真实视觉与视频

私有证据位于 `v2/data/sessions/`。

`find-x6-v2-m3-vision-20261027-02`：一次真实提供商请求，输入 266 / 输出 23 token，正确读出仅存在于 PNG 的随机六位数字，并识别红色与蓝色图形；终态 `VISION_VERIFIED`。没有手机输入，属于视觉能力挑战，不冒充手机经历。

公开视频采用 [MDN video 示例](https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/video) 的公开示例文件 `https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4`，1,128,375 字节，SHA-256 `0cd83d944a6ca7822b4a8306cecc60a36e859b041f6702c6a1ad9ead78924451`。来源与哈希写入 `source.json`；在真机 Companion 的测试播放器中静音、循环播放。不是从其他 App 的受保护流提取媒体，也没有做音频采集。

联合运行 `find-x6-v2-m3-content-event-20261027-03`：模型实际收到以下三张真机截图，形成段落 `c0b505e0f73346d895f6026fb6b63859`：

| 相对采样秒数 | observation_id |
|---:|---|
| 0.00 | `b3ff251a3c5048caa5c4237a9f03d939` |
| 13.03 | `cd820be285764c4cabe0f386c25ab61c` |
| 26.37 | `ecce0b60e2374db1bc5ed325c9bb8c24` |

模型指出红色花朵、黄色花蕊、紫边叶片及样本间开放程度变化，引用真实来源并明确没有音频/字幕。已人工查看实际截图核对主要画面。正文曾出现一次时间数字误差，可信时间以结构化来源为准；引用校验并不是语义正确性的形式证明。也不据此宣称模型看完、理解了完整视频。

播放与通知时机由脚本安排，摘要由真实模型生成，生产 PhoneController/SessionLoop 真实执行与观察；因此记为 `live_scripted`，不是模型自主选择播放或新的自主生命经历。正常 Run 已提供这些活动入口，后续仍需更广泛的自主选择验收。

## 通知接入及修复复测

联合流程中的通知来自 Companion 实际发布的 Android 系统通知，经真实 NotificationListenerService 回调进入 Python SQLite。没有使用伪造的 QQ/微信消息或 fixture 冒充来信，账号和会话身份仍明确 unresolved。

保留各次尝试，未重写失败终态：

| 会话末尾 | 秒数 | 手机输入 | 拒绝 | 提供商请求 | 结果 |
|---|---:|---:|---:|---:|---|
| `m3-content-event-20261027-01` | 87.7 | 2 | 0 | 0 | 脚本未转交摘要方法，AttributeError；已修复 |
| `m3-content-event-20261027-02` | 116.1 | 2 | 0 | 2 | 视频摘要成功，模型漏填事件字段 |
| `m3-content-event-20261027-03` | 182.2 | 2 | 4 | 10 | 联合流程成功，但混合动作/事件输出多次修正 |
| `m3-event-focused-20261027-04` | 34.7 | 0 | 0 | 1 | 改为独立短分类后成功，无修正 |

最终通知专项的事件 ID `a6080d3f983d5733589641587569dfdc18107adfcbd2759a5648e68422be98bc` 同时出现在 `inbound_received`、实际模型输出及 `inbound_decision`；模型选择 `ignore`，理由是普通本机测试通知无需回复。只用一次请求，输入 295 / 输出 61 token，终态 `M3_EVENT_ACCEPTANCE_DONE`；`outbound_dispatched=false`。有早期失败遗留的本应用测试通知时，仅由测试脚本显式标记旧测试为忽略，原记录保留，不清理真实其他来源事件。

上述四段无 unknown、未遗留待核对输入，结束状态均确认 `notification_collection_inactive=true`。生命周期真机专项另证实：结束后采集未激活、错误令牌被拒绝、一秒租期到期后关闭且队列为空；证据 `BRIDGE_LIFECYCLE_PROOF.json`。令牌和通知正文未写入此报告。

开发中还修复了：Windows ADB 标准输入截断二进制媒体（改为 push 后复制并校验哈希）；OEM 安装确认必须在真实当前控件上分发；增量安装的本机子进程占用（专项改非增量）；APK 路径校验补齐 Android 使用的 `~` 字符；内容事件 payload 曾覆盖 JSONL 的 kind 字段（现保留字段由日志器最终写入）。未把安装超时当成安装成功。

最终只读核查 SQLite `quick_check=ok`、schema 3；原有生命记忆仍为 12 条 episode 与 6 条 blocked，全部 `v2_autonomous`，未混入视觉挑战、脚本视频或测试通知。

验收结束后，`find-x6-v2-m3-desktop-cleanup-20261027-05` 用生产闭环执行 HOME 并取得独立后观察，终态 `DESKTOP_CLEANUP_DONE`，测试播放器已离开前台；没有留下自动运行任务。

## 尚未验收的范围

目前的视频证据来自真机测试播放器中的公开视频；还没有证明任意视频平台的播放/字幕/分享识别均可用。文章当前是可见页提取与去重，再由模型选择普通滑动，尚未验收跨长文的稳定阅读位置、全篇去重和摘要质量。没有音频采集、没有可靠联系人/群身份适配、没有 QQ/微信或评论通知的真实来信专项。社交外发与长时间运行继续留给后续阶段。
