"""通过串口把 button_test.py 以 base64 写入板子 /main.py（不依赖 ampy）"""

import base64
import serial
import sys
import time

SRC = r"C:\Users\Win\Documents\Codex\2026-08-06\new-chat-2\work\agentpad\button_test.py"
PORT = "COM5"

with open(SRC, "rb") as fh:
    payload = base64.b64encode(fh.read()).decode()

code = (
    "import ubinascii\n"
    "f=open('/main.py','wb')\n"
    "f.write(ubinascii.a2b_base64('%s'))\n"
    "f.close()\n"
    "print('UPLOAD_OK')\n" % payload
)

s = serial.Serial(PORT, 115200, timeout=2)
time.sleep(0.3)
s.reset_input_buffer()

# 进入 raw REPL
s.write(b"\x03\x03")      # Ctrl+C 中断当前程序
time.sleep(0.3)
s.write(b"\x01")          # Ctrl+A 进 raw REPL
time.sleep(0.3)
s.reset_input_buffer()

s.write(code.encode())
s.write(b"\x04")          # Ctrl+D 执行
time.sleep(1.5)
out = s.read(4000).decode("utf-8", errors="replace")
print(out)

if "UPLOAD_OK" in out:
    print("UPLOAD SUCCESS")
else:
    print("UPLOAD FAILED")
    sys.exit(1)

# 退回正常 REPL 并软复位，让 main.py 自动运行
s.write(b"\x02")          # Ctrl+B 退出 raw REPL 回普通 REPL
time.sleep(0.3)
s.write(b"\x04")          # 普通 REPL 下 Ctrl+D = 软复位
time.sleep(2.0)
out = s.read(4000).decode("utf-8", errors="replace")
print("--- boot after soft reset ---")
print(out)
s.close()
