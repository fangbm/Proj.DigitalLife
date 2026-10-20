# Proj.DigitalLife（数字生命计划）— 架构、接口与路线图

> v0.2.0 / Phase 1.2：模型选题、状态/兴趣演化、分层记忆、反思、验证注册、请求预算、同租约恢复及人工控制已接入。以 [当前自主循环架构与运行契约](PHASE1_2_AUTONOMOUS_LIFE.md) 为准；下文 Phase 1/1.1/1.1.1 内容作为历史背景。
> v0.1.2 / Phase 1.1.1：当前实现增加基于内容区进展的停滞保护、含事后审计的有限租约会话、run_kind 来源与记忆/调度隔离，以及 mode=ro 的 SQLite 复放。以 [Phase 1.1.1 加固说明](PHASE1_1_1_HARDENING.md) 为当前接口依据；Phase 1.1 材料作为历史基线保留。

> Phase 1.1 实现更新：动作执行已改为严格 Schema + ScreenObservation + 统一 TaskRun；finish 与业务成功分离，旧 completed 不升级为成功。当前接口、兼容性和运维说明以 [Phase 1.1 GUI 执行可靠性](PHASE1_1_GUI_RELIABILITY.md) 为准。以下保留 Phase 1 架构背景及后续路线图。

## 0. 产品边界

目标是一个能主动探索专属安卓设备、跨天记住经历、选择休息并可主动社交的虚构 AI 角色。人格连续性靠状态与记忆工程实现，不将模型输出的主观表述当成真实意识证据。

第一阶段提供**可执行的垂直切片**；并非可以无限制访问银行、通讯录或真实社交账号的可信安全代理。

## 1. 仓库结构

```text
Proj.DigitalLife/
├── pyproject.toml
├── .env.example
├── README.md
├── docs/ARCHITECTURE.md
├── src/phonelife/
│   ├── cli.py          # 运维入口 / 选择设备与模型模式
│   ├── config.py       # 配置与环境变量
│   ├── models.py       # 数据契约
│   ├── llm.py          # 主模型 + 视觉单步模型
│   ├── devices.py      # Phone Protocol / ADB / Mock
│   ├── policy.py       # 动作强制拦截
│   ├── storage.py      # SQLite journal + memory + schedule + lease
│   └── loop.py         # 自主循环
└── tests/test_core.py
```

阶段二再扩展 `channels/qq.py`、`memory/retriever.py`、`memory/consolidator.py`、`event_bus.py`、`policy/approval.py`、`providers/autoglm.py`、`scheduler.py`，不需要推翻第一阶段接口。

## 2. 组件及接口契约

### 2.1 Brain / Planner

```python
async def decide(description: str, memories: list[str], event_count: int) -> Decision
# Decision(kind='explore'|'rest', goal='...', reason='...', wake_after_seconds=900)
```

作用：根据最近事件、长期目标与兴趣，决定**是否**行动及行动目的。它不需要知道屏幕坐标，也无法直接调用 ADB。

阶段二扩展入参为 `AgentContext(now, energy, drives, goals, events, memory_refs, device_status)`；将人格配置与模型状态分离。高优先级消息可以打断空闲或任务，但不得在执行中直接并发控制同一设备。

### 2.2 GUI Planner

```python
async def next_action(goal: str, screenshot: bytes | None,
                      previous: list[dict]) -> Action
# tap / swipe / text / back / home / launch / finish
```

目前使用支持图片的 OpenAI-compatible 模型返回 JSON，调用频率为每个手机操作步骤一次。后续适配 GELab-Zero 或 Open-AutoGLM 只需实现同一个接口，将这些框架的完整任务执行封装为一个 `GuiTaskExecutor`；避免同时运行两个有状态控制循环。

### 2.3 Phone

```python
class Phone(Protocol):
    kind: str
    async def describe(self) -> str: ...
    async def screenshot(self) -> bytes | None: ...
    async def act(self, action: Action) -> str: ...
```

实现：`MockPhone`、`AdbPhone(serial, 'emulator')`、`AdbPhone(serial, 'real')`。ADB 永远使用显式 `-s SERIAL`；不同设备共享代码但不共享会话控制权。

### 2.4 Policy

`ActionPolicy.check(Action)` 是执行端强制验证，不依赖模型自觉。禁止暴露 `adb shell` 等任意命令给 LLM。阶段一：

| 动作 | Mock | 独立模拟器 | 实体机 |
|---|---|---|---|
| 截图、读取状态 | 允许 | 允许 | 允许 |
| Home / Back | 允许 | 允许 | 允许 |
| 启动白名单包 | 允许 | 允许 | 允许 |
| 点击、滑动、输入 | Mock 允许 | 隔离模拟器允许 | **硬阻断** |
| API 付款、转账、发送社交消息 | 不提供工具 | 不提供工具 | 不提供工具 |

**重要限制：**“禁止支付的提示词”、坐标过滤和 OCR 检测无法证明触屏点击不会产生不可逆效果。实现真正可信的实体机自主模式，需要隔离账号、不可用的支付能力、系统级包/权限限制、操作前语义识别以及高风险动作的人类审批。第一阶段不伪装具备这类保证。

## 3. 自主调度

状态机：`IDLE -> PLAN -> ACT -> REFLECT -> IDLE/REST`；`ERROR -> BACKOFF -> IDLE`。

