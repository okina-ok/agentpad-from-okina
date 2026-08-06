"""状态文件模拟器：向状态文件夹写入/循环演示状态（M1 用）

用法：
    python demo_write.py                 # 写一轮静态示例
    python demo_write.py --loop          # 每 2 秒循环切换 1 号 agent 状态
    python demo_write.py --clear         # 清空所有状态文件
    python demo_write.py --dir X         # 指定状态文件夹
"""

import argparse
import json
import os
import time

DEFAULT_DIR = os.path.join(os.path.expanduser("~"), ".agent-status")
STATES = ["thinking", "running", "waiting", "done", "error", "idle"]


def write_status(directory, slot, state, summary=""):
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"agent-{slot}.json")
    payload = {
        "state": state,
        "ts": int(time.time()),
        "agent": f"agent-{slot}",
        "summary": summary,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    print(f"[demo] agent-{slot} -> {state}")


def main():
    parser = argparse.ArgumentParser(description="AgentPad 状态文件模拟器")
    parser.add_argument("--dir", default=DEFAULT_DIR)
    parser.add_argument("--loop", action="store_true", help="循环演示")
    parser.add_argument("--clear", action="store_true", help="清空状态文件")
    parser.add_argument("--interval", type=float, default=2.0)
    args = parser.parse_args()

    if args.clear:
        for slot in range(1, 7):
            path = os.path.join(args.dir, f"agent-{slot}.json")
            if os.path.exists(path):
                os.remove(path)
                print(f"[demo] removed {path}")
        return

    if not args.loop:
        samples = [
            (1, "thinking", "refactoring parser"),
            (2, "running", "running tests"),
            (3, "waiting", "needs your approval"),
            (4, "done", "review PR #42"),
            (5, "error", "build failed"),
        ]
        for slot, state, summary in samples:
            write_status(args.dir, slot, state, summary)
        return

    print(f"[demo] looping every {args.interval}s, Ctrl+C to stop")
    step = 0
    try:
        while True:
            step += 1
            # 按协议 v0.2：thinking <-> running 交替，周期性插一个 waiting
            if step % 4 == 0:
                write_status(args.dir, 1, "waiting", "needs your approval")
            elif step % 2 == 1:
                write_status(args.dir, 1, "thinking", "analyzing next step")
            else:
                write_status(args.dir, 1, "running", "running tool")
            write_status(args.dir, 2, "running", "always running")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n[demo] bye")


if __name__ == "__main__":
    main()
