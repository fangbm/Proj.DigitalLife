# M3 a5 增量交付

后续 a6 已通过手机播放音频真机验收，M3 最小验收已达标，见 [a6 交付记录](M3-AUDIO-DELIVERY.md)。以下保留 a5 当时的实现和证据边界。

日期：2026-10-27。版本 `2.0.0a5`，分支 `codex/v2-m3`，基于首个切片 `9c619dc3d19069ba27d299718e701b0c015ec910`。a4 历史证据保留于 [M3-DELIVERY.md](M3-DELIVERY.md)。长文纯文本读取及 QQ/主微信真实来信接收已通过，手机播放音频捕获仍待真机验收。

## 长文：真机文本路径已验收

`read_article` 优先读取经哈希核对的原始 XML 正文；导航控件仍使用 300 字摘要，正文独立保留完整节点文字。保存来源时间、观察/控件、字符偏移与 XML 哈希，按文档及来源分段去重。每次最多三批，正文只以文本发送给模型，不附截图。失败保留未处理段落，再次阅读仅处理 pending；重复页不产生模型请求。处理段落不等于证明理解或全文读完。

实机材料来自用户提供 V2.2 指南的有限节选，在原生测试阅读器显示三页并包含跨页重复；不是完整指南或任意网页的覆盖验收。最终记录：`data/sessions/find-x6-v2-m3-reading-text-20261027-09/READING_READY.json`。

- 终态 `READING_ACCEPTANCE_DONE`，188.2 秒、4 次 UI 输入、0 拦截。
- 3 个真实快照，30 个去重文本单元；30 已提供并获摘要、0 pending。
- 8 次真实模型请求，输入 4601 / 输出 1809 token；图片输入 0。
- 重复第三页返回 `already_read`，不新增请求；`whole_article_complete=false`。
- 文档 ID `144808862bfe66bd5fce23f5d1d51e526e64a18def4f6f149a8d7fbefe06fe92`。

尝试 07 的第三页失败，暴露脚本在中断结果中读取 progress 的错误；已修复 finally 保存和中断续读，并用有未处理段落的回归覆盖。08 已通过，09 是缩短正文元数据和摘要约束后的最终复测。旧失败证据均保留。以上由脚本安排页面，实际 ADB 观察与模型请求，不是模型自主选择文章。

## 播放音频：实现与验收边界

Android MediaProjection 前台服务、指定应用 UID 的播放捕获、20 秒 PCM/WAV 上限、独立格式/RMS 检验和本地 faster-whisper 转写已实现；未使用麦克风。没有授权、静音、应用不允许或本地 ASR 不可用时记录明确缺口，模型不会获得虚构音频。会话关闭同时释放 projection 和音频内存。

复用了本机已有 faster-whisper-base 缓存；本地 ASR 环境为忽略目录 `data/asr-env`，faster-whisper 1.2.1。PyAV 19 接口变化通过标准 WAV 解码为 numpy 音频数组解决，不修改第三方库。公开 JFK 测试材料直接本地转写已输出含 country 的英文句子；这单独不证明手机播放捕获成功。

尝试 10 在授权按钮已分发后缺少稳定后观察，终态 unknown，原 pending、事件与现场复核保存在同会话。随后系统授权结果晚到且租约已失效，触发前台服务启动超时崩溃；已添加返回结果租约检查，并确保服务先履行 startForeground 生命周期再停用。复核当前已在桌面、租约与 projection 关闭，归档原 pending 后继续新范围；没有重放原点击。生产循环对明确的焦点切换仅有限重试读取，其余错误继续保留未知屏障。

后续尝试 13 中前台在微信页面之间切换，启动测试应用后仍未稳定进入预期页面；终态 `AUDIO_BUTTON_UNAVAILABLE`，1 次启动输入、2 次采集拦截、0 模型请求，无未知 pending。未继续点击、未采集到播放 WAV，不能宣称实机捕获链路通过，也没有启用持久音频配置；保留本地 ASR 单独通过与设备捕获待验收的区别。测试没有强制切回桌面，以免干扰现场操作。结束后租约和音频授权释放。

