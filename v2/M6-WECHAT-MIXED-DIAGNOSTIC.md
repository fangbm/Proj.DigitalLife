# M6 微信静默音频与混合音色诊断

2026-10-31，当前 `2.0.0a22`。已修正本地诊断重采样并新增可重复的虚拟声卡回录入口。中文、混合语音的完整信号回录通过，混合样本的用户试听通过；微信客户端仍提示不能录制，尚未发出微信原生语音。M6 仍未整体完成。QQ 文字/语音格式选择的已通过记录见 [格式验收](M6-REPLY-FORMATS.md)。

## 微信虚拟声卡

当前 Windows 微信自测范围仅为用户指定的文件传输助手。现有 VB-Audio Virtual Cable 设备报告驱动 `1.0.3.5`、日期 2014-09-02。本轮未升级、卸载驱动或重启设备。

此前多个临时探针的回录反复出现首段声音并夹有静音，改缓冲和接口都未解决。最终定位到探针的 `np.arange(...)*source_rate/target_rate`：本机 TTS 环境的 NumPy 默认索引为 Windows `int32`，乘采样率后溢出。32→48 kHz 时约 1.4 秒后就会出现负索引，插值把它变成首样本或早期片段。保存的原始六段测试音正确，送入设备前的额外重采样却已损坏。不能据这些探针把问题归为旧驱动或音频库故障。

新工具使用 Python 浮点位置做重采样，不依赖 NumPy 默认整数宽度；测试覆盖六秒上采样和八秒下采样的尾部。已检查正式 `speech.normalize_wav`，它原本使用 Python 浮点位置，没有这个溢出问题。早期错误探针及失败记录保留，不覆写为成功。

第一次 FFmpeg DirectShow 录音被 Windows 的麦克风权限弹窗阻挡，未生成录音；该超时单独保留。用户手动允许后，DirectShow 完整录到 300→1300 Hz 六段原始测试音。修正索引后，中文语音也能完整回录，本地转写与原文在标点归一化后相同。

正式工具再次用 6.02 秒中文样本和 4.54 秒中英混合样本进行实际回录，整个源信号均有对应窗口，波形相关度分别为 `0.993448`、`0.990980`，两次都通过 `0.98` 阈值，无播放欠载。相似度只在容纳整个源信号的窗口中计算，短前缀相同不能算通过。它证明本机虚拟音频传输，不证明微信已发送或收件人已听。

微信在临时默认输入设为虚拟设备后，点击原生语音仍提示「不能录制音频，请稍后再试」。临时切换的三个默认麦克风角色均已读回确认恢复，设置窗口已关闭，没有修改微信设置或发出消息。已请求用户在文件传输助手手动点击作对照，尚待结果。微信实际录制、发送与正常运行接入保持未完成。

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
