"""AgentPad 实时状态检测（M3）：被动读取 Codex 桌面端事件日志

从 ~/.codex/logs_2.sqlite 的 SSE / app-server 事件推导 agent 阶段。

关键认知（M2 -> M3 的修复）：
- op.dispatch.user_input 不仅在用户发消息时出现，工具调用 dispatch 时也会写；
  因此必须先判断"是否工具行"，再判断"是否真实用户消息"。
- 工具行（ToolCall / tool call completed / function_call 相关 SSE 与 trace）
  是实时写的（工具 dispatch 时落盘），可作为 running 的实时信号。
- 带工具上下文的 response.completed 属于工具周期，不能作为回合结束证据；
  只有不带工具标记的 response.completed 才记录 last_response_ts，
  由守护进程按"安静 DONE_QUIET 秒"判 done。

说明：
- waiting 目前无法从日志被动检测（桌面端日志没有暴露审批事件），
  仍由 agent 自觉写状态文件（AGENTS.md 约定）+ 心跳判活兜底。
- 日志库名可能随版本变化（logs_2.sqlite -> logs_3.sqlite 等），届时改 DB_PATH_DEFAULT。
"""

import json
import os
import re
import sqlite3
import time

DB_PATH_DEFAULT = os.path.join(os.path.expanduser("~"), ".codex", "logs_2.sqlite")
CACHE_PATH_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "log_state_cache.json")
DEBUG_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "log_state_debug.log")
CHANNEL_MAP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "channel_map.json")

TOOL_ITEM_RE = r'"item":\{.*?"type":"(function_call|custom_tool_call|web_search_call)"'
TID_FULL_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

# 工具行标记：出现任意一个即视为"工具正在执行"（running）。
# 注意不要用裸词 function_call，避免 reasoning 文本误伤。
TOOL_MARKERS = (
    "ToolCall:",
    "tool call completed",
    '"type":"function_call"',        # SSE output_item.added/done
    'item_type="function_call"',     # app-server Output item 行
    'otel.name="function_call"',     # app-server trace 行
    "custom_tool_call",              # custom tool 变体
    "web_search_call",               # web 搜索变体
    "function_call_arguments.delta",  # SSE 实时流式参数（工具即将/正在执行）
    "tool_name=",                    # app-server dispatch 行
)


def set_state(st, state, ts, summary=""):
    st.state = state
    st.state_ts = ts
    st.summary = summary


def extract_tid(body):
    """从日志行提取完整会话 ID；截断/伪造的短 ID 一律忽略。"""
    m = re.search(r"thread_id=(" + TID_FULL_RE.pattern + r")", body or "")
    return m.group(1) if m else None


