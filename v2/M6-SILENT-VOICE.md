# M6 静默语音通道与角色 TTS

后续进展：[a21 QQ 格式选择实测](M6-REPLY-FORMATS.md)、[a23 混合音色、虚拟声卡及微信原生自测](M6-WECHAT-MIXED-DIAGNOSTIC.md)。下文保留 a18 阶段的实现与验收记录。

2026-10-30，`2.0.0a18`，分支 `codex/v2-m6`。本切片将 QQ 原生语音发送接到本机 NapCat，增加本地 GPT-SoVITS 角色音色适配器。**M6 仍未整体验收完成。**

## 当前边界

- QQ 新入口只发送 `record` 音频消息：合成 WAV → 本机认证 HTTP → NapCat 转换/发送原生语音。不播放音频，不申请麦克风，不回退到音频附件或声学录音。
- 复用 V2 社交授权、已有联系人/群范围、频率和同一持久发件箱。正常接收和文字社交仍使用原手机路径；本切片提供操作员入口，尚未把语音格式选择加入 LifeBrain 的自主决策。
- 发送前保存 `voice_ready`，发送请求前保存 `dispatch_attempted`。同一意图不重放；请求中断、超时或读回失败记 `delivery_unknown`。NapCat 的原生语音消息读回只证明客户端有该消息，不证明对方已听，也不单独证明音频内容正确。
- 只连接 `127.0.0.1` / `::1` 的本地端口，不使用代理、重定向、远程音频 URL或自动重试。QQ 使用既有绑定账号；首次登录需要手机扫码。
- 微信静默通道未接入。本轮没有静默发送微信语音；[a16 的微信自播自录](M6-SPEECH-DELIVERY.md) 保留为历史试验和显式校准入口，不是新通道的失败回退。

## 实机与音色来源

