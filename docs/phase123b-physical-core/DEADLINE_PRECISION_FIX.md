# 86400秒精确期限修复

修复前确定性失败：start=110000.00000399999；end=start+86400=196400.00000399997；相减elapsed=86399.99999999999。remaining尚为1.4551915228366852e-11，精确到期仍sleeping。原始失败日志deadline-before.stderr.log保留，非自然运行故障。

SessionLedger.start首次持久化deadline_monotonic=now+duration_seconds。remaining、expired、tick和Status统一以now>=deadline判断；elapsed_seconds继续使用实测差值供显示/统计，不再用于决定是否到期。旧格式用原始start+duration的相同加法恢复，不加epsilon、不提前或延长许可，不改变sleep/退避。

临近值用math.nextafter分别验证之前/恰好/之后；含大单调值、旧格式、sleeping/running、安全终态及boot改变。重启同一OS boot保留绝对期限；boot_id改变/单调倒退仍failed，不尝试把别的boot单调时钟接续。原start/tick冷启动拒绝逻辑保留。

EmergencyStop和unknown停止优先；监督器必须先收尾并重读安全证据才记completed_duration，期限修复不删除该收尾。显示elapsed最后一位可能略小于86400不代表期限准入错误；报告应分开显示绝对deadline与elapsed。
