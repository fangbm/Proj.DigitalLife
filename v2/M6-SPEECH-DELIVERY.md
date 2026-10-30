# M6 语音基础与手机声学路径试验

2026-10-30，`2.0.0a16`，分支 `codex/v2-m6`。本轮完成本地 TTS/ASR 基础与手机自播自录的可行性试验，**M6 整体仍在开发中**。

## 本轮结论

已在当前 Find X6、Android 16、主微信 8.0.79 上验证：电脑生成固定系统音色的 WAV，经已有认证桥传给手机，手机播放时由微信的普通麦克风录入，文件传输助手中出现一条 8 秒原生语音。随后使用微信自身的“转文字”，完整读回“你好，我是数字生命，这是一条语音测试消息。”。这证明本次微信原生录音确实包含合成内容，消息类型为语音气泡。

电脑仅负责合成和控制，不必通过电脑扬声器外放，也不要求手机靠近电脑扬声器；手机自身仍然外放，环境噪声和音量会影响录入。没有把文件分享当作原生语音，也没有宣称将 PCM 直接注入其他 App 的麦克风。

| 项目 | 本轮证据 |
| --- | --- |
| 固定 AI 声音 | Windows 已安装的 Microsoft Huihui Desktop；英语使用固定的 Microsoft Zira Desktop，未克隆真人声音 |
| 普通话 TTS/ASR | 生成实际可解码 PCM/WAV，再用本地 faster-whisper 转写；测试短句内容一致 |
| 中英混合 | “你好，这是语音测试。Hello, this is a voice test.” 经固定中英文系统音色合成，本地 ASR 保留两种语言；模型置信指标明确为启发式数值 |
| 手机普通麦克风 | 自有可见校准页使用 `MIC`；手机自身播放的短句完整转写，录音 RMS 约 1027 |
| 手机语音通信源 | 同等实际音量下使用 `VOICE_COMMUNICATION`，RMS 约 18，未识别出测试短句；不可因此宣称实时通话音频可用 |
| 微信原生录音 | 系统采集配置显示微信使用 `MIC`、16 kHz 单声道、未被静音；录音后显示 8 秒原生语音，微信转写全文与测试内容一致 |
| 对外范围 | 仅微信自有“文件传输助手”，没有给联系人或群发送语音 |

主微信第一次录音出现系统麦克风许可请求，未开始录音。随后按用户既有完整手机权限授权启用录音。第一条实际自测语音因后台播放器失败而没有确认内容，仍保留原记录；修复为可见的前台媒体播放服务后，再做新的一次自测，第二条通过内容核验。没有把第一次的气泡或客户端操作返回值当作音频到达证明。

早期音量 Shell 命令在 ColorOS 上未生效，读回仍为静音。最终使用手机端 AudioManager 设置、读回实际测试音量，并在服务结束恢复原音量和静音状态。自有校准页也分别记录录音时的真实音量，而不依赖命令退出码。

## 实现与边界

- `speech.py` 提供可取消的本地合成、音频格式规范化、能量端点信息、离线 ASR、分段时间、语言提示、低置信/混合语言缺失标记、音频哈希与临时资产引用。
- 本地配置为忽略的 `data/speech.private.json`，与设备绑定；没有配置时关闭。ASR 复用 M3 已配置的独立 Python 环境和本地模型，强制离线，不下载模型或上传联系人音频。
- 语音使用同一 V2 数据库中的增量 `speech_assets`、`speech_usage` 表，预留音频秒数预算；失败或取消仍保留预留。只提供本地 Windows SAPI 和 faster-whisper 适配器；其他提供商可在后续增加。
- 原音资产保存于独立私有缓存并带有效期；`cleanup()` 只移除本模块拥有、过期且哈希一致的文件。该清理能力尚未接入长期运行调度，不将其称为已完成的全局保留策略。
- 手机桥新增自有音频导入、播放/停止、可见校准页和有通知提示的有限前台媒体播放服务。租约结束、正常 Stop 会停止播放/采音，原有通知与输入法接口保持兼容。ColorOS 若忽略后台音量恢复，原音量保存在本机恢复日志中，状态明确报告 `audio_volume_restore_pending`；回到手机桥前台或点击“停止播放并恢复音量”后再读回确认，不把播放已停等同于音量已经恢复。本轮最后已恢复最初的媒体音量 0。
- `NativeVoiceProbe` 只允许已打开的微信文件传输助手，先验证后台播放器完成播放，再执行一次实际录音。尝试记录先落盘，已有尝试不重放；外发状态未知仍沿用原有持久停止屏障。没有向普通模型开放任意按住坐标或任意目标发语音。

