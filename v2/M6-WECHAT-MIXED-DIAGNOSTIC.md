# M6 微信静默音频与混合音色诊断

2026-10-31，运行版本 `2.0.0a21`。本轮没有修改运行模块，没有发送任何 QQ 或微信消息，M6 仍未整体完成。QQ 文字/语音格式选择的已通过记录见 [格式验收](M6-REPLY-FORMATS.md)。

## 微信虚拟声卡

当前 Windows 微信自测范围仅为用户指定的文件传输助手。现有 VB-Audio Virtual Cable 设备报告驱动 `1.0.3.5`、日期 2014-09-02；版本年代只是排查线索，尚未确定根因，也未升级、卸载驱动或重启设备。

已验证输入测试 WAV 含有按顺序各一秒的 300、500、700、900、1100、1300 Hz 六段信号。现有音频库的回录反复出现首段频率，中间夹有静音，后续频率缺失。独立播放/录音流、10 ms 写入块、统一 48 kHz 和小缓冲独占方式均未解决，输入无溢出报告、输出无欠载报告。这些结果证实回录内容异常，不足以单独区分驱动、音频库及系统音频处理。

配置选择参考 [VB-Audio 官方手册](https://vb-audio.com/Cable/VBCABLE_ReferenceManual.pdf)：内部缓冲须容纳所接应用的音频块；输入、输出及内部采样率一致可避免驱动采样率转换。本轮只改探针的流参数，没有修改驱动持久参数。

为交叉检查，准备了 FFmpeg DirectShow 录音，输入固定为 `CABLE Output (VB-Audio Virtual Cable)`，没有实体麦克风回退。该进程超时、没有生成录音文件；随后真实界面发现 Windows 的 `Allow ffmpeg to access your microphone?` 授权窗口。因此这次 DirectShow 超时归为权限请求阻塞，不能记作驱动传输失败。进程已终止，须用户手动处理授权后才可再试。依照 computer-use 的权限规则，没有自动操作该弹窗。

另外做过一次默认麦克风的 45 秒有界临时切换，原三个角色的设备 ID 先保存到本地，退出时均已读回确认恢复。微信界面操作被系统弹窗阻挡，此轮没有得到微信录音能力的有效新结论。没有录音或点击发送。现有临时音频流全部关闭，虚拟播放端恢复原先静音，FFmpeg 录音进程没有遗留。

私有证据位于 `data/voice-runtime/`：`cable-six-tones-source-20261031.analysis.json`、`virtual-cable-lowlatency-probe-20261031.analysis.json`、`CABLE_LOWLATENCY_PROBE.json`、`WECHAT_MIC_LEASE.json`，以及有界探针脚本和原超时日志。微信静默发送保持未通过。

## 小酒狐中英混合样本

通过正式 `VoiceSynthesize` 生成文本「你好，这是语音测试。Hello, this is a voice test.」的 4.54 秒、32 kHz 单声道 WAV，使用既有角色权重、`text_lang=zh`、整段切分和固定种子，没有修改 TTS 私有配置。当前上游 `TextPreprocessor.get_phones_and_bert` 在 `zh` 模式下保留英文片段并使用英文前端，不是把英文全部按中文读。

生产 Whisper base 自动识别到中文和英文，转写为「你好，这是宜音测试 Hello, this is the Voice Test」；已有本地 Whisper small 使用无提示词、自动语言识别交叉检查，结果为「你好，这是乙鹰测试 Hello, this is The Voice Test」。两者都有中文词字及英文冠词偏差，逐字核验均不通过；两种语言都被识别到，也不等于读音完整正确。

已把样本展示给用户，请求对完整性及发音作试听反馈，目前尚未收到本样本的反馈。没有用此前另一段中文语音的用户试听结果替代混合音色验收。未上传音频，未播放实体扬声器，未发送消息，也未将默认 ASR 切换为 small。

私有证据：`data/sessions/m6-mixed-character-20261031-01/VOICE_RESULT.json`、`data/sessions/m6-mixed-character-asr-20261031-01/MIXED_READBACK.json` 和 `data/voice-runtime/MIXED_SMALL_CROSSCHECK.json`；试听文件为 `data/speech/samples/winefox-mixed-20261031.wav`。混合音色验收保持未完成。
