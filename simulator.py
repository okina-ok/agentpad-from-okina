"""AgentPad 模拟器（Codex Micro 布局版）

模拟 Codex Micro 官方键盘的整体布局：
  顶行：左摇杆 + 6 颗半透明 Agent 键 + 右旋钮（推理深度）
  下行：左触摸区（图层切换）+ 1.54" 屏幕（我们的差异化卖点）+ 7 颗命令键

实时读取 status_daemon.py 输出的 display_state_v2.json，
把 6 个频道的会话状态渲染成对应灯效，验证「状态 -> 灯效」链路。

用法：
    python status_daemon.py       # 终端 1（通常已被 guard_daemon 自动拉起）
    python simulator.py           # 终端 2，弹出模拟器窗口
"""

import json
import math
import os
import tkinter as tk

DISPLAY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "display_state_v2.json")

# ---------------- 布局常量（想微调键位尺寸/坐标，只改这里） ----------------
BG = "#111111"
KEY_BG = "#1E1E1E"
KEY_BORDER = "#3A3A3A"
TEXT_DIM = "#8A8A8A"
TEXT_BRIGHT = "#FFFFFF"
ACCENT = "#F59E0B"  # PTT 高亮

AGENT_KEYS = [f"A{i}" for i in range(1, 7)]           # 顶行 6 颗 Agent 键
COMMAND_KEYS = ["接受", "拒绝", "新建", "分支", "PTT", "停止", "重试"]

GAP = 8
KEY_W, KEY_H = 84, 64          # Agent 键
CMD_W, CMD_H = 62, 44          # 命令键
SIDE_W, SIDE_H = 84, 84        # 摇杆 / 旋钮
TOUCH_W, TOUCH_H = 84, 52      # 触摸区
SCREEN_W, SCREEN_H = 180, 52   # 1.54" 屏幕占位（约 2 颗键宽）
FPS = 20

