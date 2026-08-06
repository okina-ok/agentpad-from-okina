# AgentPad 更新日志

> 产品：Codex Micro 平替 RGB 状态键盘（v1 = 通道 B 状态文件协议）
> 当前版本：v0.4.0（2026-08-07，功能验收完成）

---

## v0.4.0 · 2026-08-07 —— 输出阶段修正 + 延迟优化收尾（验收版）

### 修复：最终回答输出时灯一直停留在 thinking（用户反馈）
- `response.output_text.delta`（回答文字开始流式出现）→ 直接判 **done（绿）**，
  回答一出来灯就绿，不再等输出完 + 5 秒安静期。
- 输出后的杂项行（response.completed / message item / needs_follow_up）不再把绿拉回紫。
- 工具行（含输出中途）一到立即转 running（青）。
- 说明：agent 边干活边写分析（中间文字输出）也会短暂显示绿色，属同一"输出中"语义。

### 修复：done 兜底（上一版发现 app 不写最终文本 response.completed 行）
- 新增双通道 done 判定：
  * 快通道：response.completed（文本）+ 安静 1 秒 → 绿；
  * 兜底通道：助手文本消息完成 / post sampling `needs_follow_up=false` + 安静 5 秒 → 绿，
    工具行一到立即撤销兜底。

### 优化：转场速度（实测）
- 工具结束 → thinking：3~4 秒 → **约 1 秒**（"tool call completed" 行改判 thinking）
- 防抖 1.5 → 0.5 秒；done 安静期 2.5 → 1.0 秒（兜底 5 秒）；轮询 0.3 → 0.2 秒
- 工具执行期间 ~/.codex 无实时文件写入（实测），1.5~2 秒日志落盘是抓日志方案的物理下限

### 新增
- 状态机调试日志 `log_state_debug.log`（有界 200KB），便于排查状态异常
- 单元测试 16 项（状态机全路径）

---

## v0.3.0 · 2026-08-06 深夜 —— 状态机重写（running 可见性 + 防误判）

### 修复：running 全程看不见（核心）
- 根因：`op.dispatch.user_input` 在工具 dispatch 时也写入，旧逻辑误判为新回合 thinking，
  工具一启动灯就变紫；工具周期的 response.completed 也被误当回合结束。
- 重写判定优先级：工具行(running) > response.completed > 真实用户消息 > 生成中(thinking)
- 工具行识别：ToolCall: / tool call completed / function_call 相关 SSE 与 trace /
  `function_call_arguments.delta`（模型生成工具参数时实时流式落盘，最早的 running 信号）
- 实测：18 秒 / 12 秒长命令全程青色，命令结束后 1~3 秒回紫

### 修复：假"用户消息"（submission.id 去重）
- 根因：`op.dispatch.user_input` 前缀出现在 post sampling token usage /
  Output item message/reasoning 等非用户消息上。
- 修法：只有"新的 submission.id"才算用户发消息；同 submission 内一律忽略；
  启动时预填最近 submission.id，重启不误触发。

### 新增
- 自动绑定最近使用的会话线程，其他会话的工具 trace 行不干扰，换会话自动跟随
- 参考案例调研：ThreadBeacon（同款日志抓取，承认工具执行期会失明）、
  AgentDiode（走官方 hooks，官方对 waiting 也无事件）、codex-micro-device（LED 协议专有无文档）、
  M5Stack Core2 开源固件（v2 HID 参考）、cmux issue（官方状态也过期）

---

## v0.2.0 · 2026-08-06 —— 日志被动检测（M2）

- 从"agent 自觉写状态文件"改为被动读取 Codex 桌面端事件日志
  （`~/.codex/logs_2.sqlite`），推导 thinking / running / done。
- 修复 done↔thinking 横跳：工具 dispatch 的 user_input 误判、防抖器首次返回 None 崩溃、
  旧守护进程残留写同一显示文件。
- 显示文件隔离为 `display_state_v2.json`。
- 回合结束判定：turn/completed 按内部迭代触发不可用，改"安静判定"。

---

## v0.1.0 · 2026-08-06 —— M1 原型

- 状态灯模拟器（置顶窗口，6 Agent 键 + 7 命令键 + 旋钮）
- 守护进程雏形：轮询 `.agent-status/agent-N.json`，输出灯效 JSON。
- 灯色约定：紫=thinking、青=running、琥珀=waiting、绿=done、灰=idle。

---

## 已知限制（记录在案）

- 日志 SSE 行不带线程归属，多会话同时活跃时次要会话事件可能短暂影响灯色。
- 日志库格式非稳定公开 API，Codex 升级后可能需要微调匹配串。
- waiting 审批态无法从日志被动检测，靠 agent 自觉上报 + 心跳兜底。
- 长回复时 app 侧最终文本完成行可能延迟/缺失（已用兜底通道覆盖）。