def load_channel_map(path=None):
    """读频道绑定文件。manual = 用户手工绑定（slot -> thread_id），auto = 守护进程自动分配。"""
    path = path or CHANNEL_MAP_FILE
    try:
        with open(path, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        manual = {}
        for k, v in (d.get("manual") or {}).items():
            try:
                manual[int(k)] = str(v)
            except (ValueError, TypeError):
                pass
        return {"manual": manual, "auto": d.get("auto") or {}}
    except (OSError, ValueError, json.JSONDecodeError):
        return {"manual": {}, "auto": {}}


def save_channel_map(manual, auto, path=None):
    """写频道绑定文件。manual 来自用户/面板，auto 是守护进程当前的自动分配快照。"""
    path = path or CHANNEL_MAP_FILE
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({
                "version": 1,
                "manual": {str(k): v for k, v in sorted(manual.items())},
                "auto": {str(k): v for k, v in sorted(auto.items())},
            }, fh, ensure_ascii=False, indent=1)
    except OSError:
        pass


def apply_row(body, st, ts):
    """把一行日志套到某个会话的状态机上（单线程 / 多线程共用）。

    返回该行是否产生了有意义的状态变化。st 需具备：
    state / state_ts / last_response_ts / last_content_ts / last_submission_id / summary。
    """
    meaningful = False

    is_tool = any(m in body for m in TOOL_MARKERS)
    is_cycle_over = "tool call completed" in body
    is_completed = '"type":"response.completed"' in body
    is_message_item = 'item_type="message"' in body and not is_tool
    m_nf = re.search(r"(?:model_)?needs_follow_up=(\w+)", body)
    is_turn_end_hint = m_nf is not None and m_nf.group(1) == "false"
    is_midturn_hint = m_nf is not None and m_nf.group(1) == "true"
    m_sub = re.search(r'submission\.id="(' + TID_FULL_RE.pattern + r')"', body)
    sid = m_sub.group(1) if m_sub is not None else None
    is_new_submission = sid is not None and sid != st.last_submission_id
    is_real_user = (
        "op.dispatch.user_input" in body
        and not is_tool
        and not is_completed
        and is_new_submission
    )
    if sid is not None:
        st.last_submission_id = sid

    # 1) 工具完成 -> thinking（工具已结束，模型在处理结果；不启用 done）
    if is_cycle_over:
        st.last_response_ts = 0
        st.last_content_ts = 0
        set_state(st, "thinking", ts, "tool completed")
        meaningful = True
    # 2) 工具行 -> running
    elif is_tool:
        st.last_response_ts = 0
        st.last_content_ts = 0
        set_state(st, "running", ts, "tool executing")
        meaningful = True
    # 3) 响应完成（纯文本回合结束证据）-> 记录完成点；已在 done 则保持绿
    elif is_completed:
        st.last_response_ts = ts
        if st.state != "done":
            set_state(st, "thinking", ts, "response completed")
        meaningful = True
    # 4) needs_follow_up=false -> 兜底完成点
    elif is_turn_end_hint:
        st.last_content_ts = ts
        if st.state != "done":
            set_state(st, "thinking", ts, "turn end hint")
        meaningful = True
    # 5) needs_follow_up=true -> 撤销兜底完成点
    elif is_midturn_hint:
        st.last_content_ts = 0
        if st.state != "done":
            set_state(st, "thinking", ts, "turn continue")
        meaningful = True
    # 6) 助手文本消息完成 -> 兜底完成点
    elif is_message_item:
        st.last_content_ts = ts
        if st.state != "done":
            set_state(st, "thinking", ts, "message done")
        meaningful = True
    # 7) 输出阶段（回答文字开始流式出现）-> 直接 done
    elif '"type":"response.output_text.delta"' in body:
        st.last_response_ts = ts
        set_state(st, "done", ts, "outputting answer")
        meaningful = True
    # 8) 真实用户消息 -> 新回合 thinking
    elif is_real_user:
        st.last_response_ts = 0
        st.last_content_ts = 0
        set_state(st, "thinking", ts, "user input received")
        meaningful = True
    # 9) thinking：模型生成中
    elif ('"type":"response.reasoning_text.delta"' in body
          or '"type":"response.created"' in body
          or '"type":"response.in_progress"' in body):
        if st.state != "done":
            set_state(st, "thinking", ts, "thinking")
        meaningful = True
    # 10) 兜底：output_item.added(function_call)
    elif re.search(r'"type":"response\.output_item\.added"', body):
        mm = re.search(TOOL_ITEM_RE, body)
        if mm:
            st.last_response_ts = 0
            set_state(st, "running", ts, f"tool: {mm.group(1)}")
            meaningful = True
    return meaningful


class ThreadState:
    """单个会话的状态机状态（多线程跟踪用）。"""

    def __init__(self, thread_id):
        self.thread_id = thread_id
        self.state = "idle"
        self.state_ts = 0
        self.last_event_ts = 0
        self.last_response_ts = 0
        self.last_content_ts = 0
        self.last_submission_id = ""
        self.summary = ""
        self.title = ""
        self.recency = 0      # 最近活跃时间（来自 app 线程表或日志）
        self.seq = 0          # 创建顺序，用于同活跃度时稳定排序


class LogStateTracker:
    """增量读取日志库，维护最近一次推导出的 agent 阶段。"""

    def __init__(self, db_path=None, cache_path=None):
        self.db_path = db_path or DB_PATH_DEFAULT
        self.cache_path = cache_path or CACHE_PATH_DEFAULT
        self.state = "idle"
        self.state_ts = 0
        self.last_event_ts = 0
        self.last_id = 0
        self.summary = ""
        self.last_response_ts = 0   # 最近一次 response.completed（回合结束判定的参考点）
        self.last_content_ts = 0    # 最近一次"助手文本内容完成"（done 兜底判定参考点）
        self.watched_thread = ""    # 当前关注的 thread_id（真实用户消息决定）
        self.last_submission_id = ""  # 最近一次看到的 submission.id（去重"假用户消息"）
        self._load()
        self._seed_watched_thread()

    def _seed_watched_thread(self):
        """启动时若未绑定线程/去重 id，自动绑定最近的会话与 submission。"""
        try:
            con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=1.0)
            cur = con.cursor()
            if not self.watched_thread:
                cur.execute(
                    "SELECT feedback_log_body FROM logs WHERE feedback_log_body LIKE "
                    "'%op.dispatch.user_input%' ORDER BY id DESC LIMIT 300"
                )
                for (body,) in cur.fetchall():
                    body = body or ""
                    if any(m in body for m in TOOL_MARKERS):
                        continue
                    if '"type":"response.completed"' in body:
                        continue
                    tid = extract_tid(body)
                    if tid:
                        self.watched_thread = tid
                        break
            if not self.last_submission_id:
                cur.execute(
                    "SELECT feedback_log_body FROM logs WHERE feedback_log_body LIKE "
                    "'%submission.id=%' ORDER BY id DESC LIMIT 100"
                )
                for (body,) in cur.fetchall():
                    m = re.search(r'submission\.id="(' + TID_FULL_RE.pattern + r')"', body or "")
                    if m:
                        self.last_submission_id = m.group(1)
                        break
            con.close()
        except sqlite3.Error:
            pass

    def _load(self):
        try:
            with open(self.cache_path, "r", encoding="utf-8") as fh:
                c = json.load(fh)
            self.state = c.get("state", "idle")
            self.state_ts = int(c.get("state_ts", 0))
            self.last_event_ts = int(c.get("last_event_ts", 0))
            self.last_id = int(c.get("last_id", 0))
            self.summary = c.get("summary", "")
            self.last_response_ts = int(c.get("last_response_ts", 0))
            self.last_content_ts = int(c.get("last_content_ts", 0))
            self.watched_thread = c.get("watched_thread", "")
            self.last_submission_id = c.get("last_submission_id", "")
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    def _save(self):
        try:
            with open(self.cache_path, "w", encoding="utf-8") as fh:
                json.dump({
                    "state": self.state,
                    "state_ts": self.state_ts,
                    "last_event_ts": self.last_event_ts,
                    "last_id": self.last_id,
                    "summary": self.summary,
                    "last_response_ts": self.last_response_ts,
                    "last_content_ts": self.last_content_ts,
                    "watched_thread": self.watched_thread,
                    "last_submission_id": self.last_submission_id,
                }, fh)
        except OSError:
            pass

    def _set(self, state, ts, summary=""):
        self.state = state
        self.state_ts = ts
        self.summary = summary

    def poll(self):
        """读取 last_id 之后的新日志行并更新状态。"""
        try:
            con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=1.0)
            cur = con.cursor()
            cur.execute("SELECT id, ts, feedback_log_body FROM logs WHERE id > ? ORDER BY id", (self.last_id,))
            for rid, ts, body in cur:
                self.last_id = rid
                self._handle(rid, int(ts), body or "")
            con.close()
        except sqlite3.Error:
            pass  # 日志库暂时不可读（如正在写入），下轮再试
        self._save()

    def _handle(self, rid, ts, body):
        m_sub = re.search(r'submission\.id="(' + TID_FULL_RE.pattern + r')"', body)
        sid = m_sub.group(1) if m_sub is not None else None
        is_real_user = (
            "op.dispatch.user_input" in body
            and not any(mm in body for mm in TOOL_MARKERS)
            and '"type":"response.completed"' not in body
            and sid is not None and sid != self.last_submission_id
        )

        # 线程过滤：只处理当前关注线程的 trace 行；SSE 行无法归属线程，放行。
        # 例外：其他线程的真实用户消息（user_input 且无工具标记）允许通过，
        # 以便 AgentPad 自动跟随最近使用的会话。
        tid = extract_tid(body)
        if tid is not None and self.watched_thread and tid != self.watched_thread:
            if not is_real_user:
                return

        meaningful = apply_row(body, self, ts)
        if meaningful:
            if is_real_user and tid is not None:
                self.watched_thread = tid
            self.last_event_ts = max(self.last_event_ts, ts)
            self._debug(rid, ts, self.state, self.summary)

    def _debug(self, rid, ts, state, summary):
        """记录每次有意义分类，便于观察完整生命周期（文件有界，超 200KB 截断）。"""
        try:
            with open(DEBUG_LOG, "a", encoding="utf-8") as fh:
                fh.write("%s id=%s %s %s\n" % (time.strftime("%H:%M:%S", time.localtime(ts)), rid, state, summary))
            if os.path.getsize(DEBUG_LOG) > 200 * 1024:
                with open(DEBUG_LOG, "r", encoding="utf-8") as fh:
                    lines = fh.readlines()
                with open(DEBUG_LOG, "w", encoding="utf-8") as fh:
                    fh.writelines(lines[-3000:])
        except OSError:
            pass