当前手机的系统 TTS 没有返回可用中文离线音色，因此本轮实际使用电脑端本地合成。手机桥里保留系统 TTS 的探测接口，但不把它标为本机中文 TTS 验收通过。

## 重现入口

需要绑定测试手机、已配置的本地 ASR 环境、Windows 系统音色及本轮 Companion。以下命令会实际操作手机；`NativeVoiceProbe` 会给自己的文件传输助手发送一条语音。

```powershell
cd D:\codex\Proj.DigitalLife\v2
.\start.ps1 -Command SpeechProbe -MicrophoneSource mic -Session data/sessions/NEW-M6-MIC
.\start.ps1 -Command SpeechProbe -MicrophoneSource voice_communication -Session data/sessions/NEW-M6-COMM
# 先手动打开微信“文件传输助手”，切换至“按住说话”。
.\start.ps1 -Command NativeVoiceProbe -Session data/sessions/NEW-M6-SELF-VOICE
```

每次使用新会话目录。普通校准最多三分钟，微信自测最多四分钟，无自动续跑；不需要模型 API Key，启动脚本不会为这两个入口解密模型凭据。校准是否通过以 `SPEECH_CALIBRATION.json` 的短句匹配结果判断；原生录音入口仅生成待内容核验的观察记录，后续必须播放核验或使用平台转写独立核对。

本地配置最小示例（`serial` 使用自己的绑定配置，示例不构成自动启用）：

```json
{
  "version": 1,
  "serial": "YOUR_BOUND_DEVICE",
  "enabled": true,
  "tts": {"enabled": true, "provider": "windows_sapi", "voice_id": "Microsoft Huihui Desktop", "english_voice_id": "Microsoft Zira Desktop", "language": "zh-CN", "rate": 0},
  "asr": {"enabled": true, "provider": "faster_whisper_local"},
  "limits": {"max_tts_characters": 300, "max_tts_seconds": 20, "max_asr_seconds": 60, "audio_seconds_hour": 900, "raw_ttl_seconds": 600}
}
```

## 未完成的 M6 范围

QQ 原生语音尚未实测；指定真人联系人/群的入站语音、会话绑定和平台转写接入 LifeBrain 尚未完成；面向已授权目标的正常语音发件箱、关系频率和自主选择尚未接入。本轮不会自动把已有文字社交许可提升为自主语音发送。M7 的接听、实时上下行与三轮双向通话没有进行测试。

## 验证与记录

源码与独立安装 wheel 各 309 项离线测试通过，31 个运行模块逐字节相同；Companion 已通过 Android SDK 构建、签名校验并安装。新增 17 项回归覆盖格式/静音、来源绑定、分段时间、取消、预算、许可撤销、临时缓存边界、原生自测目标限制、重复尝试禁止重放与播放服务释放。

Wheel：`digitallife_v2-2.0.0a16-py3-none-any.whl`，SHA-256：`8dee8c8442d9662f20adb1f06e2b5ea99013def28a7e3b7fd82b442f232bbb3c`。

实际截图、音频、系统路由快照与 JSON 仅保留在忽略的 `data/` 中。关键记录包括 `m6-phone-acoustic-05` 的普通麦克风成功、`m6-phone-acoustic-06` 的通信源负结果、`m6-speech-mixed-03` 的中英混合样本、`m6-wechat-self-voice-03` 的录音与播放、`m6-wechat-self-voice-review-02` 的微信全文核验。通信源测试的后续回桌面阶段异常退出，旧状态与原始日志保留；实际录音负结果及后续资源清理分别记录，不补写为完整会话成功。

技术参考：[Android TTS](https://developer.android.com/reference/android/speech/tts/TextToSpeech)、[音频输入共享](https://developer.android.com/media/platform/sharing-audio-input)、[音频焦点](https://developer.android.com/media/optimize/audio-focus)、[前台媒体播放服务](https://developer.android.com/develop/background-work/services/fgs/service-types#media-playback)。平台文档用于解释接口边界，实际手机能力以上述样本为准。
