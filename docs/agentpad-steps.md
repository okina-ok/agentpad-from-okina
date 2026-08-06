# AgentPad 动手路线图（进度跟踪）

> 更新：2026-08-07 —— 板子确认为 **ESP32-C3**（不是 S3），MicroPython v1.28.0 已刷入，
> 按键测试已通过（用板载 BOOT 按钮 GPIO9，无外接按键）。

---

**第 1 步：清点硬件 + 装驱动** ✅
- 板子：ESP32-C3 开发板（带 BOOT/RST 按钮）
- 串口：CP2102 USB-TTL 转接 → COM5（驱动已装）
- 无外接按键 → 用板载 BOOT 按钮（GPIO9）替代

**第 2 步：装工具 + 刷 MicroPython** ✅
- esptool 已装（`python -m esptool`）
- 固件：ESP32_GENERIC_C3-20260406-v1.28.0.bin，已刷入并验证
- 刷机要点：先按 BOOT+RST 进下载模式，再 `write_flash --erase-all -z 0x0`

**第 3 步：按键测试（最小闭环）** ✅
- 固件：`work\agentpad\button_test.py`（上传为板子 /main.py，通过串口 base64 写入）
- 结果：BOOT 键按下/松开各发 `{"ev":"key","i":1,"s":1/0}`，
  PC 监听 4 次循环全部正确，去抖正常，链路打通

**第 4 步：PTT 语音原型**（下一步）
- PC 端：串口收到"按下"→ 开始录麦克风；"松开"→ 停止录音
- 本地转写：faster-whisper（small 模型，离线中文）
- 验收：按住 BOOT 说话，松开后文字出来

**第 5 步：文字进 Codex 输入框**
- 转好的文字自动填入输入框（剪贴板+粘贴 / UI 自动化），审阅后发送
- 需要确认目标：桌面端还是 CLI

**第 6 步：状态灯接实体硬件**
- 购买 WS2812 灯珠 ×13（或灯条裁剪），固件加灯珠驱动 + 串口桥

**第 7 步：打包成安装包**
- PyInstaller + Inno Setup，自动发现 COM 口 / Codex 日志库

---

## 关键技术记录

- 刷机：`python -m esptool --chip esp32c3 --port COM5 --baud 460800 write_flash --erase-all -z 0x0 固件.bin`
- 上传 main.py（无 ampy）：`work\agentpad\upload_main.py`（串口 raw REPL + base64）
- 板子复位跑 main.py：串口发 Ctrl+B 退 raw REPL，再 Ctrl+D 软复位
- 注意：上传前先关掉占用 COM5 的监听程序
