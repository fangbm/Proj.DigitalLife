# a9 通知到会话的路由

本实现只使用有效租约内、当前 Android 用户、已允许来源包实际发出的 `contentIntent`，默认要求 creator package 与来源包相同；QQ 的本机 OPPO 推送入口另核对固定系统组件 `com.heytap.mcs` 的系统应用标志和 PendingIntent creator UID，才允许使用原入口。模型不能传 Intent、URI、组件或通知 key；桌面从实际 EventHub 事件取回该 key。路由返回后必须独立观察目标 App 的聊天界面，再核对当前账号、好友或群信息、实际正文及发送权限。

## Android 15/16 配置

本机修复前 `RECEIVE_SENSITIVE_NOTIFICATIONS` AppOp 为 `ignore`，此前 QQ 提供给监听器的内容出现“已隐藏敏感通知内容”。Android 15 对通知监听器引入敏感内容限制，见 [官方行为变更](https://developer.android.com/about/versions/15/behavior-changes-all)。在用户已授权手机权限的本机，显式管理命令可给 Companion 授权并核对系统读回：

```powershell
python -m digitallife_v2 bridge-setup --profile find-x6 --apk companion/build/digitallife-companion.apk --provision-only --allow-sensitive-notifications
```

原设置保存在忽略的 `data/notification-permissions.private.json`，重复设置不会覆盖原值。普通 Run 和模型不能调用该管理选项；其他机器也不会因安装源码自动授予敏感通知权限。监听器继续只在有限租约内收集指定包，结束清空内存队列及路由缓存。

Android 在监听器连接时缓存可信 UID；[Android 16 系统实现](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/android16-release/services/core/java/com/android/server/notification/NotificationManagerService.java) 中的 `isAppTrustedNotificationListenerService` 与服务连接处理共同决定该状态。因此显式授权后重连本监听器，避免权限命令读回已允许而旧连接仍使用旧状态。没有关闭整个手机的通知或投屏保护。

## 真正的通知入口

桌面先把自己的 Companion 主页面置于前台。Native 端要求该 Activity 已 resumed、原通知入口仍有效、来源包已允许、创建者通过同包或上述固定系统推送校验、租约未变且本租约尚未尝试同一 key。Android 14 起 PendingIntent sender 需要显式委托启动权限，见 [官方 Activity 安全文档](https://developer.android.com/guide/components/activities/secure-bal)。当前编译 SDK 35 使用相应 ActivityOptions，并额外要求桥在前台；不会从任意后台服务突然弹出聊天。

调用原 PendingIntent 时不传 fill-in Intent，不改动目标或参数。调用已移到监听器锁外，避免目标 App 启动时的通知回调与监听器锁互相等待。每个 key 每租约最多调用一次；`send()` 返回只是调用结果，最终成功依据是桌面独立观察到实际聊天页。

已知无入口时预检直接暂缓；过期、未前台、已尝试、或系统明确返回取消时，Native 返回 `not_dispatched=true`。只有该可信的明确响应才能取消本次动作 pending 且不计输入。调用已返回后再出现观察错误，仍保留 unknown 屏障；断线、超时和不确定结果不解释为“没执行”。

## 事件与验收

正常路径为 `SessionLoop.step` 的真实通知判断 → `reply_now` → `SocialAgent` 路由/读取/再次决定 → 单次发送 → 发后观察。已知发送前暂缓的事件重新排队并退避 120 秒，不在分类后丢弃，也不会马上热循环。未知发送仍受发件箱冻结与全局 pending 约束。

`M4Listen` 无 `-SocialBinding` 时直接调用这条生产步骤，只过滤本轮实际测试标记；不改事件时间、来源和接收记录。旧的显式绑定验收入口仍标为 legacy，不能算作通知自动定位证明。新的验收也不使用群消息快照文件。

测试消息到达后若本次剩余不足 450 秒，在打开通知之前结束当前窗口，保留实际系统通知，另开新的有限测试窗口读取 active snapshot；不把半程发送流程塞进即将到期的会话。此为人工启动的验收安排，普通 Run 不自动续跑。

路由专项 `acceptance.route_probe` 可以使用实际保留通知只验证页面跳转，0 模型、0 聊天发送，明确记录通知年龄。它与新来信完整回复验收分开，旧通知打开成功不能冒充新消息回复完成。

## 现场发现并修复的输入语义

通知分类明确区分现在打开核对的 `reply_now` 与本轮不打开的 `reply_later`，提供实际入口核验能力；身份 unresolved 不意味着无法进入现场核对。M4 启用时不再附带 M3 仅记录意图的提示，模型仍可选择稍后或不回复。

微信读取上下文时，先在当前截图上定位真实输入框，以它的上边界排除草稿并读取下方新消息，不沿用固定 87% 高度或旧截图编辑框边界。对实际通知入口打开的微信会话，完整预览与最后一条来信可以按 Unicode NFKC 和空白归一化逐字匹配；无编辑距离容错、不只比较测试标记。可见正文精确匹配不依赖通知更新时间与聊天显示分钟相同；隐藏正文的备用匹配仍要求原通知路由及相同 UI 分钟。

有限接收验收只选择仍有原生入口的实际待处理标记事件，已消失通知不会使新窗口提前结束，也不会修改原事件的来源或时间。普通生产链路仍保留其暂缓与退避记录。
