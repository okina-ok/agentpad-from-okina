"""AgentPad 状态守护进程

监听状态文件夹 + Codex 桌面端事件日志，输出 display_state_v2.json 供模拟器渲染。

用法：
    python status_daemon.py                 # 前台循环运行
    python status_daemon.py --once          # 只扫描一次并输出（测试用）
    python status_daemon.py --no-logwatch   # 禁用日志被动检测，只用状态文件
"""

import argparse
import json
import os
import sys
import time

from log_state import MultiLogTracker

STATUS_DIR_DEFAULT = os.path.join(os.path.expanduser("~"), ".agent-status")
DISPLAY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "display_state_v2.json")
RUN_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "daemon_run.log")

ACTIVE_AFTER = 5        # 5 秒内算活跃
SUSPECT_AFTER = 15      # 5~15 秒算可疑
OFFLINE_AFTER = 60      # 超过 60 秒算离线
RUNNING_ACTIVE = 300    # 工具执行最长可视作活跃（秒）
DONE_ACTIVE = 60        # done 保持明亮的时间（秒）
DEBOUNCE_SECONDS = 0.5  # 回合内状态防抖（秒）——分类已可靠，大幅缩短切换延迟
DONE_QUIET = 1.0        # 回合结束判定：thinking 且安静这么久才算 done（秒）
DONE_QUIET_CONTENT = 5.0  # done 兜底：只有内容结束信号（无 response.completed）时的安静时长
DONE_QUIET_BG = 2.0     # 后台会话 done 兜底：无实时流，缩短安静时长提升响应

VALID_STATES = {"thinking", "running", "waiting", "done", "error", "idle"}

COLOR_BY_STATE = {
    "thinking": "#9B59FF",  # 紫
    "running": "#00E5A0",   # 青
    "waiting": "#FFB000",   # 琥珀
    "done": "#22C55E",      # 绿
    "error": "#FF3B4E",     # 红
    "idle": "#555555",      # 灰
}

EFFECT_BY_STATE = {
    "thinking": "breathe",
    "running": "solid",
    "waiting": "flash",
    "done": "solid",
    "error": "flash",
    "idle": "off",
}


def freshness(ts):
    """按心跳把时间戳归类为 active / suspect / stale / offline"""
    now = time.time()
    if ts > now + 60:          # 未来时间戳：时钟可疑，不信任
        return "suspect"
    age = now - ts
    if age < ACTIVE_AFTER:
        return "active"
    if age < SUSPECT_AFTER:
        return "suspect"
    if age < OFFLINE_AFTER:
        return "stale"
    return "offline"


def log_freshness(state, state_ts, last_event_ts):
    """日志源状态的新鲜度：running/done 按状态时间，thinking 按事件流刷新。"""
    now = time.time()
    if state_ts > now + 60 or last_event_ts > now + 60:
        return "suspect"
    if state == "running":
        age = now - state_ts
        if age < RUNNING_ACTIVE:
            return "active"
        if age < RUNNING_ACTIVE + SUSPECT_AFTER:
            return "suspect"
        return "stale"
    if state == "done":
        age = now - state_ts
        if age < DONE_ACTIVE:
            return "active"
        if age < DONE_ACTIVE * 2:
            return "suspect"
        if age < OFFLINE_AFTER * 5:
            return "stale"
        return "offline"
    # thinking：按最近一次有意义事件刷新
    age = now - last_event_ts
    if age < ACTIVE_AFTER:
        return "active"
    if age < SUSPECT_AFTER:
        return "suspect"
    if age < OFFLINE_AFTER:
        return "stale"
    return "offline"


