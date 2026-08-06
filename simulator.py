"""AgentPad 灯效模拟器（M1 原型）

读取 status_daemon.py 输出的 display_state.json，在电脑上渲染
6 个 Agent 键 + 7 个命令键 + 旋钮的 RGB 效果，验证"状态 -> 灯效"链路。

用法：
    python status_daemon.py       # 终端 1
    python demo_write.py --loop   # 终端 2
    python simulator.py           # 终端 3
"""

import json
import math
import os
import time
import tkinter as tk

DISPLAY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "display_state_v2.json")

AGENT_KEYS = [
    ("A1", 0, 0), ("A2", 0, 1), ("A3", 0, 2),
    ("A4", 1, 0), ("A5", 1, 1), ("A6", 1, 2),
]
COMMAND_KEYS = ["接受", "拒绝", "新建", "分支", "PTT", "停止", "重试"]

W, H = 660, 470
KEY_W, KEY_H = 92, 58
GAP = 14
FPS = 20


def hex_to_rgb(color):
    color = (color or "#555555").lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


def blend(rgb, base, factor):
    return tuple(int(base[i] + (rgb[i] - base[i]) * factor) for i in range(3))


def rgb_to_hex(rgb):
    return "#{:02X}{:02X}{:02X}".format(*rgb)


class Simulator(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AgentPad 模拟器 (M1)")
        self.geometry(f"{W}x{H}")
        self.configure(bg="#111111")
        self.attributes("-topmost", True)  # 常驻模式当桌面挂件用，置顶
        self.agent_frames = []
        self.command_frames = []
        self.encoder_canvas = None
        self.last_payload = None
        self.t = 0.0
        self.build_ui()
        self.after(int(1000 / FPS), self.tick)

    def build_ui(self):
        title = tk.Label(self, text="AgentPad 状态模拟器（通道 B）", bg="#111111",
                         fg="#CCCCCC", font=("Microsoft YaHei UI", 12, "bold"))
        title.pack(pady=8)

        grid = tk.Frame(self, bg="#111111")
        grid.pack()
        for label, row, col in AGENT_KEYS:
            cell = tk.Frame(grid, width=KEY_W, height=KEY_H, bg="#222222",
                            highlightbackground="#333333", highlightthickness=1)
            cell.grid(row=row, column=col, padx=GAP // 2, pady=GAP // 2)
            cell.pack_propagate(False)
            cap = tk.Label(cell, text=label, bg="#222222", fg="#AAAAAA",
                           font=("Consolas", 13, "bold"))
            cap.pack(expand=True)
            self.agent_frames.append((cell, cap))

        cmd_row = tk.Frame(self, bg="#111111")
        cmd_row.pack(pady=10)
        for i, label in enumerate(COMMAND_KEYS):
            cell = tk.Frame(cmd_row, width=KEY_W - 12, height=44, bg="#262626",
                            highlightbackground="#333333", highlightthickness=1)
            cell.grid(row=0, column=i, padx=4)
            cell.pack_propagate(False)
            cap = tk.Label(cell, text=label, bg="#262626", fg="#999999",
                           font=("Microsoft YaHei UI", 10))
            cap.pack(expand=True)
            self.command_frames.append((cell, cap))

        self.encoder_canvas = tk.Canvas(self, width=100, height=104, bg="#111111",
                                        highlightthickness=0)
        self.encoder_canvas.pack(pady=4)

    def tick(self):
        self.t += 1.0 / FPS
        payload = None
        try:
            with open(DISPLAY_FILE, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError):
            payload = None
        if payload and payload.get("agents"):
            self.last_payload = payload
        if self.last_payload:
            self.render(self.last_payload)
        self.after(int(1000 / FPS), self.tick)

    def render(self, payload):
        agents = payload["agents"]
        for (cell, cap), info in zip(self.agent_frames, agents):
            rgb = hex_to_rgb(info.get("color"))
            base = (24, 24, 24)
            effect = info.get("effect", "off")
            fresh = info.get("fresh", "offline")
            f = 1.0
            if effect == "breathe":
                f = 0.45 + 0.55 * (math.sin(self.t * 2 * math.pi) + 1) / 2
            elif effect == "flash":
                f = 1.0 if int(self.t * 2) % 2 == 0 else 0.15
            elif effect == "off":
                f = 0.05
            if fresh in ("suspect", "stale", "offline"):
                f *= 0.4
            color = rgb_to_hex(blend(rgb, base, f))
            cell.configure(bg=color)
            text = f"{info.get('slot', '?')} {info.get('state', '?')}"
            if fresh in ("suspect", "stale", "offline"):
                text = f"{info.get('slot', '?')} {fresh}"
            cap.configure(text=text, bg=color,
                          fg="#FFFFFF" if f > 0.35 else "#777777")

        for cell, cap in self.command_frames:
            cell.configure(bg="#262626")
            cap.configure(bg="#262626")

        self.encoder_canvas.delete("all")
        self.encoder_canvas.create_oval(22, 18, 78, 74, outline="#666666", width=8)
        self.encoder_canvas.create_text(50, 52, text="推理", fill="#888888",
                                        font=("Microsoft YaHei UI", 9))
        self.encoder_canvas.create_text(50, 96, text="旋钮（v1）", fill="#555555",
                                        font=("Microsoft YaHei UI", 8))


if __name__ == "__main__":
    Simulator().mainloop()