class MultiLogTracker:
    """多会话状态跟踪：每个线程一个状态机，槽位 = 最近活跃的 N 个会话。

    行路由：
    - 带 thread_id 的 trace 行 -> 喂给对应线程的状态机；
    - 不带 thread_id 的 SSE 流 -> 喂给当前活跃线程（最近有 trace 活动的线程）。

    会话标题 / 活跃度来自 app 的线程表（~/.codex/state_*.sqlite 的 threads 表），
    这是"哪个会话叫什么名字、最近动过没有"的权威来源。
    """

    CACHE_PATH_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "multi_state_cache.json")
    STATE_DB_GLOB = os.path.join(os.path.expanduser("~"), ".codex", "state_*.sqlite")

    def __init__(self, db_path=None, state_db_path=None, cache_path=None, max_threads=6):
        self.db_path = db_path or DB_PATH_DEFAULT
        self.cache_path = cache_path or self.CACHE_PATH_DEFAULT
        self.max_threads = max_threads
        self.last_id = 0
        self.threads = {}        # thread_id -> ThreadState
        self.active_tid = ""
        self.pool = {}           # thread_id -> {title, recency, pinned}
        self.slot_map = {}       # thread_id -> 槽位号（一旦分配就固定，LRU 只逐出不挪位）
        self._cm_mtime = -1.0
        self._cm = {"manual": {}, "auto": {}}
        self._last_auto = None
        self._seq = 0
        self.state_db_path = ""
        self._find_state_db(state_db_path)
        self._read_pool()
        self._load_cache()
        self._seed_active()

    def _find_state_db(self, explicit=None):
        if explicit and os.path.exists(explicit):
            self.state_db_path = explicit
            return
        import glob
        cands = sorted(glob.glob(self.STATE_DB_GLOB), key=os.path.getmtime, reverse=True)
        for c in cands:
            try:
                con = sqlite3.connect(f"file:{c}?mode=ro", uri=True, timeout=1)
                has = con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='threads'"
                ).fetchone()
                con.close()
                if has:
                    self.state_db_path = c
                    return
            except sqlite3.Error:
                pass
        self.state_db_path = explicit or ""

    def _read_pool(self):
        """读 app 线程表：标题 + 最近活跃时间 + 钉选。"""
        if not self.state_db_path:
            return
        try:
            con = sqlite3.connect(f"file:{self.state_db_path}?mode=ro", uri=True, timeout=1)
            cur = con.cursor()
            cur.execute(
                "SELECT id, title, COALESCE(name,''), recency_at, updated_at, is_pinned "
                "FROM threads WHERE archived = 0"
            )
            for tid, title, name, recency_at, updated_at, pinned in cur.fetchall():
                self.pool[tid] = {
                    "title": (name or title or tid[:8])[:12],
                    "recency": max(recency_at or 0, updated_at or 0),
                    "pinned": bool(pinned),
                }
            con.close()
        except sqlite3.Error:
            pass

    def _seed_active(self):
        if not self.active_tid and self.pool:
            self.active_tid = max(self.pool, key=lambda t: self.pool[t]["recency"])

    def _read_channel_map(self):
        """读取频道绑定（本机文件 mtime 不可靠，直接每次读；文件很小）。"""
        return load_channel_map()

    def _load_cache(self):
        try:
            with open(self.cache_path, "r", encoding="utf-8") as fh:
                c = json.load(fh)
            self.last_id = int(c.get("last_id", 0))
            self.active_tid = c.get("active_tid", "")
            self.slot_map = {str(k): int(v) for k, v in (c.get("slot_map") or {}).items()}
            for tid, d in (c.get("threads") or {}).items():
                if not TID_FULL_RE.fullmatch(tid):
                    continue  # 清掉截断 ID 的幽灵线程
                st = ThreadState(tid)
                st.state = d.get("state", "idle")
                st.state_ts = int(d.get("state_ts", 0))
                st.last_event_ts = int(d.get("last_event_ts", 0))
                st.last_response_ts = int(d.get("last_response_ts", 0))
                st.last_content_ts = int(d.get("last_content_ts", 0))
                st.last_submission_id = d.get("last_submission_id", "")
                st.title = d.get("title", "")
                st.recency = int(d.get("recency", 0))
                st.seq = int(d.get("seq", 0))
                self._seq = max(self._seq, st.seq)
                self.threads[tid] = st
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    def _save_cache(self):
        try:
            with open(self.cache_path, "w", encoding="utf-8") as fh:
                json.dump({
                    "last_id": self.last_id,
                    "active_tid": self.active_tid,
                    "slot_map": self.slot_map,
                    "threads": {
                        tid: {
                            "state": st.state,
                            "state_ts": st.state_ts,
                            "last_event_ts": st.last_event_ts,
                            "last_response_ts": st.last_response_ts,
                            "last_content_ts": st.last_content_ts,
                            "last_submission_id": st.last_submission_id,
                            "title": st.title,
                            "recency": st.recency,
                            "seq": st.seq,
                        }
                        for tid, st in self.threads.items()
                    },
                }, fh, ensure_ascii=False)
        except OSError:
            pass

    def poll(self):
        """增量读取日志库，路由到各线程状态机。"""
        try:
            con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=1.0)
            cur = con.cursor()
            cur.execute("SELECT id, ts, feedback_log_body FROM logs WHERE id > ? ORDER BY id", (self.last_id,))
            for rid, ts, body in cur:
                self.last_id = rid
                self._route(rid, int(ts), body or "")
            con.close()
        except sqlite3.Error:
            pass  # 日志库暂时不可读（如正在写入），下轮再试
        self._save_cache()

    def _route(self, rid, ts, body):
        tid = extract_tid(body)
        has_tid = tid is not None
        if not has_tid:
            tid = self.active_tid
        if not tid:
            return
        st = self.threads.get(tid)
        if st is None:
            st = ThreadState(tid)
            self._seq += 1
            st.seq = self._seq
            if tid in self.pool:
                st.title = self.pool[tid]["title"]
                st.recency = self.pool[tid]["recency"]
            self.threads[tid] = st
        # SSE 流（无线程归属）只喂给"最近在动"的活跃会话，避免旧噪音灌错对象
        if not has_tid and st.last_event_ts and ts - st.last_event_ts > 15:
            return
        m_sub = re.search(r'submission\.id="(' + TID_FULL_RE.pattern + r')"', body)
        sid = m_sub.group(1) if m_sub is not None else None
        is_real_user = (
            has_tid
            and "op.dispatch.user_input" in body
            and not any(x in body for x in TOOL_MARKERS)
            and '"type":"response.completed"' not in body
            and sid is not None and sid != st.last_submission_id
        )
        meaningful = apply_row(body, st, ts)
        if meaningful:
            st.last_event_ts = max(st.last_event_ts, ts)
            # 只有真实用户消息才切换活跃会话（后台工具行不抢焦点）
            if is_real_user:
                self.active_tid = tid
        if has_tid and ts > st.recency:
            st.recency = ts

    def _ensure_thread(self, tid):
        """取线程状态对象，不存在则按会话池信息创建（idle 起步）。"""
        st = self.threads.get(tid)
        if st is None:
            st = ThreadState(tid)
            self._seq += 1
            st.seq = self._seq
            if tid in self.pool:
                st.title = self.pool[tid]["title"]
                st.recency = self.pool[tid]["recency"]
            self.threads[tid] = st
        return st

    def get_slots(self):
        """槽位分配：手工绑定优先且固定；自动分配在首次出现时定槽、之后不挪位，
        超员时才逐出最不活跃的自动会话（手工绑定永不逐出）。"""
        now = time.time()
        cm = self._read_channel_map()
        manual = cm["manual"]

        slots = {}          # slot -> ThreadState
        placed = set()      # 已占位的 thread_id
        # 1) 手工绑定：永远优先、永不逐出
        for slot, tid in sorted(manual.items()):
            if slot < 1 or slot > self.max_threads:
                continue
            st = self._ensure_thread(tid)
            slots[slot] = st
            placed.add(tid)
            self.slot_map[tid] = slot

        # 2) 自动候选：app 会话池，或日志里 6 小时内活跃的会话；手工绑定的不再参与
        candidates = [
            st for st in self.threads.values()
            if TID_FULL_RE.fullmatch(st.thread_id)
            and st.thread_id not in manual.values()
            and (st.thread_id in self.pool or st.recency > now - 6 * 3600)
        ]
        candidates.sort(key=lambda st: (-st.recency, st.seq))

        # 3) 已自动分配过的会话，原槽位若空闲则保留（不挪位）
        for st in candidates:
            if st.thread_id in placed:
                continue
            s = self.slot_map.get(st.thread_id)
            if s is not None and 1 <= s <= self.max_threads and s not in slots:
                slots[s] = st
                placed.add(st.thread_id)

        # 4) 新会话填空闲槽；满了则只有比"当前最不活跃的自动会话"更新才逐出（频道1除外）
        for st in candidates:
            if st.thread_id in placed:
                continue
            free = [s for s in range(1, self.max_threads + 1) if s not in slots]
            if free:
                slot = free[0]
            else:
                auto_slots = {
                    s: st.thread_id for s, st in slots.items()
                    if st.thread_id not in manual.values()
                }
                # 频道 1 是锚定槽：不参与逐出（用户要求"第一频道一直在第一频道的位置"）
                auto_slots = {s: tid for s, tid in auto_slots.items() if s != 1}
                if not auto_slots:
                    break
                victim = min(
                    auto_slots,
                    key=lambda s: (self.threads[auto_slots[s]].recency,
                                   self.threads[auto_slots[s]].seq),
                )
                if st.recency <= self.threads[auto_slots[victim]].recency:
                    break  # 新会话不比最旧的活跃，不逐出
                del self.slot_map[auto_slots[victim]]
                slot = victim
            self.slot_map[st.thread_id] = slot
            slots[slot] = st
            placed.add(st.thread_id)

        # 5) 持久化 auto 快照（manual 保留原文件内容）
        auto = {s: st.thread_id for s, st in slots.items()}
        if auto != self._last_auto:
            save_channel_map(manual, auto)
            self._last_auto = auto

        out = []
        for slot in sorted(slots):
            st = slots[slot]
            out.append({
                "slot": slot,
                "thread_id": st.thread_id,
                "state": st.state,
                "ts": st.state_ts,
                "last_event_ts": st.last_event_ts,
                "title": st.title or st.thread_id[:8],
            })
        return out
