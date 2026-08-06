"""AgentPad 守护进程看门狗：status_daemon.py 崩了自动拉起（日志在 logs/guard.log）"""

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "logs", "guard.log")


def log(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), msg))


def main():
    log("guard started")
    while True:
        p = subprocess.Popen(
            [sys.executable, os.path.join(HERE, "status_daemon.py")],
            cwd=HERE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        log("daemon started pid=%d" % p.pid)
        p.wait()
        log("daemon exited rc=%s, restarting in 3s" % p.returncode)
        time.sleep(3)


if __name__ == "__main__":
    main()