# 状态 -> 中文短标签（模拟器展示用）
STATE_ZH = {
    "thinking": "思考",
    "running": "运行",
    "waiting": "等待",
    "done": "完成",
    "idle": "空闲",
    "suspect": "可疑",
    "stale": "过期",
}


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
        self.title("AgentPad 模拟器 · Codex Micro 布局")
        self.configure(bg=BG)
        self.attributes("-topmost", True)  # 常驻模式当桌面挂件用，置顶
        self.agent_frames = []   # [(cell, cap)]
        self.command_frames = []  # [(cell, cap)]
        self.joy_canvas = None
        self.knob_canvas = None
        self.screen_line1 = None
        self.screen_line2 = None
        self.last_payload = None
        self.t = 0.0
        self.build_ui()
        # 由布局推导窗口尺寸
        row0_w = SIDE_W + 6 * KEY_W + SIDE_W + 8 * GAP
        row1_w = TOUCH_W + SCREEN_W + 7 * CMD_W + 8 * GAP
        self.geometry(f"{max(row0_w, row1_w) + 24}x{320}")
        self.after(int(1000 / FPS), self.tick)

    # ---------------- UI 搭建 ----------------
    def build_ui(self):
        title = tk.Label(self, text="AgentPad 模拟器 · Codex Micro 布局", bg=BG,
                         fg="#CCCCCC", font=("Microsoft YaHei UI", 12, "bold"))
        title.pack(pady=(10, 6))

        # 顶行：摇杆 | A1..A6 | 旋钮
        top = tk.Frame(self, bg=BG)
        top.pack()
        self.joy_canvas = tk.Canvas(top, width=SIDE_W, height=SIDE_H, bg=BG,
                                    highlightthickness=0)
        self.joy_canvas.grid(row=0, column=0, padx=GAP // 2, pady=2)
        for i in range(6):
            cell = tk.Frame(top, width=KEY_W, height=KEY_H, bg=KEY_BG,
                            highlightbackground=KEY_BORDER, highlightthickness=1)
            cell.grid(row=0, column=i + 1, padx=GAP // 2, pady=2)
            cell.pack_propagate(False)
            cap = tk.Label(cell, text=f"A{i + 1} · 空闲", bg=KEY_BG, fg=TEXT_DIM,
                           font=("Microsoft YaHei UI", 9, "bold"))
            cap.pack(expand=True)
            self.agent_frames.append((cell, cap))
        self.knob_canvas = tk.Canvas(top, width=SIDE_W, height=SIDE_H, bg=BG,
                                     highlightthickness=0)
        self.knob_canvas.grid(row=0, column=7, padx=GAP // 2, pady=2)

        # 下行：触摸区 | 屏幕 | 7 颗命令键
        bottom = tk.Frame(self, bg=BG)
        bottom.pack(pady=(10, 6))
        touch = tk.Frame(bottom, width=TOUCH_W, height=TOUCH_H, bg="#181818",
                         highlightbackground=KEY_BORDER, highlightthickness=1)
        touch.grid(row=0, column=0, padx=GAP // 2, pady=2)
        touch.pack_propagate(False)
        tk.Label(touch, text="触摸区\n图层切换", bg="#181818", fg=TEXT_DIM,
                 font=("Microsoft YaHei UI", 9)).pack(expand=True)

        screen = tk.Frame(bottom, width=SCREEN_W, height=SCREEN_H,
                          bg="#0A1420", highlightbackground="#1E3A5F",
                          highlightthickness=1)
        screen.grid(row=0, column=1, padx=GAP // 2, pady=2)
        screen.pack_propagate(False)
        self.screen_line1 = tk.Label(screen, text="就绪", bg="#0A1420",
                                     fg="#7CFC00", font=("Consolas", 12, "bold"))
        self.screen_line1.pack(pady=(4, 0))
        self.screen_line2 = tk.Label(screen, text="😎 电量 100% · 6 会话",
                                     bg="#0A1420", fg="#58A6FF",
                                     font=("Microsoft YaHei UI", 8))
        self.screen_line2.pack()

        for i, label in enumerate(COMMAND_KEYS):
            cell = tk.Frame(bottom, width=CMD_W, height=CMD_H, bg="#222222",
                            highlightbackground=KEY_BORDER, highlightthickness=1)
            cell.grid(row=0, column=i + 2, padx=GAP // 2, pady=2)
            cell.pack_propagate(False)
            fg = ACCENT if label == "PTT" else "#9A9A9A"
            cap = tk.Label(cell, text=label, bg="#222222", fg=fg,
                           font=("Microsoft YaHei UI", 10))
            cap.pack(expand=True)
            self.command_frames.append((cell, cap))

        legend = tk.Label(self, text="紫=思考  青=运行  琥珀=等待  绿=完成  灰=空闲",
                          bg=BG, fg="#666666", font=("Microsoft YaHei UI", 8))
        legend.pack(pady=(2, 6))

    # ---------------- 主循环 ----------------
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
        agents = payload.get("agents") or []
        for (cell, cap), info in zip(self.agent_frames, agents):
            rgb = hex_to_rgb(info.get("color"))
            base = (30, 30, 30)
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
                f *= 0.4  # 新鲜度不足：整体压暗，不显示 STALE/SUSPECT 字样
            color = rgb_to_hex(blend(rgb, base, f))
            cell.configure(bg=color)
            slot = info.get("slot", "?")
            state = STATE_ZH.get(info.get("state"), info.get("state", "?"))
            cap.configure(text=f"A{slot} {state}", bg=color,
                          fg=TEXT_BRIGHT if f > 0.35 else "#777777")

        for cell, cap in self.command_frames:
            cell.configure(bg="#222222")
            cap.configure(bg="#222222")

        # 屏幕：显示当前最活跃频道的状态 + 电量（v1 固定占位）
        active = self._pick_active(agents)
        if active:
            st = STATE_ZH.get(active.get("state"), active.get("state", "?"))
            slot = active.get("slot", "?")
            self.screen_line1.configure(text=f"A{slot} · {st}")
        else:
            self.screen_line1.configure(text="就绪")
        total = sum(1 for a in agents if a.get("src") != "empty")
        self.screen_line2.configure(text=f"😎 电量 100% · {total} 会话")

        self._draw_joy()
        self._draw_knob(active)

    @staticmethod
    def _pick_active(agents):
        """优先取 thinking/running/waiting，其次取 fresh=active，否则 None。"""
        hot = ("thinking", "running", "waiting")
        for info in agents:
            if info.get("state") in hot:
                return info
        for info in agents:
            if info.get("fresh") == "active":
                return info
        return None

    # ---------------- 摇杆 / 旋钮 ----------------
    def _draw_joy(self):
        c = self.joy_canvas
        c.delete("all")
        w, h = SIDE_W, SIDE_H
        cx, cy = w // 2, h // 2
        arm, thick = 22, 12
        c.create_rectangle(cx - arm, cy - thick // 2, cx + arm, cy + thick // 2,
                           fill="#333333", outline="")
        c.create_rectangle(cx - thick // 2, cy - arm, cx + thick // 2, cy + arm,
                           fill="#333333", outline="")
        c.create_oval(cx - 8, cy - 8, cx + 8, cy + 8, fill="#4A4A4A", outline="")
        c.create_text(cx, cy - arm - 6, text="▲", fill="#666666",
                      font=("Microsoft YaHei UI", 8))

    def _draw_knob(self, active):
        c = self.knob_canvas
        c.delete("all")
        w, h = SIDE_W, SIDE_H
        cx, cy = w // 2, h // 2
        r = 24
        color = "#666666"
        if active:
            rgb = hex_to_rgb(active.get("color"))
            color = rgb_to_hex(blend(rgb, (90, 90, 90), 0.6))
        c.create_oval(cx - r, cy - r, cx + r, cy + r, outline=color, width=6)
        ang = self.t * 0.8
        px = cx + (r - 10) * math.cos(ang)
        py = cy + (r - 10) * math.sin(ang)
        c.create_line(cx, cy, px, py, fill="#DDDDDD", width=3)
        c.create_oval(cx - 5, cy - 5, cx + 5, cy + 5, fill="#DDDDDD", outline="")
        c.create_text(cx, cy + r + 10, text="推理", fill=TEXT_DIM,
                      font=("Microsoft YaHei UI", 8))


if __name__ == "__main__":
    Simulator().mainloop()
