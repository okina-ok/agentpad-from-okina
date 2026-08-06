"""串口监听工具：把 COM 口收到的内容保存到 serial_capture.txt（调试用）

用法：
    python serial_listen.py

说明：监听 120 秒内板子输出的所有字符，用于核对按键 JSON 事件是否正常。
"""

import serial
import time

import os

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "serial_capture.txt")

try:
    s = serial.Serial("COM5", 115200, timeout=0.5)
except Exception as exc:
    with open(out, "w", encoding="utf-8") as f:
        f.write("OPEN FAIL: %s\n" % exc)
    raise SystemExit

end = time.time() + 120
with open(out, "w", encoding="utf-8") as f:
    f.write("listening on COM5 @115200 for 30s...\n")
    f.flush()
    while time.time() < end:
        data = s.read(4096)
        if data:
            f.write(data.decode("utf-8", errors="replace"))
            f.flush()
    f.write("\n--- done ---\n")
s.close()
