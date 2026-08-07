"""AgentPad v1 模拟器（4×4 硬件布局）

按用户拍板的 4×4 方格布局预览产品：
  第 1 排：强度旋钮 | 频道1 | 频道2 | 屏幕（只占 1 键位）
  第 2 排：频道3 | 频道4 | 频道5 | 频道6
  第 3 排：确定 | 返回 | 新建 | 分支  （可自定义预设）
  第 4 排：停止(可自定义) | 语音输入(2 键宽大键) | 通话(可自定义)

实时读取 status_daemon.py 输出的 display_state_v2.json，
把 6 个频道的会话状态渲染成灯效，验证「状态 -> 灯效」链路。

用法：
    python status_daemon.py       # 终端 1（通常已被 guard_daemon 自动拉起）
    python simulator.py           # 终端 2，弹出模拟器窗口
"""

import json
import math
import os
import subprocess
import sys
import threading
import time
import tkinter as tk

DISPLAY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "display_state_v2.json")
PTT_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ptt.py")

# ---------------- 布局常量（想微调键位尺寸/坐标，只改这里） ----------------
BG = "#111111"
KEY_BG = "#1E1E1E"
KEY_BORDER = "#3A3A3A"
TEXT_DIM = "#8A8A8A"
TEXT_BRIGHT = "#FFFFFF"
ACCENT = "#F59E0B"     # 确定 / 强调
DANGER = "#F87171"     # 停止
VOICE_BG = "#0B2E40"   # 语音大键
VOICE_FG = "#7DD3FC"
VOICE_REC_BG = "#7F1D1D"   # 录音中
VOICE_REC_FG = "#FFFFFF"
VOICE_ERR_BG = "#3F1D1D"   # PTT 异常
VOICE_ERR_FG = "#FCA5A5"
SCREEN_BG = "#0A1420"
SCREEN_FG = "#7CFC00"
SCREEN_DIM = "#58A6FF"

GRID = 4
KEY_W, KEY_H = 104, 86
GAP = 10
FPS = 20

STATE_ZH = {
    "thinking": "思考",
    "running": "运行",
    "waiting": "等待",
    "done": "完成",
    "idle": "空闲",
    "suspect": "可疑",
    "stale": "过期",
}

# 频道键：slot -> (行, 列)
CHANNEL_SLOTS = [
    (1, 0, 1), (2, 0, 2),
    (3, 1, 0), (4, 1, 1), (5, 1, 2), (6, 1, 3),
]

