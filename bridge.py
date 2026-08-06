"""AgentPad 串口桥（PC → 键盘灯珠）

读取 status_daemon.py 输出的 display_state_v2.json，把 6 个 agent 槽位的
状态/颜色/灯效打包成串口帧发给键盘（单行 JSON）；同时接收键盘上报的
按键事件与 ACK。

用法：
    python bridge.py --port COM5              # 接实体键盘（默认）
    python bridge.py --test                   # 测试模式：循环推各状态，验证灯珠

协议 v0.1：
    下行 {"v":1,"seq":N,"keys":[{"i":1,"s":"running","c":"#00E5A0","e":"solid"},...]}
    上行 {"v":1,"ack":N}  /  {"v":1,"ev":"key","i":1,"s":1/0}
"""

import argparse
import json
import os
import time

try:
    import serial
except ImportError:
    serial = None

DISPLAY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "display_state_v2.json")


class Bridge:
    def __init__(self, port, interval=0.1, display_file=DISPLAY_FILE):
        self.port = port
        self.interval = interval
        self.display_file = display_file
        self.seq = 0
        self.last_sig = None
        self.pending_seq = None
        self.pending_bytes = None
        self.retries = 0
        self.last_send_ts = 0.0
        self._rx_buf = ""
        self.s = None

    def connect(self):
        if serial is None:
            raise SystemExit("pyserial 未安装：pip install pyserial")
        self.s = serial.Serial(self.port, 115200, timeout=0.05)
        print(f"[bridge] {self.port} connected")

    def send(self, payload):
        self.seq += 1
        payload.setdefault("v", 1)
        payload["seq"] = self.seq
        frame = json.dumps(payload, ensure_ascii=False) + "\n"
        self.s.write(frame.encode("utf-8"))
        self.pending_seq = self.seq
        self.pending_bytes = frame
        self.retries = 0
        self.last_send_ts = time.time()
        print(f"[bridge] -> #{self.seq} {frame.strip()[:130]}")

    def drain(self):
        """读板子回包/按键事件。"""
        try:
            n = self.s.in_waiting or 1
            data = self.s.read(n)
        except Exception:
            return
        if not data:
            return
        # 半包缓冲：串口数据可能被拆成多段，按 \n 拼成完整行再处理
        self._rx_buf += data.decode("utf-8", errors="replace")
        while "\n" in self._rx_buf:
            line, self._rx_buf = self._rx_buf.split("\n", 1)
            self._handle_line(line.strip())

    def _handle_line(self, line):
        if not line:
            return
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            print(f"[bridge] raw: {line[:100]}")
            return
        ack = ev.get("ack")
        if ack is not None:
            print(f"[bridge] <- ack #{ack}")
            if ack == self.pending_seq:
                self.pending_seq = None
        else:
            print(f"[bridge] <- {line[:110]}")

    def poll(self):
        """读状态文件，内容变化才下发。"""
        try:
            with open(self.display_file, "r", encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return
        sig = json.dumps(d.get("agents", []), ensure_ascii=False)
        if sig == self.last_sig:
            return
        self.last_sig = sig
        keys = []
        for a in d.get("agents", []):
            keys.append({
                "i": a.get("slot", 1),
                "s": a.get("state", "idle"),
                "c": a.get("color", "#555555"),
                "e": a.get("effect", "off"),
            })
        self.send({"keys": keys})

    def tick(self):
        self.poll()
        self.drain()
        if self.pending_seq is not None and time.time() - self.last_send_ts >= 0.5:
            if self.retries >= 3:
                print(f"[bridge] 丢帧 #{self.pending_seq}（无 ACK）")
                self.pending_seq = None
            else:
                self.s.write(self.pending_bytes.encode("utf-8"))
                self.retries += 1
                self.last_send_ts = time.time()
                print(f"[bridge] 重发 #{self.pending_seq} ({self.retries}/3)")

    def loop(self):
        self.connect()
        while True:
            try:
                self.tick()
            except KeyboardInterrupt:
                print("\n[bridge] bye")
                break
            except Exception as exc:
                print(f"[bridge] error: {exc}")
            time.sleep(self.interval)


def test_loop(port):
    """测试模式：每 2 秒推一个状态，覆盖全部颜色/灯效。"""
    b = Bridge(port)
    b.connect()
    states = [
        ("thinking", "#9B59FF", "breathe"),
        ("running", "#00E5A0", "solid"),
        ("waiting", "#FFB000", "flash"),
        ("done", "#22C55E", "solid"),
        ("error", "#FF3B4E", "flash"),
        ("idle", "#555555", "off"),
    ]
    i = 0
    while True:
        s, c, e = states[i % len(states)]
        b.send({"keys": [{"i": 1, "s": s, "c": c, "e": e}]})
        i += 1
        for _ in range(20):  # 2 秒
            try:
                b.drain()
            except Exception:
                pass
            time.sleep(0.1)


def main():
    ap = argparse.ArgumentParser(description="AgentPad 串口桥")
    ap.add_argument("--port", default="COM5")
    ap.add_argument("--interval", type=float, default=0.1)
    ap.add_argument("--file", default=DISPLAY_FILE)
    ap.add_argument("--test", action="store_true", help="测试模式：循环推状态")
    args = ap.parse_args()
    if args.test:
        test_loop(args.port)
    else:
        Bridge(args.port, args.interval, args.file).loop()


if __name__ == "__main__":
    main()
