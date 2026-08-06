import serial
import time

out = r"C:\Users\Win\Documents\Codex\2026-08-06\new-chat-2\work\agentpad\serial_capture.txt"

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
