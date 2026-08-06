# AgentPad —— Codex Micro 平替 RGB 状态键盘（通道 B）

一块能跟随 Codex / Claude Code 等 AI agent 状态的 RGB 键盘，v1 走本地状态文件 + 日志检测。
项目文档与需求规格见 `docs/`（或仓库上级 outputs 目录），产品说明见
[规格书](../../outputs/codex-micro-replica-spec-v0.1.md)。

## 组成

- `status_daemon.py` / `log_state.py` —— PC 守护进程：读 Codex 日志推导 thinking/running/done，输出 `display_state_v2.json`
- `simulator.py` —— 电脑上模拟 AgentPad 灯效的置顶窗口
- `button_test.py` —— 板子（MicroPython / ESP32-C3）按键测试固件，上传为 `/main.py`
- `upload_main.py` —— 通过串口把固件写入板子（base64 + raw REPL）
- `ptt.py` —— PTT 语音原型：按住按键说话，松开本地转写（faster-whisper）
- `whisper-small/` —— 本地转写模型（**不随仓库分发**，需自行下载，见下）

## 模型文件（不随仓库分发）

`model.bin`（约 460MB）下载自 faster-whisper-small 仓库后放入 `whisper-small/`：

```
https://hf-mirror.com/Systran/faster-whisper-small/resolve/main/model.bin
```

## 快速开始（PC 侧）

```powershell
python status_daemon.py     # 终端 1：状态守护进程
python simulator.py         # 终端 2：模拟器窗口
```

详见 docs / 桌面报告。