已在本机启动 NapCat `4.18.41`。原电脑 QQ `9.9.16` 在用户扫码后，被腾讯以版本过旧拒绝登录，未发送消息。随后在忽略的 `data/voice-runtime/` 下独立解压腾讯签名的 QQ `9.9.33.52230`，不修改原 QQ 安装。安装包 SHA-256 与 [winget 清单](https://github.com/microsoft/winget-pkgs/blob/master/manifests/t/Tencent/QQ/NT/9.9.33.52230/Tencent.QQ.NT.installer.yaml) 一致：`b25c0d3ce9df764074a9118d0ded927e1b2d7ebf60e306112e8df18a040ec492`。新版二维码已由测试手机识别，但 QQ 官方授权页明确拒绝相册图片识别/长按授权，要求使用摄像头实际扫码。该轮没有登录、没有发送消息；临时二维码图片已清理，手机回到桌面，未登录桥已停止。随后用户完成摄像头扫码并在手机授权，新版 QQ 已实际登录。

DS娘没有在本项目中被当作唯一的官方音色。查到的一个具体来源是 LingChat 的 DeepSeek娘演示引用的“小酒狐” GPT-SoVITS 模型：[模型作者视频](https://www.bilibili.com/video/BV1ozLE6pE3k/)、[LingChat 语音接入文档](https://slimeboyowo.github.io/LingBlog/blog/projects/ling-chat/develop/tts_guide)。作者旧夸克链接已失效；置顶评论补档为 `https://pan.baidu.com/s/1D4u8i7h18yJmf2iNBXKhBg`，提取码 `bmww`。浏览器安全策略禁止代理访问该补档站点，用户下载的精简包已在本机找到并解压，使用其中 v2Pro 权重和 idle50 日语参考录音；本仓库不重新分发角色权重或参考音频。

`gpt_sovits_local` 会请求本地官方 `api_v2.py` 的非流式 WAV，固定本地角色模型、参考音频及逐字文本。生成后验证 WAV 格式、时长、非静音并绑定哈希；配置撤销或改变则丢弃结果。错误不会切回系统声音。当前私有配置已选择小酒狐角色音色；真实中文合成与完整正文转写核验已通过，试听样本留在本地。

本机创建独立 Python 3.12 环境 `data/tts-env`，安装 PyTorch `2.9.1+cu128`；GPT-SoVITS 上游源码位于独立忽略目录，版本 `b4570b8`。通用中文特征模型复用现有本地文件，角色模型来自用户下载包。Windows 日语前端使用 `pyopenjtalk-plus` 的同名导入接口，不修改其他项目环境。Windows 参考音频解码使用 TorchCodec 0.9.1 与 shared FFmpeg 8.1.2，另补齐 resampy、opencc-python-reimplemented、离线 NLTK 英语词典/JSON 词性模型、fast_langdetect 和 G2PW 模型。启动脚本预检这些资源并实际解码参考音频，不向其他项目环境安装依赖。G2PW 的 ONNX 前端回退 CPU，主要 Torch 合成使用 CUDA。

## 使用

已有本地 NapCat 服务时，在忽略的 `data/voice.private.json` 配置 `version:1`、绑定设备 `serial`、`enabled:true`、`provider:"napcat"`、`endpoint:"http://127.0.0.1:31071"` 和 `access_token`。脚本也会创建本地认证配置：

```powershell
cd D:\codex\Proj.DigitalLife\v2
# Runtime 内先解压官方 NapCat.Shell.zip；QQPackagePath 指定该 QQ 的实际 package.json。
.\start-voice-bridge.ps1 -QQPath "绝对路径\QQ.exe" -QQPackagePath "绝对路径\resources\app\package.json"
.\start.ps1 -Command VoiceStatus
# 仅生成文件，不播放或发送；完整资产路径在私有 VOICE_RESULT.json。
.\start.ps1 -Command VoiceSynthesize -Session data/sessions/NEW-SYNTH -VoiceText '你好，这是一条语音测试消息。'
# 发给自己。意图 ID 须稳定；不确定发送绝不换 ID 重试。
.\start.ps1 -Command VoiceSend -Session data/sessions/NEW-SELF-SEND -VoiceTarget self -VoiceIntent unique-source-id -VoiceText '你好，这是一条静默发送测试。'
```

`VoiceTarget` 也可填已加入的 QQ 联系人号/群号，群使用 `-VoiceTargetKind group`。`VoiceRef` 可替代 `VoiceText` 引用未过期的合成资产。`VoiceStatus` 不创建发件箱、不发送消息；这些入口不解密模型 API Key。转换/发送最多四分钟，可用 Ctrl+C 或原有 Stop 标记停止，取消后的不确定外发仍留在持久发件箱中。

角色模型下载后，在 PowerShell 7 中指定权重和参考音频（`FFmpegBin` 可指定兼容的 shared FFmpeg 目录）：

```powershell
.\start-character-tts.ps1 -GPTWeights "绝对路径\角色.ckpt" -SoVITSWeights "绝对路径\角色.pth" -ReferenceAudio "绝对路径\idle50.wav" -ReferenceText '与参考录音逐字一致的日语文本' -ReferenceLanguage ja
.\start.ps1 -Command VoiceSynthesize -Session data/sessions/NEW-CHARACTER-VOICE -VoiceText '你好，我是数字生命。今天想和你聊些什么呢？'
```

脚本先保留原私有 TTS 配置，再选择本地角色服务，默认整段切分 cut0、串行推理、关闭 split_bucket、种子 1234；只有实际生成和试听/转写才算音色验收。服务默认仅监听本机，模型/API 错误不触发任何手机播放。TTS 服务是独立进程；退出语音生成命令不会自动卸载模型。个人 QQ 桥属于非官方客户端集成，需要承担版本兼容和账号风控成本。

接口依据：[NapCat 消息格式](https://napneko.github.io/develop/msg)、[NapCat 本地网络配置](https://napneko.github.io/config/basic)、[GPT-SoVITS api_v2](https://github.com/RVC-Boss/GPT-SoVITS/blob/main/api_v2.py)。

## 验证

新增本机 HTTP 集成回归验证实际 JSON/语音文件传输、认证头、群/私聊路由、错误消息类型、发送中断和取消后的单次发送约束，以及 GPT-SoVITS 分块 HTTP WAV、错误响应与配置范围；这些是本地替身测试，不能冒充真实 QQ 或真实角色模型验收。原有回归与 wheel/source 一致性在交付时另行记录。

源码和独立安装 wheel 的 318 项回归均通过，34 个运行模块与源码逐字节相同。Wheel 为 `digitallife_v2-2.0.0a18-py3-none-any.whl`，SHA-256：`4de4c5723cfb16e056c5462eb19035efb76ea260a9bf0ae95a8a26e511776d65`。历史失败与修复日志保留在私有 `data/`。

通过正式 `VoiceSynthesize` 入口，用角色权重生成 6.02 秒、32 kHz、单声道 WAV，文本为「你好呀，我是数字生命。现在开始，我会用这个新的声音和你聊天。」本地 ASR 转写全文经繁简、标点归一化后完全一致。证据为 `data/voice-runtime/CHARACTER_VOICE_ACCEPTANCE.json`，试听文件为 `data/speech/samples/winefox-demo-20261030.wav`。本轮没有播放、没有用麦克风，也没有发给任何联系人。

最初并行/分句参数的中文样本转写不完整，未计为通过；调整成上述默认参数后才通过。纯英文单句在常规 ASR 静音过滤下漏掉开头 Hello，关闭过滤后的诊断能完整识别；中英混合样本仍未通过正文核验，不能宣称混合语音验收完成。中英混合问题保留为后续验收项。

TTS 请求取消会关闭本地连接；独立模型服务可能继续完成当前推理，服务生命周期与每次转换分开管理。正常自主语音选择、真人入站语音、微信静默通道尚未完成，M6 整体仍在开发。

## 2026-10-30 QQ 静默语音实测

用户完成摄像头扫码登录后，`VoiceStatus` 读回账号与现有绑定一致。正式 `VoiceSend` 入口只向登录账号自己发送一条小酒狐中文语音，结果 `sent_observed`；QQ `get_msg` 读回发送者、同一消息 ID 和原生 `record` 类型。随后使用 QQ `get_record` 将该语音读回 WAV，并由本地 ASR 转写；正文经繁简和标点归一化后与「你好呀，我是数字生命。现在开始，我会用这个新的声音和你聊天。」全文一致。

私有证据为 `data/voice-runtime/QQ_NATIVE_VOICE_ACCEPTANCE.json`，发送会话为 `data/sessions/m6-qq-silent-self-character-20261030-01`。这次实际只发给自己一条，没有播放扬声器、使用麦克风或向其他联系人/群发消息；读回证明原生语音内容可解码，不代表其他收件人已听。未额外发送第二条，也没有启动自主社交循环。QQ 静默自发与新中文角色音色已实测通过，M6 其他未完成项保持上述边界。
