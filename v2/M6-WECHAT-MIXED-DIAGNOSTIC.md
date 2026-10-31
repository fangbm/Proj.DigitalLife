# M6 微信静默音频与混合音色诊断

2026-10-31，当前 `2.0.0a23`。Windows 微信升级到 `4.1.15.56` 后已能录制，并向文件传输助手发送一条原生语音，微信官方转写与预期全文相符。6.02 秒源音被操作间延迟拉成 60 秒，时长未通过。新增固定时长执行器已完成离线回归和真实音频资源恢复验证，其 GUI 发送路径尚未实际运行；正常微信自动运行接入未完成，M6 未整体完成。QQ 验收见 [格式选择](M6-REPLY-FORMATS.md)。

## 微信虚拟声卡

当前 Windows 微信自测范围仅为用户指定的文件传输助手。现有 VB-Audio Virtual Cable 设备报告驱动 `1.0.3.5`、日期 2014-09-02。本轮未升级、卸载驱动或重启设备。

此前多个临时探针的回录反复出现首段声音并夹有静音，改缓冲和接口都未解决。最终定位到探针的 `np.arange(...)*source_rate/target_rate`：本机 TTS 环境的 NumPy 默认索引为 Windows `int32`，乘采样率后溢出。32→48 kHz 时约 1.4 秒后就会出现负索引，插值把它变成首样本或早期片段。保存的原始六段测试音正确，送入设备前的额外重采样却已损坏。不能据这些探针把问题归为旧驱动或音频库故障。

新工具使用 Python 浮点位置做重采样，不依赖 NumPy 默认整数宽度；测试覆盖六秒上采样和八秒下采样的尾部。已检查正式 `speech.normalize_wav`，它原本使用 Python 浮点位置，没有这个溢出问题。早期错误探针及失败记录保留，不覆写为成功。

第一次 FFmpeg DirectShow 录音被 Windows 的麦克风权限弹窗阻挡，未生成录音；该超时单独保留。用户手动允许后，DirectShow 完整录到 300→1300 Hz 六段原始测试音。修正索引后，中文语音也能完整回录，本地转写与原文在标点归一化后相同。

正式工具再次用 6.02 秒中文样本和 4.54 秒中英混合样本进行实际回录，整个源信号均有对应窗口，波形相关度分别为 `0.993448`、`0.990980`，两次都通过 `0.98` 阈值，无播放欠载。相似度只在容纳整个源信号的窗口中计算，短前缀相同不能算通过。它证明本机虚拟音频传输，不证明微信已发送或收件人已听。

旧版微信在临时默认输入设为虚拟设备后，点击原生语音提示「不能录制音频，请稍后再试」。用户随后确认「手动点击也报错」，因此故障不限于自动点击方式。临时切换的三个默认麦克风角色均已读回确认恢复，设置窗口已关闭，没有修改微信设置或发出消息。这是升级前的失败记录；升级后的录制和单次发送见下文，正常运行接入仍未完成。