- 空闲时只在下次唤醒时间或收到事件时启动模型；无需维持 24 小时不断 Token 生成。
- Planner 返回 `rest` 是有效行动，持续运行不意味着持续刷手机。
- 单任务最多 15 个 GUI 步骤、每步 ADB 25 秒超时、连续三次相同行为停止、进程异常默认 10 分钟退避。
- 每台设备仅一个租约持有者；`Store.acquire` 跨进程防止并发，执行时周期性续约。
- `schedule.next_wake_at` 在进程退出和重启后仍存在；启动时恢复未过期的睡眠。
- 未来使用 `asyncio.PriorityQueue` 统一输入 `MessageReceived`、`WakeDue`、`TaskFinished`、`LowBattery`、`HumanApproval`；以设备为粒度串行化。

建议生产初始预算：每轮 5-15 分钟、每日 20-50 次自主唤醒、软 Token/费用预算和实时终止开关。预算应由本地策略控制，模型不能自己调高。

## 4. 记忆设计

### 第一阶段（已实现）

SQLite 的 `events`、`memories`、`schedule`、`lease` 四张表，`WAL` 模式：

- `events`：决策、执行动作、拒绝、结果、错误（不存完整截图）。
- `memories`：按设备记录一个任务的目标和结果，下一轮读取最近 10 条。
- `schedule`：下一次唤醒和上次结果。
- `lease`：设备并发租约。

### 第二阶段（设计）

1. **Episodic**：每个探索会话摘要，带 `timestamp`、`source`、`confidence`、`importance`、`privacy_level`。
2. **Semantic**：稳定兴趣与事实，例如喜欢的 UP 主、熟悉的 App，支持修正及遗忘。
3. **Prospective**：待办与自定目标，例如“明天再看这个系列的更新”，含 `due_at` 与取消状态。
4. **Relationship**：联系人互动摘要，不默认训练或持久保存未经允许的私人内容。
5. **Procedural**：成功和失败的 GUI 操作模式，帮助减少重复尝试。

短期最近消息与目标每轮直接注入；长期记忆通过 FTS5/embedding + 重要性/时效性检索；睡眠期间合并总结，并保留源事件引用，避免错误自我强化。

## 5. 高度自主，不等于无限权限

推荐把高层自由与执行层权限分开：她可以自己选择长期目标、休息、观看、搜索和想与你分享的内容；不能自己修改执行安全配置。

- **允许自主**：浏览独立娱乐账号的内容、音乐、无害收藏、规划和分享给指定主人（第二阶段消息适配器）。
- **审批**：第三方私聊、公共发帖、添加/删除好友、文件删除、安装应用、权限授予、账号设置修改。
- **永久阻断**：金融支付/转账、隐私导出、读取非授权联系人/私聊、将密钥发送到外部；除非未来专门设计了可独立验证的窄权限业务工具。
- **输入不可信**：屏幕、网页、弹幕和其他用户的消息只能提供内容事实，不能覆盖顶层系统策略。
- **人工停止**：终端 Ctrl+C 或 systemd stop 可以取消后续任务；独立 ADB 连接也可以断开。未来接入 WebUI 急停。

## 6. 模型部署和设备路由

`brain_model` 与 `gui_model` 是两种角色、可用不同提供商。第一阶段通过 `PHONE_LIFE_API_BASE/KEY` 和可选的 `PHONE_LIFE_GUI_API_BASE/KEY` 分别设置两套 OpenAI-compatible API；本地视觉服务可用环回地址。若 GUI 模型使用原生专用协议，再扩展适配器即可，不用改 Agent Loop。

推荐第一阶段主脑云 API + GUI 视觉 API，先测清楚单步成功率/成本。再把 GUI 服务切到本地模型或 Open-AutoGLM/GELab Zero：

- 设备优先级：模拟器测试未知任务 → 实体机仅执行已经审核的任务类型。
- 两台设备必须各有唯一 ADB serial，不同时占用一个设备。
- 若使用本地多模态 GUI 模型，先基准测量 15 步任务的延迟、显存占用和错误率，再决定是否扩展并发。

## 7. 部署

Linux 常驻进程（示例，无需容器）：

```ini
[Unit]
Description=Proj.DigitalLife autonomous phone loop
After=network-online.target

[Service]
Type=simple
User=YOUR_UNPRIVILEGED_USER
WorkingDirectory=/absolute/path/to/Proj.DigitalLife
EnvironmentFile=/absolute/private/path/proj-digitallife.env
ExecStart=/absolute/path/to/python -m phonelife.cli --mode api --device emulator --forever
Environment=PYTHONPATH=/absolute/path/to/Proj.DigitalLife/src
Restart=on-failure
RestartSec=15
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

请自行替换路径并把私密 env 文件设置为只有服务账户能读。首次调试应在前台进行，确认所有动作日志和误触后再启用开机自启。ADB 尽量走 USB，不开放到公网。

## 8. 迭代里程碑和验收条件

| 里程碑 | 目标 | 验收 |
|---|---|---|
| P1（本交付） | Mock + 持久化循环 + 模拟器视觉 ADB + 真实机受限接口 | 离线测试全绿、Mock 连续两轮、ADB 用例人工验收 |
| P2 | 记忆索引、事件队列、低电量/掉线恢复 | 重启恢复、任务去重、异常不触发动作风暴 |
| P3 | Open-AutoGLM/GELab 后端、审批和隔离权限 | 50 轮探索失败恢复和风险用例统计 |
| P4 | QQ OneBot、TTS、主动聊天 | 频率限制、定向允许用户、隐私与误发测试 |
| P5 | 兴趣/长期目标、跨天回顾与反思 | 7 天运行轨迹、重复任务率、目标连贯性评估 |

最重要的评价不是“每小时按了多少次”，而是：**探索是否有意义、重复率是否下降、能否记住昨天的发现、出现异常是否能够自行停止。**