def read_agent_file(path):
    try:
        # utf-8-sig 同时兼容带 BOM（Windows PowerShell 写的）和不带 BOM 的文件
        with open(path, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    state = data.get("state", "idle")
    if state not in VALID_STATES:
        state = "idle"
    ts = data.get("ts")
    if not isinstance(ts, (int, float)):
        ts = 0
    return {
        "state": state,
        "ts": int(ts),
        "summary": str(data.get("summary", ""))[:80],
    }


def scan_files(status_dir):
    """纯状态文件模式（--no-logwatch）。"""
    agents = []
    for slot in range(1, 7):
        path = os.path.join(status_dir, f"agent-{slot}.json")
        entry = {"slot": slot}
        info = read_agent_file(path)
        if info is None:
            entry.update({"state": "idle", "fresh": "offline",
                          "color": COLOR_BY_STATE["idle"], "effect": "off"})
        else:
            fresh = freshness(info["ts"])
            # 心跳过期时即使文件里写着 running 也不能信，按 idle 处理
            state = info["state"] if fresh in ("active", "suspect") else "idle"
            entry.update({
                "state": state,
                "fresh": fresh,
                "color": COLOR_BY_STATE[state],
                "effect": EFFECT_BY_STATE[state],
                "ts": info["ts"],
                "summary": info["summary"],
            })
        agents.append(entry)
    return {"agents": agents, "updated": int(time.time())}


def scan(status_dir, tracker=None):
    """多会话模式：槽位 = 最近活跃的会话，逐个映射状态/颜色/灯效。"""
    if tracker is None:
        return scan_files(status_dir)
    tracker.poll()
    slots = tracker.get_slots()
    agents = []
    active_slot = None
    for i in range(1, 7):
        if i <= len(slots):
            s = slots[i - 1]
            state = s["state"]
            now = int(time.time())
            quiet = now - s["last_event_ts"]
            is_active = s["thread_id"] == tracker.active_tid
            # done 兜底：thinking 且收到过完成信号 + 安静超时 -> done
            # （输出流漏判/延迟时的保险，恢复 v0.4 单线程版的双通道判定）
            done_q = (
                state == "thinking"
                and ((s.get("last_response_ts") and quiet >= DONE_QUIET)
                     or (s.get("last_content_ts") and quiet >=
                         (DONE_QUIET_CONTENT if is_active else DONE_QUIET_BG)))
            )
            # 点击/激活产生的假 user_input：只有一条 dispatch、之后 10 秒无后续活动 -> 回空闲
            click_noise = (
                state == "thinking"
                and not (s.get("last_response_ts") or s.get("last_content_ts"))
                and quiet >= 10
            )
            if done_q:
                entry = {
                    "slot": i, "state": "done", "fresh": "active",
                    "color": COLOR_BY_STATE["done"], "effect": EFFECT_BY_STATE["done"],
                    "ts": now, "summary": s["title"], "name": s["title"],
                    "thread_id": s.get("thread_id", ""), "src": "log",
                }
            elif click_noise:
                entry = {
                    "slot": i, "state": "idle", "fresh": "stale",
                    "color": COLOR_BY_STATE["idle"], "effect": EFFECT_BY_STATE["idle"],
                    "ts": s["ts"], "summary": s["title"], "name": s["title"],
                    "thread_id": s.get("thread_id", ""), "src": "log",
                }
            else:
                fresh = log_freshness(state, s["ts"], s["last_event_ts"])
                if fresh == "offline":
                    state = "idle"
                entry = {
                    "slot": i, "state": state, "fresh": fresh,
                    "color": COLOR_BY_STATE[state], "effect": EFFECT_BY_STATE[state],
                    "ts": s["ts"], "summary": s["title"], "name": s["title"],
                    "thread_id": s.get("thread_id", ""), "src": "log",
                }
            if s["thread_id"] == tracker.active_tid:
                active_slot = i
        else:
            entry = {"slot": i, "state": "idle", "fresh": "offline",
                     "color": COLOR_BY_STATE["idle"], "effect": "off", "src": "empty"}
        agents.append(entry)
    # 文件 waiting 覆盖到活跃槽位（日志检测不到审批）
    if active_slot is not None:
        f0 = read_agent_file(os.path.join(status_dir, "agent-1.json"))
        if f0 and f0["state"] == "waiting" and freshness(f0["ts"]) in ("active", "suspect"):
            idx = active_slot - 1
            agents[idx] = {
                "slot": active_slot, "state": "waiting", "fresh": "active",
                "color": COLOR_BY_STATE["waiting"], "effect": EFFECT_BY_STATE["waiting"],
                "ts": f0["ts"], "summary": "waiting (file)",
                "name": agents[idx].get("name", ""), "src": "file",
            }
    return {"agents": agents, "updated": int(time.time())}


def log(msg):
    try:
        with open(RUN_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except OSError:
        pass


class Debounce:
    """状态防抖：回合内状态变化需稳定 seconds 秒才切换显示。
    done（回合完成）与新回合开始（done -> 任何状态）立即切换，不防抖。"""

    def __init__(self, seconds):
        self.seconds = seconds
        self.shown = None
        self._candidate = None  # (key, first_seen)

    def update(self, key, now):
        key = key[0] if isinstance(key, tuple) else key  # 只看状态，不看新鲜度
        if key == "done":
            self.shown = key
            self._candidate = None
            return key
        if self.shown == "done":
            self.shown = key
            self._candidate = None
            return key
        if key == "running":
            self.shown = key
            self._candidate = None
            return key
        if key == self.shown:
            self._candidate = None
            return self.shown
        if self._candidate is None or self._candidate[0] != key:
            self._candidate = (key, now)
        elif now - self._candidate[1] >= self.seconds:
            self.shown = key
            self._candidate = None
            return key
        return self.shown


def main():
    parser = argparse.ArgumentParser(description="AgentPad 状态守护进程")
    parser.add_argument("--status-dir", default=STATUS_DIR_DEFAULT)
    parser.add_argument("--once", action="store_true", help="只扫描一次并退出")
    parser.add_argument("--interval", type=float, default=0.2, help="轮询间隔（秒）")
    parser.add_argument("--no-logwatch", action="store_true", help="禁用日志被动检测，只用状态文件")
    args = parser.parse_args()

    os.makedirs(args.status_dir, exist_ok=True)
    tracker = None if args.no_logwatch else MultiLogTracker()
    debouncers = [Debounce(DEBOUNCE_SECONDS) for _ in range(6)]

    def emit():
        payload = scan(args.status_dir, tracker)
        if tracker is not None:
            for i, a in enumerate(payload["agents"]):
                key = (a["state"], a["fresh"])
                shown = debouncers[i].update(key, time.time())
                if shown is not None and shown != key[0]:
                    a["state"] = shown
                    a["color"] = COLOR_BY_STATE[shown]
                    a["effect"] = EFFECT_BY_STATE[shown]
        with open(DISPLAY_FILE, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        return payload

    if args.once:
        print(json.dumps(emit(), ensure_ascii=False, indent=1))
        return

    print(f"[daemon] watching {args.status_dir} -> {DISPLAY_FILE}"
          + ("" if tracker else " (logwatch disabled)"))
    log("daemon started" + ("" if tracker else " (logwatch disabled)"))
    last = None
    while True:
        try:
            payload = emit()
            sig = [(a["slot"], a["state"], a["fresh"]) for a in payload["agents"]]
            if sig != last:
                last = sig
                print("[daemon]", sig)
                log(f"state {sig}")
        except KeyboardInterrupt:
            print("\n[daemon] bye")
            log("daemon stopped")
            break
        except Exception as exc:  # 守护进程不因单次错误退出
            msg = f"[daemon] error: {exc}"
            print(msg, file=sys.stderr)
            log(msg)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
