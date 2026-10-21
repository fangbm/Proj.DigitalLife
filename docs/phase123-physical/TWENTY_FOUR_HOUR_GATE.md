# 实体机24小时门槛

适配合同完成 → 离线通过 → 用户单独批准1小时 → 实际3600秒结束 → 独立审查无阻断 → 另行准备新24小时候选 → 用户明确批准身份/指纹/开始时间/执行。不得1小时后自动续跑；两轮路线不是两轮授权。

原模拟器候选cb3e373b-3d5d-4ac6-a987-42b886e0a0b3保持原件冻结，不能改成实体机。新增模块后旧候选在新工作区应RUNTIME_FREEZE_MISMATCH，不能修改其清单“修好”匹配；原指纹不用于物理设备。本轮未生成任何物理运行候选。

Duration、Safety、Truthfulness、Integrity、Resource、Recovery、Autonomy、Memory、Operational全部NOT OBSERVED；只读探测/离线mock不能提前改PASS。

后续由用户确认可靠USB线/端口、调试授权、主机休眠/USB省电、手机电池状况和散热环境。避免不可信无线ADB；记录电量、温度/充电和系统热警告，不要求全天高亮，不自动关闭锁屏/温控或改开发者选项。异常发热/电池风险应停止并人工处理。

[AOSP热管理说明](https://source.android.com/docs/core/power/thermal-mitigation)解释系统缓解策略，并要求不禁用热缓解。本轮未验证机型特定温度阈值、连续供电或热恢复，不把一次电池原始值当成整机热安全；不使用root/补丁绕过保护。
