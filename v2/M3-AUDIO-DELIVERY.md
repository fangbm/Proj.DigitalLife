# M3 a6：音频真机验收与最小闭环完成

2026-10-27，`2.0.0a6`，分支 `codex/v2-m3`，基于 a5 与真实通知交付 `ab4aa1b`。此前长文与 QQ/主微信证据见 [a5 增量记录](M3-READING-AUDIO-DELIVERY.md)，公开视频视觉摘要见 [a4 记录](M3-DELIVERY.md)。

**按用户提供 V2.2 指南的 M3 最小验收门槛，当前已达标**：真实公开视频有带采样来源的摘要，真实 QQ/主微信通知进入本地队列并触发真实模型判断；正文跨页纯文本读取、去重/续读和手机播放音频捕获也已有实机证据。本阶段无社交发送。此结论不代表任意 App、视频平台评论回复、群 @、应用分身、受保护媒体或全文覆盖均已验收。M4/M5 与 M6 保持独立。

## 音频最终真机证据

会话 `data/sessions/find-x6-v2-m3-audio-capture-20261027-17`，终态 `AUDIO_ACCEPTANCE_DONE`。操作为脚本安排、生产设备控制器实际执行；系统授权、播放捕获、本地 ASR 与模型总结均真实发生，不标为模型自主选择的生命经历。

- 142.2 秒，3 次 UI 输入、0 拦截、1 次真实 DeepSeek 请求；输入 4353 / 输出 213 token。
- 实机系统授权页确认后，播放测试语音；只捕获 Companion UID 的 MEDIA/GAME 音频，没有麦克风读取或兜底。
- WAV 为 PCM16、单声道、16000Hz，20.0 秒、640044 字节；独立检验 RMS 4661.27，SHA-256 `5668e18d1ce522688666965a60564ffe16634186d5ba6720a66d9040a7d788c9`。
- 本地 `faster-whisper-base` CPU 转写为英文演讲片段，识别出 country 相关句子；模型收到实际自动转写与三张来源截图，生成摘要并明确标注转写可能有误，没有把文字输入称作直接听音。
- `AUDIO_READY.json` 关联实际捕获 ID、WAV 哈希、转写段、模型摘要；`audio_capture_started` 同时记录源观察与 UID。
- 最终新观察确认为桌面；`notification_collection_inactive=true`、`audio_projection_released=true`；无未解决 pending，无自动续跑。

公开语音素材来源为 OpenAI Whisper 仓库的 `tests/jfk.flac`，本地 `data/m3-audio/source.json` 记录下载来源、原始与转换后哈希；手机采集 WAV 的哈希独立于原始文件。示例播放器循环播放，因此实际捕获含重复及头尾截断，不能声称完整演讲覆盖或每字语义无误。此前公开视频已验证实际视觉，本次语音界面没有字幕，字幕缺失如实报告。

验证成功后写入忽略的、绑定 serial 的 `data/audio.private.json`，保存本机已验证 ASR Python 与缓存模型路径；正常运行不自动下载模型。每个新的手机采集会话仍需有效的 Android 系统投屏授权；无授权、受保护播放或不可用时返回 audio_unavailable，不使用麦克风。没有常驻投屏或后台 AI。

## 实机发现与修复

尝试 16 在授权请求按钮已分发后停止于 `FOREGROUND_UNVERIFIED`，保留 unknown 与原 pending。Android 16/ColorOS 的授权弹窗只把 `com.android.systemui` 作为焦点窗口标题，原识别只接受完整 Activity，无法观察预期授权页。

先独立采集只读窗口元数据与截图，确认实际 DigitalLife 授权页，再修复生产识别：完整 Activity 继续原样记录；完整 UI 模式下的包名窗口必须有唯一焦点 token、同包窗口归属、owner UID 与明确显示证据，记录为 `package/@window:token`，不伪造 Activity。窗口检查按实际 token 定位，并兼容 Android 16 的 isOnScreen/isVisible 无括号语法。受限模式保留原约束。

修复后独立重新观察确认弹窗，保存 `PENDING_REVIEW.json` 并核对归档原 pending；没有重放原授权请求。新会话通过显式专项 `-ResumeProjection` 从已复核的现存授权页继续，仍重新观察、绑定控件后只点击一次“开始”。这仅是验收入口；正常 Run 不自动恢复或重放未知动作。

## 验证

最终源码与独立安装 wheel 的回归均为 **94/94 通过**，安装测试明确断言导入目录与 a6 版本，并向 HTTP 子进程传递同一安装路径；本地日志为 `data/m3-a6-source-tests.log` 与 `data/m3-a6-wheel-tests.log`。新增用例覆盖焦点 token/包归属/显示状态不匹配拒绝、受限模式拒绝、保留 Activity 身份，以及新版可见覆盖窗口仍拦截。

wheel SHA-256 `03f73e0586ff99516be292477f49be9f6af382efb27f35d61a331f2765b03605`，15 个运行模块逐字节与源码一致。Companion Java 本轮未再改动，沿用已实机核对的 APK `2ebfa8fcd81c3eeee49b5160f4dea48647288fe0874582e722f919b5063d955d`。

所有截图/XML、通知预览、WAV、ASR 模型/环境、设备绑定和密钥保持本地忽略；只提交实现、测试与不含个人消息正文的结果记录。V1 未导入或修改，脚本验收不训练生命记忆。