修复后重新构建、实机安装并核对字节：`find-x6-v2-m3-service-fix-install-20261027-11`，终态 `COMPANION_INSTALL_VERIFIED`。APK SHA-256 `2ebfa8fcd81c3eeee49b5160f4dea48647288fe0874582e722f919b5063d955d`。

## QQ / 主微信真实来信：已验收

用户确认测试账号为主微信，并从另一端发送两条测试短句。最终窗口 `data/sessions/find-x6-v2-m3-qq-wechat-receive-20261027-15` 在真实 NotificationListener 回调中收到 QQ 和主微信通知，均匹配本轮短句；调用真实模型分别判断，未打开聊天或发送内容。`SOCIAL_READY.json` 与 `events.jsonl`、SQLite 的入队/判断事件引用相同事件 ID。

| 来源 | 事件 ID | 模型判断 | 外发 |
|---|---|---|---|
| QQ `com.tencent.mobileqq` | `2778db389c8f8463347d2cf6a2e743ab26ba23dffcbefc8abb96a775d9c16231` | `read_only` | false |
| 主微信 `com.tencent.mm` | `06173c1c183c3c65f9efa941273fc3c30ea8699be01a5b65eec755f3aef89456` | `reply_later` | false |

终态 `SOCIAL_RECEIVE_ACCEPTANCE_DONE`，208.8 秒，2 次真实模型请求，输入 561 / 输出 152 token；0 UI 输入、0 拦截、无未知 pending。微信的稍后处理只记录队列意图，未发送回复，未证实联系人/账号身份；`context_complete=false` 与 sender identity unresolved 保留。结束后 `notification_collection_inactive=true`、`audio_projection_released=true`，没有后台自动续跑。

本轮只把匹配测试短句的新通知给模型，其他来信不送模型。`PRIMARY_PROFILE_PROOF.json` 只读核对 QQ/主微信在用户 0 已安装且 POST_NOTIFICATIONS 均允许；用户确认主微信不等于技术上验证发送方身份，应用分身并未验收。

先前窗口 12、14 均到期且没有匹配事件，原失败证据保留。权限检查发现 QQ 通知权限关闭，已在用户既有完整手机授权范围内开启并读回 true。微信主应用通知允许，应用分身关闭；用户随后确认使用主微信。最终窗口中 QQ 先通过，微信当时停在前台且 Android 没有生成通知，提示回桌面后重发，随后收到真实微信通知。未复制账号、安装分身通知桥或触碰登录，也未把本机测试通知替代 QQ/微信来信。

## 验证与范围

源码离线回归 92/92，包含正文完整尾部、跨页去重、来源隔离、哈希篡改拒绝、纯文本发送、重复零请求、中断续读、音频拒绝/来源绑定、通知短句过滤及两来源完成要求、焦点切换不重放输入。独立目录安装的 wheel 回归同样 92/92；已断言实际导入来自安装目录，15 个运行模块逐字节与源码一致。首轮从仓库根运行时被根目录历史 acceptance 包遮蔽，另有一轮未把安装路径传给子进程，导致 HTTP worker 与独立导入检查失败；统一父子进程搜索路径后复测。wheel SHA-256 `3b446697b9c8dcd6168c78be67186c40af07c0ff938beb1a7df2697133717b4d`。

数据库 schema 4，升级已有 schema 3 前一致性备份 `pre-reading-*`；保留 M2 历史、V1 数据与凭据。不导入 V1，脚本验收不训练生命记忆；现有 18 条记忆仍全部为 v2_autonomous，未新增脚本训练记录。根 README 的 V1 历史正文逐字节保持。M4/M5 外发和 M6 长期运行未提前实施，M3 尚不能据单个阅读器或播放器宣称任意 App 全覆盖。