新探针不改变默认音频设备，只使用唯一命名的虚拟播放和捕获端；端口缺失、重名或有其他活动音频会失败，不回退到实体设备。只暂时解除虚拟播放端静音，退出恢复原值，终止自己的捕获进程；不操作任何微信 UI。采样率和缓冲排查参考 [VB-Audio 官方手册](https://vb-audio.com/Cable/VBCABLE_ReferenceManual.pdf)，没有修改驱动持久参数。

## 可重复的回录入口

需要已安装的 VB-CABLE、含 `numpy/sounddevice/pycaw` 的本地音频 Python 环境，以及本地 FFmpeg。仓库不会自动安装驱动或处理 Windows 隐私授权。下面使用本机已有 TTS 环境，在 V2 目录运行，每次换一个新的会话目录：

```powershell
.\start.ps1 -Command VirtualCableProbe -Python .\data\tts-env\Scripts\python.exe `
  -VoiceWav data/speech/samples/winefox-mixed-20261031.wav `
  -FFmpegExe data/voice-runtime/ffmpeg-8.1.2/ffmpeg-8.1.2-full_build-shared/bin/ffmpeg.exe `
  -Session data/sessions/NEW-VIRTUAL-CABLE
```

它不连接手机或模型、不解密密钥、不发送消息。读取最长 20 秒的 PCM16 WAV，保存实际回录、捕获日志和 `ROUNDTRIP.json`；只有完整信号匹配且虚拟静音恢复才返回成功。Windows 权限窗口未处理时会有界失败并关闭进程，须用户手动操作，不会代点或偷偷换输入。

通过证据：`data/sessions/m6-virtual-cable-formal-20261031-01/ROUNDTRIP.json`、`data/sessions/m6-virtual-cable-mixed-formal-20261031-02/ROUNDTRIP.json`。完整中文转写为 `data/voice-runtime/CABLE_DSHOW_SPEECH_FIXED_CROSSCHECK.json`。此前溢出与权限失败在 `data/voice-runtime/` 中分别保留。全部音频流已关闭，虚拟播放端已恢复原先静音，没有本轮遗留的 FFmpeg 录音进程。

源码和独立安装 wheel 各 343 项回归通过，35 个运行模块逐字节一致。新回归覆盖超过 Windows 32 位乘法边界的长音频尾部，以及缺失/重名虚拟端点时拒绝选择实体默认设备。Wheel：`digitallife_v2-2.0.0a22-py3-none-any.whl`，SHA-256：`f7693e908a845f2848ba2333c8c6196ad839a6df39196d9ebce3ed75d2d7839b`。这些回归不替代上面的实际回录，也不代表微信发送已通过。

## 小酒狐中英混合样本

通过正式 `VoiceSynthesize` 生成文本「你好，这是语音测试。Hello, this is a voice test.」的 4.54 秒、32 kHz 单声道 WAV，使用既有角色权重、`text_lang=zh`、整段切分和固定种子，没有修改 TTS 私有配置。当前上游 `TextPreprocessor.get_phones_and_bert` 在 `zh` 模式下保留英文片段并使用英文前端，不是把英文全部按中文读。

生产 Whisper base 自动识别到中文和英文，转写为「你好，这是宜音测试 Hello, this is the Voice Test」；已有本地 Whisper small 使用无提示词、自动语言识别交叉检查，结果为「你好，这是乙鹰测试 Hello, this is The Voice Test」。两者都有中文词字及英文冠词偏差，逐字核验均不通过；两种语言都被识别到，也不等于读音完整正确。

用户针对本样本明确反馈「中文英文都完整清楚」。因此该中英混合角色样本的完整性与可懂度试听通过；逐字 ASR 核验仍不通过，两个结论分开记录。未上传音频，代理未播放实体扬声器、未发送消息，也未将默认 ASR 切换为 small。

私有证据：`data/sessions/m6-mixed-character-20261031-01/VOICE_RESULT.json`、`data/sessions/m6-mixed-character-asr-20261031-01/MIXED_READBACK.json` 和 `data/voice-runtime/MIXED_SMALL_CROSSCHECK.json`；试听文件为 `data/speech/samples/winefox-mixed-20261031.wav`。用户试听反馈保存在本聊天及单独的本地验收汇总中。

## 微信客户端升级与备份

手动对照失败后，已只读检查：系统用户及机器级麦克风总权限为 `Allow`，音频和权限管理服务在运行，磁盘空间充足；微信已加载 `VoipEngine.dll`、MMDevAPI 和 Media Foundation，当前 `Weixin.exe` 及 `VoipEngine.dll` 的腾讯签名有效。现有加密 xlog 未解出内部录音错误，尚未定位微信内部失败的根因，没有据此更改系统隐私设置或重装声卡。

升级前版本为 `4.1.12.55`。[微软 WinGet 清单](https://raw.githubusercontent.com/microsoft/winget-pkgs/master/manifests/t/Tencent/WeChat/Universal/4.1.15.56/Tencent.WeChat.Universal.installer.yaml) 记录了 2026-10-19 发布的 `4.1.15.56`，安装包来自腾讯域名 `dldir1v6.qq.com`。已下载并核验腾讯 Authenticode 签名和清单 SHA-256：`4422bad98617dd095005c7879b2190f188ffa9ad6abf79e075358cf981a3d9e4`。安装器外壳文件版本为 `4.1.15.1000`，不能拿它替代安装后实际客户端版本核对。

当前程序的 31 个文件已复制到忽略目录并逐一比较哈希通过。用户明确同意「开始升级并复测」后，已关闭窗口并结束仍驻留后台的微信进程；在所有微信进程退出后，离线复制 3291 个配置、数据库及附属文件，共 15,611,332,836 字节，逐文件哈希与原件一致。复制包含数据库目录中的 WAL 等文件；加密数据库没有进行内容或语义校验。没有清除聊天数据，也没有把复制成功写成数据库完整性验收。

用户最终手动完成安装并进入聊天列表，实际客户端及 VoipEngine.dll 均为 `4.1.15.56`，腾讯签名有效。代理未操作 Windows 管理员确认或认证界面。旧版录音失败、新版能够录音和发送均有实际观察，内部故障根因未查明。

私有准备及升级记录：`data/voice-runtime/WECHAT_UPGRADE_READY.json`；程序备份：`data/voice-runtime/wechat-before-upgrade-4.1.12.55-20261031-01/MANIFEST.json`；用户状态备份：`data/voice-runtime/wechat-user-state-before-4.1.15.56-20261031-01/MANIFEST.json`。

## 升级后的真实原生语音

范围仅限用户指定的 Windows「文件传输助手」；电脑微信账号与项目手机账号不同，没有向其他联系人或群发送。唯一发送的样本为 6.02 秒小酒狐语音，正文「你好呀，我是数字生命。现在开始，我会用这个新的声音和你聊天。」。播放时确认实际 Weixin.exe 进程捕获 CABLE Output，无欠载，没有使用实体扬声器或实体麦克风。

发送后，微信「语音转文字」得到「你好呀，我是数字生命，现在开始，我会用这个新的声音和你聊天。」；标点归一化后全文匹配。这是官方转写核验，没有替代人工试听。界面操作之间的模型和工具往返导致录音达到 60 秒上限，实际卡片显示 60 秒，时长不通过。同一意图未重发，前面的未播放或空白尝试均取消。音频流关闭，三个默认麦克风角色和虚拟播放端静音状态已恢复。

私有证据：`data/sessions/m6-wechat-fileassistant-native-20261031-02/PLAYBACK.json`、`SEND_ATTEMPT.json`、`NATIVE_READBACK.json`。相邻 `...-01` 失败记录保留。

## 固定时长自测入口

新入口先生成音频，再由项目 Windows UIA 执行器在单个本地进程内完成开始录音、播放完成和立即发送，避免每步等待模型。只接受已打开、输入框为空的文件传输助手，不导航联系人；音频限 0.25–20 秒，每个稳定意图最多尝试发送一次。超过源音时长加 3 秒会取消；发送结果不确定则停止，不自动重发。

已通过仅准备入口生成 3.62 秒新样本，`messages_sent=0`、`gui_executed=false`。真实音频资源进入/退出验证得到 `defaults_restored=true`、`mute_restored=true`、`watchdog_finished=true`。这些没有执行新适配器的 GUI 发送路径，不能宣称时长修复已实际通过。

只准备音频：

```powershell
cd D:\codex\Proj.DigitalLife\v2
.\start.ps1 -Command WechatTimedSelfProbe -PrepareOnly `
  -VoiceIntent NEW-PREPARATION-INTENT -VoiceText '你好，这是微信静默语音的时长测试。' `
  -Session data/sessions/NEW-PREPARATION
```

实际自测（会向文件传输助手发送一条原生语音）：

```powershell
.\start.ps1 -Command WechatTimedSelfProbe `
  -VoiceIntent m6-wx-timed-20261031-01 -VoiceText '你好，这是微信静默语音的时长测试。' `
  -Session data/sessions/m6-wx-timed-self-20261031-01
```

执行前手动登录新版微信，打开文件传输助手、保持空草稿，执行时不要切换窗口，首次建议在场观察。需要本机 `data/tts-env/Scripts/python.exe` 中的 `numpy`、`sounddevice`、`pycaw`、`psutil`、`pywinauto`；基础 wheel 不包含这些可选依赖及 acceptance 脚本。

只使用唯一命名的虚拟声卡端点，不回退到实体输出。临时切换三个默认输入角色并启动独立 45 秒音频恢复看门狗，普通异常会先取消未发送录音再恢复资源。强制终止或界面卡死时，应现场确认录音面板关闭；资源恢复不证明 GUI 发送成功。结果 `accepted` 只表示新原生消息可见且时长在容差内，正文需另行读取或试听。未知结果不自动重试；保留意图和 `FAILURE.json`、`WORKER_FAILURE.json`（子进程可写出时）。

本版源码和独立安装 wheel 各 351 项回归通过，36 个运行模块逐字节一致。新增 8 项回归覆盖定时发送、超时取消、会话切换、不确定发送不重试、错误时长拒绝、无回执、无效时长及清理失败保留送达事实。Wheel：`digitallife_v2-2.0.0a23-py3-none-any.whl`，SHA-256：`16b8a86d299e09d57fc598bed3702a3f7edf97b8adc9a7ebca55629382a770c6`。离线回归不替代新执行器实际 GUI 验收。
