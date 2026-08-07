"""AgentPad 频道绑定面板

把 Codex 的对话手动绑定到 1-6 频道。纯本地操作，不需要跟 AI 交流；
守护进程读取 channel_map.json，约 1 秒内生效。

用法：
    python bind_panel.py

绑定文件 channel_map.json 格式：
    {"version": 1, "manual": {"1": "线程id", ...}, "auto": {"2": "线程id", ...}}
manual = 你手工绑定的；auto = 守护进程自动分配的（仅作记录）。
"""

import glob
import json
import os
import sqlite3
import time
import tkinter as tk
from tkinter import ttk

CHANNEL_MAP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "channel_map.json")
DISPLAY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "display_state_v2.json")
N_CHANNELS = 6
GRID_POS = {
    1: "上排·左", 2: "上排·中", 3: "上排·右",
    4: "下排·左", 5: "下排·中", 6: "下排·右",
}


def find_state_db():
    cands = sorted(
        glob.glob(os.path.join(os.path.expanduser("~"), ".codex", "state_*.sqlite")),
        key=os.path.getmtime, reverse=True,
    )
    for c in cands:
        try:
            con = sqlite3.connect(f"file:{c}?mode=ro", uri=True, timeout=1)
            has = con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='threads'"
            ).fetchone()
            con.close()
            if has:
                return c
        except sqlite3.Error:
            pass
    return ""


def list_threads(db):
    out = []
    if not db:
        return out
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1)
        cur = con.cursor()
        cur.execute(
            "SELECT id, title, COALESCE(name,''), recency_at, updated_at "
            "FROM threads WHERE archived=0 ORDER BY recency_at DESC"
        )
        for tid, title, name, ra, ua in cur.fetchall():
            out.append({
                "id": tid,
                "title": (name or title or tid[:8])[:24],
                "recency": max(ra or 0, ua or 0),
            })
        con.close()
    except sqlite3.Error:
        pass
    return out


def load_manual():
    try:
        with open(CHANNEL_MAP, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return {int(k): v for k, v in (d.get("manual") or {}).items()}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def save_manual(manual):
    try:
        with open(CHANNEL_MAP, "r", encoding="utf-8") as fh:
            old = json.load(fh)
        auto = old.get("auto") or {}
        anchor = bool(old.get("anchor_channel_1", False))
    except (OSError, ValueError, json.JSONDecodeError):
        auto, anchor = {}, False
    with open(CHANNEL_MAP, "w", encoding="utf-8") as fh:
        json.dump({
            "version": 1,
            "anchor_channel_1": anchor,
            "manual": {str(k): v for k, v in sorted(manual.items())},
            "auto": auto,
        }, fh, ensure_ascii=False, indent=1)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AgentPad 频道绑定")
        self.geometry("680x460")
        self.threads = list_threads(find_state_db())
        self.id_by_label = {f"{t['title']} ({t['id'][:8]})": t['id'] for t in self.threads}
        self.label_by_id = {v: k for k, v in self.id_by_label.items()}
        self.manual = load_manual()
        self.vars = {}
        self.cbs = {}
        self.cur_labels = {}
        self.autosave_after = None

        tk.Label(self, text="为每个频道选择要绑定的对话；选「自动」= 让守护进程自己分配；选择即自动保存",
                 font=("Microsoft YaHei UI", 10)).pack(pady=(10, 4))

        frame = ttk.Frame(self)
        frame.pack(fill=tk.X, padx=12)
        for slot in range(1, N_CHANNELS + 1):
            row = ttk.Frame(frame)
            row.pack(fill=tk.X, pady=2)
            ttk.Label(row, text=f"频道 {slot}（{GRID_POS[slot]}）", width=16).pack(side=tk.LEFT)
            var = tk.StringVar()
            tid = self.manual.get(slot)
            var.set(self.label_by_id.get(tid, "自动"))
            cb = ttk.Combobox(
                row, textvariable=var,
                values=["自动"] + list(self.id_by_label.keys()),
                width=52, state="readonly",
            )
            cb.pack(side=tk.LEFT, padx=6)
            cb.bind("<<ComboboxSelected>>", lambda _e, s=slot: self.schedule_save(s))
            self.vars[slot] = var
            self.cbs[slot] = cb
            cur = ttk.Label(row, text="当前: -", width=34, foreground="#555555")
            cur.pack(side=tk.LEFT, padx=4)
            self.cur_labels[slot] = cur

        tk.Label(self, text="当前 Codex 对话列表（最近活跃在上）",
                 font=("Microsoft YaHei UI", 9)).pack(anchor=tk.W, padx=12, pady=(8, 2))
        tree = ttk.Treeview(self, columns=("name", "rec"), show="headings", height=8)
        tree.heading("name", text="对话")
        tree.heading("rec", text="最近活跃")
        tree.column("name", width=420)
        for t in self.threads:
            tree.insert("", tk.END, values=(
                t["title"],
                time.strftime("%m-%d %H:%M", time.localtime(t["recency"])),
            ))
        tree.pack(fill=tk.BOTH, expand=True, padx=12, pady=4)
        self.tree = tree
        ttk.Button(self, text="刷新会话列表", command=self.reload_threads).pack(pady=2)

        self.status = tk.Label(self, text="", fg="#0A7D32")
        self.status.pack()
        ttk.Button(self, text="立即保存（已自动保存，可不用点）", command=self.save).pack(pady=(2, 10))
        self.after(2000, self.refresh_status)

    def schedule_save(self, _slot=None):
        """选中即自动保存（防抖 800ms，连续改动只保存最后一次）。"""
        if self.autosave_after is not None:
            self.after_cancel(self.autosave_after)
        self.autosave_after = self.after(800, self.save)

    def refresh_status(self):
        """每 2 秒从守护进程的显示文件读当前映射，标在频道行上。"""
        by_slot = {}
        try:
            with open(DISPLAY_FILE, "r", encoding="utf-8") as fh:
                d = json.load(fh)
            for a in d.get("agents", []):
                by_slot[a["slot"]] = (
                    a.get("name") or a.get("summary") or "",
                    a.get("state", ""),
                )
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        for slot, lab in self.cur_labels.items():
            name, state = by_slot.get(slot, ("", ""))
            lab.config(text=f"当前: {name or '空闲'} [{state}]")
        self.after(2000, self.refresh_status)

    def reload_threads(self):
        """刷新会话列表（新开的对话会出现在这里）。"""
        self.threads = list_threads(find_state_db())
        self.id_by_label = {f"{t['title']} ({t['id'][:8]})": t['id'] for t in self.threads}
        self.label_by_id = {v: k for k, v in self.id_by_label.items()}
        values = ["自动"] + list(self.id_by_label.keys())
        for cb in self.cbs.values():
            cb["values"] = values
        for item in self.tree.get_children():
            self.tree.delete(item)
        for t in self.threads:
            self.tree.insert("", tk.END, values=(
                t["title"],
                time.strftime("%m-%d %H:%M", time.localtime(t["recency"])),
            ))

    def save(self):
        self.autosave_after = None
        manual = {}
        for slot, var in self.vars.items():
            sel = var.get()
            if sel != "自动":
                tid = self.id_by_label.get(sel)
                if tid:
                    manual[slot] = tid
        save_manual(manual)
        self.status.config(text="已保存并生效 " + time.strftime("%H:%M:%S"))
        self.refresh_status()


if __name__ == "__main__":
    App().mainloop()
