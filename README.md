# AgentPad — Codex Micro 平替 RGB 状态键盘

一块能实时显示 AI agent（Codex / Claude Code 等）工作状态的 RGB 键盘，
是官方硬件设备的一个**开源平价替代**。

v1 只做**通道 B（本地状态文件 + 日志检测）**，不依赖 OpenAI 私有协议，
因此兼容 Codex 桌面端、Codex CLI 以及接了 DeepSeek 等其他模型的任何 agent。

> ⚠️ 项目处于实验/原型阶段，当前为个人 DIY + 学习用途，欢迎围观和提意见。

## 这个项目是什么（给第一次来的人）

官方有一款卖得很贵的"状态键盘"硬件（Codex Micro）。我们想搞清楚它到底值不值，
于是从零复刻了一遍，并把每一步都开源出来：

- **实时状态灯**：6 个 Agent 槽位显示 thinking / running / waiting / done，
  转场延迟控制在 1 秒左右，回复一出来灯就变绿；
- **PTT 语音输入**：按住按键说话 → 松开 → 本地离线转写 → 文字自动进输入框，
  审阅后发送（不用官方语音通道也能用）；
- **不绑定官方生态**：靠"本地状态文件 + 桌面端日志"推导状态，
  正版 Codex、Codex CLI、以及换模型（DeepSeek 等）都兼容；
- **目标是一个小硬件**：状态灯 + 语音按键的 RGB 键盘，自己用或小规模售卖。

技术栈：Python（PC 侧守护 / 转写 / 注入）+ MicroPython（ESP32-C3 实验板）+
WS2812 RGB（成品规划）。

## 当前进度一览

| 模块 | 状态 | 说明 |
| --- | --- | --- |
| 状态灯链路（通道 B） | ✅ 完成 | 日志 → thinking/running/done → 渲染；工具结束回 thinking ≈ 1s |
| done 兜底判定 | ✅ 完成 | app 不写完成行时也能在 ≤5s 内亮绿 |
| 多会话跟随 | ✅ 完成 | 自动跟随最近使用的会话，其他会话事件不干扰 |
| PTT 语音输入 | ✅ PC 侧完成 | 按键录音 → faster-whisper 本地转写（离线，中文实测通过） |
| 自动注入输入框 | ✅ 完成 | 转写文字自动填入 Codex 输入框（UIA 定位，窗口不全屏也准） |
| 硬件按键链路 | ✅ 实验通过 | ESP32-C3 板载按键 → 串口 JSON（MicroPython，去抖正常） |
| 实体 RGB 键盘固件 | 🚧 规划中 | WS2812 ×13 + 串口桥（路线图第 6 步） |
| 打包安装包 | 🚧 规划中 | PyInstaller + Inno Setup，一键安装即用（第 7 步） |
| 通道 A（官方 HID 协议） | ⏳ 未开始 | 依赖 macOS + 官方私有协议，不在 v1 范围 |

实测片段：按住 BOOT 键说"帮我写一个快速排序算法"，松开后约 2 秒转写出
完全正确的文字，随后自动填入 Codex 输入框，全程离线。

## 目录结构

```text
status_daemon.py    守护进程：日志检测 + 状态合并，输出 display_state_v2.json
log_state.py        事件流状态机（日志行 -> thinking/running/done）
simulator.py        电脑上模拟 AgentPad 灯效的置顶窗口
ptt.py              PTT 语音原型（录音 + faster-whisper 转写）
button_test.py      板子按键测试固件（MicroPython / ESP32-C3，上传为 /main.py）
upload_main.py      通过串口把固件写入板子（base64 + raw REPL）
serial_listen.py    串口监听工具（调试用）
bind_panel.py       频道绑定面板：把对话绑定到 1-6 频道（不跟 AI 交流）
channel_map.json    频道映射（manual=手工 / auto=自动分配）
demo_write.py       状态文件演示写入器
hooks/              agent 自觉上报 waiting 的约定脚本
docs/               需求规格 / 更新日志 / 路线图 / 硬件方案
whisper-small/      本地转写模型（model.bin 不随仓库分发，见下）
```

## 快速开始（PC 侧）

```powershell
python status_daemon.py     # 终端 1：状态守护进程
python simulator.py         # 终端 2：模拟器窗口（置顶）
```

然后打开 Codex 使用，模拟器会实时显示 6 个 Agent 槽位的状态灯。

灯色约定：紫=思考、青=工具执行、琥珀=等待、绿=完成、灰=空闲。

## 硬件实验（ESP32-C3 按键测试 + PTT）

1. 给板子刷 MicroPython：`python -m esptool --chip esp32c3 --port COMx write_flash --erase-all -z 0x0 固件.bin`
2. 用 `upload_main.py` 把 `button_test.py` 写入板子 `/main.py`
3. 按板载 BOOT 键（GPIO9）→ 串口输出 `{"ev":"key","i":1,"s":1/0}`
4. 跑 `python ptt.py --port COMx`：按住 BOOT 说话，松开自动转文字

## 语音转写模型（不随仓库分发）

`ptt.py` 依赖 faster-whisper small 模型（约 460MB），下载后放入 `whisper-small/model.bin`：

```
https://hf-mirror.com/Systran/faster-whisper-small/resolve/main/model.bin
```

## 注意事项

1. **日志格式非稳定 API**：状态检测读取 Codex 桌面端日志库（`~/.codex/logs_*.sqlite`），
   Codex 升级后匹配串可能需要微调（位置已在 `log_state.py` 注释标注）。
2. **多会话干扰**：日志的 SSE 行无法区分会话，多个 Codex 会话同时活跃时，
   次要会话事件可能短暂影响灯色；以"最近使用的会话"为准，单会话场景无影响。
3. **waiting 审批态**：桌面端日志不暴露审批事件，等待状态靠 agent 自觉上报
   （`hooks/write_status.ps1` 约定）+ 心跳兜底。
4. **模型许可**：faster-whisper 模型文件（MIT/Whisper 许可）需自行下载，
   注意其许可以及所在地法规要求。
5. **本项目与 OpenAI 无隶属/背书关系**；不涉及 OpenAI 私有协议。
6. 代码仅供学习与自用参考，请遵守相关平台条款。

## 文档

- [需求规格](docs/codex-micro-replica-spec-v0.1.md)
- [更新日志](docs/AgentPad-CHANGELOG.md)
- [用户手册（草稿）](docs/agentpad-user-manual-draft.md)
- [动手路线图](docs/agentpad-steps.md)
- [v1 硬件方案](docs/v1-hardware-plan.md)
