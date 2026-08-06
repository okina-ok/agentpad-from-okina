"""后台下载 faster-whisper-small 的 model.bin（curl 断点续传）"""

import os
import subprocess
import sys
import time

URL = "https://hf-mirror.com/Systran/faster-whisper-small/resolve/main/model.bin"
DEST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "whisper-small", "model.bin")
LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dl_model_bin.log")


def log(msg):
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), msg))


log("start curl download -> %s" % DEST)
try:
    r = subprocess.run(
        ["curl.exe", "-sL", "-C", "-", "-o", DEST, URL],
        capture_output=True,
        timeout=1500,
    )
    size = os.path.getsize(DEST) if os.path.exists(DEST) else 0
    log("curl rc=%d size=%d" % (r.returncode, size))
    if size > 400 * 1024 * 1024:
        log("DONE")
    else:
        log("INCOMPLETE: %s" % r.stderr.decode("utf-8", errors="replace")[-500:])
except Exception as exc:
    log("ERROR: %r" % exc)
