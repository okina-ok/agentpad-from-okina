"""通过 Codex CLI 直接向 app 线程发送消息（流接口，无 UI 自动化）。

原理：app 的线程与 CLI 共用 ~/.codex/sessions/ 下的 rollout 文件，
`codex exec resume <thread_id> <text>` 会恢复该线程并发起新回合。
配置里 approval_policy="never"、全权限，CLI 可无交互直接跑。

用法：
    python codex_send.py "<文字>"                # 发到当前活跃线程
    python codex_send.py "<文字>" <thread_id>    # 发到指定线程
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(HERE, "multi_state_cache.json")

# 优先 app 内置的 codex.exe；PATH 里有 codex 则回退
CODEX_EXE = os.environ.get(
    "AGENTPAD_CODEX_EXE",
    r"D:\HuaweiMoveData\Users\Win\Codex-zh-CN\app\resources\codex.exe",
)
if not os.path.exists(CODEX_EXE):
    CODEX_EXE = shutil.which("codex") or CODEX_EXE


def active_thread_id():
    """当前活跃线程（状态守护进程记录的最近有用户输入的会话）。"""
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return d.get("active_tid") or ""
    except Exception:
        return ""


def send_text(text, thread_id=None, timeout=45.0):
    """向线程发送文字，进程在 turn.started 出现后即返回（后台继续跑）。

    返回 (ok, detail)。ok 只代表"消息已进入执行"，不表示回合完成。
    """
    text = (text or "").strip()
    if not text:
        return False, "没有可发送的文字（先按住语音键说话）"
    tid = thread_id or active_thread_id()
    if not tid:
        return False, "找不到当前活跃线程"
    try:
        p = subprocess.Popen(
            [CODEX_EXE, "exec", "resume", tid, text, "--json"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:
        return False, "启动失败: %r" % exc

    deadline = time.time() + timeout
    while time.time() < deadline:
        line = p.stdout.readline()
        if not line:
            break
        line = line.strip()
        if '"turn.started"' in line or '"thread.started"' in line:
            # 后台线程接管剩余输出：保持管道打开，子进程才能独立跑完回合
            # （父进程退出时管道关闭会导致回合中止，这里防止 send_text 返回后管道被回收）
            threading.Thread(target=lambda: p.stdout.read(), daemon=True).start()
            return True, "已进入执行（后台运行中）"
        if "error" in line.lower() and "warn" not in line.lower():
            return False, line[:200]
    try:
        p.kill()
    except Exception:
        pass
    return False, "启动超时（%ds）" % timeout


def main():
    if len(sys.argv) < 2:
        print("用法: python codex_send.py \"<文字>\" [thread_id]")
        return 1
    text = sys.argv[1]
    tid = sys.argv[2] if len(sys.argv) > 2 else None
    ok, detail = send_text(text, tid)
    print(("OK " if ok else "FAIL ") + detail)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
