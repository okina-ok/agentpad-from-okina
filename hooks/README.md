# Codex CLI hooks 样例（通道 B 的"写入端"）

真实场景下，AI agent 通过 hooks 自动把状态写进 `%USERPROFILE%\.agent-status\`，
不需要靠提示词，也不花 token。本目录是 M1 阶段的样例配置，真实接入待 M1 实测后回填。

## Codex CLI（config.toml）

编辑 `%USERPROFILE%\.codex\config.toml`，追加（路径按实际安装位置改）：

```toml
[hooks]
UserPromptSubmit = [{ command = "powershell -NoProfile -ExecutionPolicy Bypass -File \"C:\\path\\to\\agentpad\\hooks\\write_status.ps1\" -State thinking -Agent 1 -Summary \"start\"" }]
PreToolUse = [{ command = "powershell -NoProfile -ExecutionPolicy Bypass -File \"C:\\path\\to\\agentpad\\hooks\\write_status.ps1\" -State running -Agent 1 -Summary \"tool\"" }]
PermissionRequest = [{ command = "powershell -NoProfile -ExecutionPolicy Bypass -File \"C:\\path\\to\\agentpad\\hooks\\write_status.ps1\" -State waiting -Agent 1 -Summary \"approval\"" }]
Stop = [{ command = "powershell -NoProfile -ExecutionPolicy Bypass -File \"C:\\path\\to\\agentpad\\hooks\\write_status.ps1\" -State done -Agent 1 -Summary \"done\"" }]
```

注意：

- 上面的写法是"单 agent 固定槽位 1"的简化样例；多 agent 按会话路由需要在 M1 实测阶段补（用 stdin 传入的 hook JSON 判断 session/agent）。
- `PermissionRequest` 正好对应"等待你审批"状态，是 waiting 灯的核心来源。
- Claude Code 的 hooks 事件名不同（`UserPromptSubmit` / `Stop` 等），但写入格式完全一样，守护进程不区分来源。

## 手动测试单个 hook

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\write_status.ps1 -State running -Agent 1 -Summary "test"
```
