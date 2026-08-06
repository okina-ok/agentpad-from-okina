"""后台下载 faster-whisper small 模型（走 hf-mirror，禁用 Xet）"""

import os
import sys
import time

os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["HF_HUB_DISABLE_XET"] = "1"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dl_model.log")


def log(msg):
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), msg))


log("start download")
try:
    from huggingface_hub import snapshot_download

    path = snapshot_download("Systran/faster-whisper-small")
    log("DONE: %s" % path)
except Exception as exc:
    log("ERROR: %r" % exc)
    sys.exit(1)
