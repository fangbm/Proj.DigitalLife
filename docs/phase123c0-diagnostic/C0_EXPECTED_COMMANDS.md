# 固定命令审查表（不可执行模板）

所有下列设备操作均须显式 -s AUTHORIZED_SERIAL；占位符不是可直接运行脚本。

身份：get-state；shell getprop 加固定八个属性（见 JSON）。范围：shell wm size；shell dumpsys power；shell dumpsys window policy；shell dumpsys window windows；shell dumpsys window displays；shell dumpsys input。设备列表是 host 选择检查 devices -l，不能产生默认目标选择。

PNG：exec-out screencap -p。

XML 候选：exec-out uiautomator dump /dev/tty。

禁止任意拼接、管道、重定向、shell字符串、用户自选参数、默认serial、am/input/pm/install/pull、文件路径 fallback。真实后端无子进程创建实现、恒拒绝；此表仅供下一轮逐命令批准与实现审查。没有“自动尝试所有方式”策略。
