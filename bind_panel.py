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
N_CHANNELS = 6


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
    except (OSError, ValueError, json.JSONDecodeError):
        auto = {}
    with open(CHANNEL_MAP, "w", encoding="utf-8") as fh:
        json.dump({
            "version": 1,
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

        tk.Label(self, text="为每个频道选择要绑定的对话；选「自动」= 让守护进程自己分配",
                 font=("Microsoft YaHei UI", 10)).pack(pady=(10, 4))

        frame = ttk.Frame(self)
        frame.pack(fill=tk.X, padx=12)
        for slot in range(1, N_CHANNELS + 1):
            row = ttk.Frame(frame)
            row.pack(fill=tk.X, pady=2)
            ttk.Label(row, text=f"频道 {slot}", width=8).pack(side=tk.LEFT)
            var = tk.StringVar()
            tid = self.manual.get(slot)
            var.set(self.label_by_id.get(tid, "自动"))
            cb = ttk.Combobox(
                row, textvariable=var,
                values=["自动"] + list(self.id_by_label.keys()),
                width=52, state="readonly",
            )
            cb.pack(side=tk.LEFT, padx=6)
            self.vars[slot] = var

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

        self.status = tk.Label(self, text="", fg="#0A7D32")
        self.status.pack()
        ttk.Button(self, text="保存绑定", command=self.save).pack(pady=(2, 10))

    def save(self):
        manual = {}
        for slot, var in self.vars.items():
            sel = var.get()
            if sel != "自动":
                tid = self.id_by_label.get(sel)
                if tid:
                    manual[slot] = tid
        save_manual(manual)
        self.status.config(text="已保存 → channel_map.json（守护进程 1 秒内生效）")


if __name__ == "__main__":
    App().mainloop()