# 自定义预设键：(标签, 副标签, 行, 列, 前景色)
CUSTOM_KEYS = [
    ("确定", "发送", 2, 0, ACCENT),
    ("返回", "导航", 2, 1, "#9A9A9A"),
    ("新建", "新会话", 2, 2, "#9A9A9A"),
    ("分支", "派生", 2, 3, "#9A9A9A"),
    ("停止", "可自定义", 3, 0, DANGER),
    ("通话", "按住快速发", 3, 3, VOICE_FG),
]


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
        self.title("AgentPad v1 模拟器 · 4×4 布局")
        self.configure(bg=BG)
        self.attributes("-topmost", True)  # 常驻模式当桌面挂件用，置顶
        self.channel_frames = {}   # slot -> (cell, cap, name)
        self.custom_frames = []    # (cell, cap, sub)
        self.knob_canvas = None
        self.screen_line1 = None
        self.screen_line2 = None
        self.voice_cell = None
        self.voice_cap = None
        self.voice_sub = None
        self.confirm_cell = None
        self.confirm_cap = None
        self.confirm_sub = None
        self.confirm_busy = False
        self.last_payload = None
        self.t = 0.0
        # ---- PTT 子进程（复用 ptt.py --demo，与实体按键同一条链路）----
        self.ptt_proc = None
        self.ptt_ready = False
        self.ptt_state = "loading"   # loading / ready / recording / transcribing / error
        self.ptt_busy_since = 0.0    # 转写开始时间（超时兜底用）
        self.build_ui()
        width = GRID * KEY_W + (GRID - 1) * GAP + 24
        height = 4 * KEY_H + 3 * GAP + 88  # 标题 + 图例 + 留白
        self.geometry(f"{width}x{height}")
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._spawn_ptt()
        self.after(int(1000 / FPS), self.tick)

    # ---------------- UI 搭建 ----------------
    def build_ui(self):
        title = tk.Label(self, text="AgentPad v1 模拟器 · 4×4 布局", bg=BG,
                         fg="#CCCCCC", font=("Microsoft YaHei UI", 12, "bold"))
        title.pack(pady=(10, 6))

        grid = tk.Frame(self, bg=BG)
        grid.pack()

        # 第 1 排：旋钮 | 频道1 | 频道2 | 屏幕
        self.knob_canvas = tk.Canvas(grid, width=KEY_W, height=KEY_H, bg=KEY_BG,
                                     highlightbackground=KEY_BORDER,
                                     highlightthickness=1)
        self.knob_canvas.grid(row=0, column=0, padx=GAP // 2, pady=GAP // 2)

        for slot, row, col in CHANNEL_SLOTS:
            cell = tk.Frame(grid, width=KEY_W, height=KEY_H, bg=KEY_BG,
                            highlightbackground=KEY_BORDER, highlightthickness=1)
            cell.grid(row=row, column=col, padx=GAP // 2, pady=GAP // 2)
            cell.pack_propagate(False)
            cap = tk.Label(cell, text=f"频道{slot}", bg=KEY_BG, fg=TEXT_DIM,
                           font=("Microsoft YaHei UI", 10, "bold"))
            cap.pack(pady=(10, 0))
            name = tk.Label(cell, text="--", bg=KEY_BG, fg="#666666",
                            font=("Microsoft YaHei UI", 7))
            name.pack(pady=(2, 0))
            self.channel_frames[slot] = (cell, cap, name)

        screen = tk.Frame(grid, width=KEY_W, height=KEY_H, bg=SCREEN_BG,
                          highlightbackground="#1E3A5F", highlightthickness=1)
        screen.grid(row=0, column=3, padx=GAP // 2, pady=GAP // 2)
        screen.pack_propagate(False)
        self.screen_line1 = tk.Label(screen, text="就绪", bg=SCREEN_BG,
                                     fg=SCREEN_FG, font=("Consolas", 11, "bold"))
        self.screen_line1.pack(pady=(18, 0))
        self.screen_line2 = tk.Label(screen, text="电量 100% · 6 会话",
                                     bg=SCREEN_BG, fg=SCREEN_DIM,
                                     font=("Microsoft YaHei UI", 7))
        self.screen_line2.pack(pady=(4, 0))

        # 第 3、4 排：自定义预设键 + 语音大键
        for label, sub, row, col, fg in CUSTOM_KEYS:
            cell = tk.Frame(grid, width=KEY_W, height=KEY_H, bg="#222222",
                            highlightbackground=KEY_BORDER, highlightthickness=1)
            cell.grid(row=row, column=col, padx=GAP // 2, pady=GAP // 2)
            cell.pack_propagate(False)
            cap = tk.Label(cell, text=label, bg="#222222", fg=fg,
                           font=("Microsoft YaHei UI", 12, "bold"))
            cap.pack(pady=(14, 0))
            sub_l = tk.Label(cell, text=sub, bg="#222222", fg="#666666",
                             font=("Microsoft YaHei UI", 7))
            sub_l.pack(pady=(2, 0))
            self.custom_frames.append((cell, cap, sub_l))
            if label == "确定":
                self.confirm_cell, self.confirm_cap, self.confirm_sub = cell, cap, sub_l
                for w in (cell, cap, sub_l):
                    w.bind("<ButtonRelease-1>", self._confirm_press)

        voice = tk.Frame(grid, width=2 * KEY_W + GAP, height=KEY_H, bg=VOICE_BG,
                         highlightbackground="#155E75", highlightthickness=2)
        voice.grid(row=3, column=1, columnspan=2, padx=GAP // 2, pady=GAP // 2)
        voice.pack_propagate(False)
        self.voice_cell = voice
        self.voice_cap = tk.Label(voice, text="语音输入", bg=VOICE_BG, fg=VOICE_FG,
                                  font=("Microsoft YaHei UI", 15, "bold"))
        self.voice_cap.pack(pady=(12, 0))
        self.voice_sub = tk.Label(voice, text="按住说话 → 松开进对话框", bg=VOICE_BG,
                                  fg="#4B8BA8", font=("Microsoft YaHei UI", 8))
        self.voice_sub.pack(pady=(4, 0))
        # 按住 = 按下实体按键，松开 = 松开实体按键（事件与 ptt.py --demo 一致）
        for w in (voice, self.voice_cap, self.voice_sub):
            w.bind("<ButtonPress-1>", self._voice_press)
            w.bind("<ButtonRelease-1>", self._voice_release)

        legend = tk.Label(
            self,
            text="紫=思考  青=运行  琥珀=等待  绿=完成  灰=空闲   ·   第3/4排为可自定义预设",
            bg=BG, fg="#666666", font=("Microsoft YaHei UI", 8))
        legend.pack(pady=(4, 6))

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
        self._refresh_voice()
        self.after(int(1000 / FPS), self.tick)

    def render(self, payload):
        agents = payload.get("agents") or []
        by_slot = {info.get("slot"): info for info in agents if info.get("slot")}

        for slot, (cell, cap, name_l) in self.channel_frames.items():
            info = by_slot.get(slot)
            if not info or info.get("src") == "empty":
                cell.configure(bg=KEY_BG)
                cap.configure(text=f"频道{slot}", bg=KEY_BG, fg=TEXT_DIM)
                name_l.configure(text="空", bg=KEY_BG, fg="#555555")
                continue
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
            state = STATE_ZH.get(info.get("state"), info.get("state", "?"))
            cell.configure(bg=color)
            cap.configure(text=f"频道{slot} · {state}", bg=color,
                          fg=TEXT_BRIGHT if f > 0.35 else "#777777")
            nm = (info.get("name") or info.get("summary") or "").strip()
            name_l.configure(text=nm[:8] or "--", bg=color,
                             fg="#DDDDDD" if f > 0.35 else "#777777")

        # 屏幕：显示当前最活跃频道的状态 + 电量（v1 固定占位）
        active = self._pick_active(agents)
        if active:
            st = STATE_ZH.get(active.get("state"), active.get("state", "?"))
            slot = active.get("slot", "?")
            self.screen_line1.configure(text=f"A{slot} · {st}")
        else:
            self.screen_line1.configure(text="就绪")
        total = sum(1 for a in agents if a.get("src") != "empty")
        self.screen_line2.configure(text=f"电量 100% · {total} 会话")

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

    # ---------------- 旋钮 ----------------
    def _draw_knob(self, active):
        c = self.knob_canvas
        c.delete("all")
        cx, cy = KEY_W // 2, 40
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
        c.create_text(cx, cy + r + 12, text="强度", fill=TEXT_DIM,
                      font=("Microsoft YaHei UI", 9))

    # ---------------- PTT 语音（复用 ptt.py --demo，与实体按键同一条链路） ----------------
    def _spawn_ptt(self):
        """启动 ptt.py --demo：从 stdin 读按键事件，与 ESP32 串口模式共用处理函数。"""
        try:
            self.ptt_proc = subprocess.Popen(
                [sys.executable, PTT_SCRIPT, "--demo"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as exc:
            self.ptt_state = "error"
            print("PTT spawn failed:", exc)
            return
        threading.Thread(target=self._ptt_reader, daemon=True).start()

    def _ptt_reader(self):
        """读 ptt.py 输出，跟踪就绪/转写/错误状态。"""
        try:
            for line in self.ptt_proc.stdout:
                line = line.strip()
                if "PTT 就绪" in line or "model ready" in line:
                    self.ptt_ready = True
                    self.ptt_state = "ready"
                elif "转写中" in line:
                    self.ptt_state = "transcribing"
                    self.ptt_busy_since = time.time()
                elif ("转写结果" in line or "已填入" in line or "注入失败" in line
                      or "太短" in line or "没听清" in line):
                    self.ptt_state = "ready"
                elif "ERROR" in line or "Traceback" in line:
                    self.ptt_state = "error"
        except Exception:
            self.ptt_state = "error"

    def _write_ptt(self, line):
        try:
            self.ptt_proc.stdin.write(line + "\n")
            self.ptt_proc.stdin.flush()
        except Exception:
            self.ptt_state = "error"

    def _voice_press(self, _ev=None):
        if not self.ptt_ready:
            return  # 模型未就绪，忽略（界面会显示"模型加载中"）
        if self.ptt_state == "transcribing":
            return  # 上一段还在转写，避免同时录两段
        self._write_ptt('{"ev":"key","i":1,"s":1}')
        self.ptt_state = "recording"

    def _voice_release(self, _ev=None):
        if self.ptt_state == "recording":
            self._write_ptt('{"ev":"key","i":1,"s":0}')
            self.ptt_state = "transcribing"
            self.ptt_busy_since = time.time()

    def _refresh_voice(self):
        """按 PTT 状态刷新语音大键外观（20fps 由 tick 调用）。"""
        cell, cap, sub = self.voice_cell, self.voice_cap, self.voice_sub
        if cell is None:
            return
        # 兜底：转写超过 60 秒还没回音（丢行/崩溃），强制回就绪，避免 UI 卡死
        if (self.ptt_state == "transcribing" and self.ptt_busy_since
                and time.time() - self.ptt_busy_since > 60):
            self.ptt_state = "ready"
        if self.ptt_state == "recording":
            cap.configure(text="录音中…", bg=VOICE_REC_BG, fg=VOICE_REC_FG)
            sub.configure(text="松开结束并转写", bg=VOICE_REC_BG, fg="#FECACA")
            cell.configure(bg=VOICE_REC_BG, highlightbackground="#B91C1C")
        elif self.ptt_state == "transcribing":
            cap.configure(text="转写中…", bg=VOICE_BG, fg=VOICE_FG)
            sub.configure(text="正在转成文字，请稍候", bg=VOICE_BG, fg="#4B8BA8")
            cell.configure(bg=VOICE_BG, highlightbackground="#155E75")
        elif self.ptt_state == "error":
            cap.configure(text="PTT 异常", bg=VOICE_ERR_BG, fg=VOICE_ERR_FG)
            sub.configure(text="详情见 ptt_result.txt", bg=VOICE_ERR_BG, fg="#FDA4AF")
            cell.configure(bg=VOICE_ERR_BG, highlightbackground="#7F1D1D")
        elif not self.ptt_ready:
            cap.configure(text="语音输入", bg=VOICE_BG, fg="#5B8EA8")
            sub.configure(text="模型加载中…", bg=VOICE_BG, fg="#4B8BA8")
            cell.configure(bg=VOICE_BG, highlightbackground="#155E75")
        else:
            cap.configure(text="语音输入", bg=VOICE_BG, fg=VOICE_FG)
            sub.configure(text="按住说话 → 松开进对话框", bg=VOICE_BG, fg="#4B8BA8")
            cell.configure(bg=VOICE_BG, highlightbackground="#155E75")

    def _on_close(self):
        if self.ptt_proc is not None:
            try:
                self.ptt_proc.terminate()
            except Exception:
                pass
        self.destroy()

    # ---------------- 确定键：发送（复用 inject_text.send_enter） ----------------
    def _confirm_press(self, _ev=None):
        if self.confirm_busy:
            return
        self.confirm_busy = True
        self._set_confirm_text("发送中…", "正在发送", "#FCD34D")
        threading.Thread(target=self._do_send, daemon=True).start()

    def _do_send(self):
        try:
            import inject_text
            ok = inject_text.send_enter()
        except Exception as exc:
            print("send error:", exc)
            ok = False
        status = "已发送" if ok else "发送失败"
        color = "#4ADE80" if ok else "#F87171"
        sub = "发送成功" if ok else "点击重试"
        self.after(0, lambda: self._set_confirm_text(status, sub, color))
        self.after(2000, self._reset_confirm_text)

    def _set_confirm_text(self, main, sub, color="#F59E0B"):
        if self.confirm_cap is not None:
            self.confirm_cap.configure(text=main, fg=color)
            self.confirm_sub.configure(text=sub)

    def _reset_confirm_text(self):
        self._set_confirm_text("确定", "发送")
        self.confirm_busy = False


if __name__ == "__main__":
    Simulator().mainloop()
