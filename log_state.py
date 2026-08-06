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

TOOL_ITEM_RE = r'"item":\{.*?"type":"(function_call|custom_tool_call|web_search_call)"'

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
                    m = re.search(r"thread_id=([0-9a-f-]+)", body)
                    if m:
                        self.watched_thread = m.group(1)
                        break
            if not self.last_submission_id:
                cur.execute(
                    "SELECT feedback_log_body FROM logs WHERE feedback_log_body LIKE "
                    "'%submission.id=%' ORDER BY id DESC LIMIT 100"
                )
                for (body,) in cur.fetchall():
                    m = re.search(r'submission\.id="([0-9a-f-]+)"', body or "")
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
        # 只有"有意义"的事件才刷新活动时间；token/速率/状态等后台杂音不刷。
        meaningful = False

        is_tool = any(m in body for m in TOOL_MARKERS)
        # 工具周期结束：completion 行（含巨型周期行）表示工具已跑完，
        # 应立刻回到 thinking，而不是继续亮 running 等下一批日志。
        is_cycle_over = "tool call completed" in body
        is_completed = '"type":"response.completed"' in body
        is_message_item = 'item_type="message"' in body and not is_tool
        m_nf = re.search(r"(?:model_)?needs_follow_up=(\w+)", body)
        is_turn_end_hint = m_nf is not None and m_nf.group(1) == "false"
        is_midturn_hint = m_nf is not None and m_nf.group(1) == "true"
        # submission.id 去重：同一 submission 内的 user_input 前缀行
        # （post sampling token usage / Output item message/reasoning 等）都不是用户消息，
        # 只有"新的 submission.id"（用户真正发了新消息）才算新回合。
        m_sub = re.search(r'submission\.id="([0-9a-f-]+)"', body)
        sid = m_sub.group(1) if m_sub is not None else None
        is_new_submission = sid is not None and sid != self.last_submission_id
        is_real_user = (
            "op.dispatch.user_input" in body
            and not is_tool
            and not is_completed
            and is_new_submission
        )

        # 线程过滤：只处理当前关注线程的 trace 行；SSE 行无法归属线程，放行。
        # 例外：其他线程的真实用户消息（user_input 且无工具标记）允许通过，
        # 以便 AgentPad 自动跟随最近使用的会话。
        m = re.search(r"thread_id=([0-9a-f-]+)", body)
        if m is not None and self.watched_thread and m.group(1) != self.watched_thread:
            if not is_real_user:
                return

        # 行真正被处理后才提交 submission.id（忽略的行不污染去重状态）
        if sid is not None:
            self.last_submission_id = sid

        # 1) 工具完成 -> thinking（工具已结束，模型在处理结果；不启用 done）
        if is_cycle_over:
            self.last_response_ts = 0
            self.last_content_ts = 0
            self._set("thinking", ts, "tool completed")
            meaningful = True
        # 2) 工具行 -> running（实时：工具 dispatch 时即落盘）
        elif is_tool:
            self.last_response_ts = 0  # 工具周期不启用回合结束判定
            self.last_content_ts = 0
            self._set("running", ts, "tool executing")
            meaningful = True
        # 3) 响应完成（纯文本回合的结束证据）-> 记录完成点；已在 done 则保持绿
        elif is_completed:
            self.last_response_ts = ts
            if self.state != "done":
                self._set("thinking", ts, "response completed")
            meaningful = True
        # 4) 回合结束线索：post sampling 行 needs_follow_up=false -> 兜底完成点
        #    （注意：中间文本回复也会出现 false，因此只作兜底，安静期更长）
        elif is_turn_end_hint:
            self.last_content_ts = ts
            if self.state != "done":
                self._set("thinking", ts, "turn end hint")
            meaningful = True
        # 5) 回合继续线索：needs_follow_up=true -> 撤销兜底完成点
        elif is_midturn_hint:
            self.last_content_ts = 0
            if self.state != "done":
                self._set("thinking", ts, "turn continue")
            meaningful = True
        # 6) 助手文本消息完成（item_type=message）-> 兜底完成点；已在 done 则保持绿
        elif is_message_item:
            self.last_content_ts = ts
            if self.state != "done":
                self._set("thinking", ts, "message done")
            meaningful = True
        # 7) 输出阶段（回答文字开始流式出现）-> 直接 done（绿色）
        #    "最后思考完输出"不再一直紫；若之后有工具行会立刻转 running。
        elif '"type":"response.output_text.delta"' in body:
            self.last_response_ts = ts
            self._set("done", ts, "outputting answer")
            meaningful = True
        # 8) 真实用户消息 -> 新回合 thinking
        elif is_real_user:
            if m is not None:
                self.watched_thread = m.group(1)
            self.last_response_ts = 0
            self.last_content_ts = 0
            self._set("thinking", ts, "user input received")
            meaningful = True
        # 9) thinking：模型生成中（reasoning 等）
        elif ('"type":"response.reasoning_text.delta"' in body
              or '"type":"response.created"' in body
              or '"type":"response.in_progress"' in body):
            if self.state != "done":
                self._set("thinking", ts, "thinking")
            meaningful = True
        # 10) 兜底：output_item.added(function_call)（span 没抓到时的保险）
        elif re.search(r'"type":"response\.output_item\.added"', body):
            mm = re.search(TOOL_ITEM_RE, body)
            if mm:
                self.last_response_ts = 0
                self._set("running", ts, f"tool: {mm.group(1)}")
                meaningful = True
        if meaningful:
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
