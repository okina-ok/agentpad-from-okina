"""合成日志 -> LogStateTracker 状态机单测（不碰真实日志库）"""

import os
import sqlite3
import tempfile
import time

from log_state import LogStateTracker

TID = "019fd6a2-46d2-7310-af72-582411385d96"
OTHER = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
USER_SID = "019f0000-0000-0000-0000-000000000001"
OTHER_SID = "019f0000-0000-0000-0000-000000000002"
NEW_SID = "019f0000-0000-0000-0000-000000000003"


def make_db(path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE logs (id INTEGER PRIMARY KEY, ts INTEGER, target TEXT, feedback_log_body TEXT)")
    return con


def insert(con, rows):
    for rid, ts, body in rows:
        con.execute("INSERT INTO logs (id, ts, target, feedback_log_body) VALUES (?,?,NULL,?)", (rid, ts, body))
    con.commit()


def insert_t(con, rows):
    """带 target 列插入（用于回显行过滤测试）。"""
    for rid, ts, tgt, body in rows:
        con.execute("INSERT INTO logs (id, ts, target, feedback_log_body) VALUES (?,?,?,?)",
                    (rid, ts, tgt, body))
    con.commit()


def user_msg(rid, ts, tid=TID, sid=USER_SID):
    return (rid, ts, 'session_loop{thread_id=%s}:submission_dispatch{otel.name="op.dispatch.user_input" submission.id="%s" codex.op="user_input"}' % (tid, sid))


def tool_rows(rid, ts, tid=TID, sid=USER_SID):
    # 仅 dispatch/执行中的行（不包含 completion）
    return [
        (rid, ts, 'SSE event: {"type":"response.function_call_arguments.delta","delta":"x"}'),
        (rid + 1, ts, 'SSE event: {"type":"response.output_item.added","item":{"type":"function_call"}}'),
        (rid + 2, ts, 'handle_output_item_done: ToolCall: shell_command {"command":"echo hi"} thread_id=%s' % tid),
        (rid + 3, ts, 'session_loop{thread_id=%s}:submission_dispatch{otel.name="op.dispatch.user_input" submission.id="%s"} Output item item_type="function_call"' % (tid, sid)),
    ]


def completion_row(rid, ts, tid=TID, sid=USER_SID):
    return (rid, ts, 'session_loop{thread_id=%s}:submission_dispatch{otel.name="op.dispatch.user_input" submission.id="%s"}:handle_output_item_done:handle_tool_call: tool call completed event.name="codex.tool_call" tool_name=shell_command total_duration_ms=5000' % (tid, sid))


def giant_cycle_row(rid, ts, tid=TID, sid=USER_SID):
    return (rid, ts, 'session_loop{thread_id=%s}:submission_dispatch{otel.name="op.dispatch.user_input" submission.id="%s"}:handle_output_item_done: ToolCall: shell_command {...} handle_tool_call: tool call completed ... SSE event: {"type":"response.completed"}' % (tid, sid))


def reasoning(rid, ts):
    return (rid, ts, 'SSE event: {"type":"response.reasoning_text.delta","delta":"thinking..."}')


def text_completed(rid, ts, tid=TID, sid=USER_SID):
    return (rid, ts, 'session_loop{thread_id=%s}:submission_dispatch{otel.name="op.dispatch.user_input" submission.id="%s"}:run_sampling_request: SSE event: {"type":"response.completed"}' % (tid, sid))


def tool_completed(rid, ts, tid=TID, sid=USER_SID):
    return (rid, ts, 'session_loop{thread_id=%s}:submission_dispatch{otel.name="op.dispatch.user_input" submission.id="%s"}:handle_responses{otel.name="function_call" tool_name="shell_command"}: SSE event: {"type":"response.completed"}' % (tid, sid))


def same_sub_noise(rid, ts, kind="token", tid=TID, sid=USER_SID):
    if kind == "token":
        tail = "session_task.run:run_turn: post sampling token usage model_needs_follow_up=true"
    elif kind == "message":
        tail = 'session_task.run:run_turn:run_sampling_request:handle_output_item_done: Output item item_type="message"'
    else:
        tail = 'session_task.run:run_turn:run_sampling_request:handle_output_item_done: Output item item_type="reasoning"'
    return (rid, ts, 'session_loop{thread_id=%s}:submission_dispatch{otel.name="op.dispatch.user_input" submission.id="%s" codex.op="user_input"}:%s' % (tid, sid, tail))


def message_item(rid, ts, tid=TID, sid=USER_SID):
    return (rid, ts, 'session_loop{thread_id=%s}:submission_dispatch{otel.name="op.dispatch.user_input" submission.id="%s"}:handle_output_item_done: Output item item_type="message" item_id="msg-1"' % (tid, sid))


def post_sampling(rid, ts, nf, tid=TID, sid=USER_SID):
    return (rid, ts, 'session_loop{thread_id=%s}:submission_dispatch{otel.name="op.dispatch.user_input" submission.id="%s"}:session_task.run:run_turn: post sampling token usage needs_follow_up=%s' % (tid, sid, nf))


def output_text(rid, ts):
    return (rid, ts, 'SSE event: {"type":"response.output_text.delta","delta":"answer..."}')


def reasoning_row(rid, ts):
    return (rid, ts, 'SSE event: {"type":"response.reasoning_text.delta","delta":"thinking..."}')


def main():
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "logs_test.sqlite")
    cache = os.path.join(tmp, "cache.json")
    con = make_db(db)

    t0 = int(time.time())
    tracker = LogStateTracker(db_path=db, cache_path=cache)

    # 1) 真实用户消息 -> thinking + 绑定线程
    insert(con, [user_msg(1, t0)])
    tracker.poll()
    assert tracker.state == "thinking", tracker.state
    assert tracker.watched_thread == TID, tracker.watched_thread
    assert tracker.last_response_ts == 0
    print("1) user msg -> thinking OK, watched=%s" % tracker.watched_thread[:8])

    # 2) 工具行（含工具周期的 response.completed）-> running，不启用 done
    insert(con, tool_rows(10, t0 + 1))
    tracker.poll()
    assert tracker.state == "running", tracker.state
    assert tracker.last_response_ts == 0
    print("2) tool rows -> running OK")

    # 3) 工具完成后模型恢复思考（reasoning deltas）-> thinking
    insert(con, [tool_completed(20, t0 + 2), reasoning(21, t0 + 2)])
    tracker.poll()
    assert tracker.state == "thinking", tracker.state
    print("3) post-tool reasoning -> thinking OK")

    # 3b) "tool call completed" 是工具开始执行的回执（execution_started=true），
    #     应保持 running，不被拉回 thinking；真正的 reasoning 输出才回 thinking
    insert(con, [completion_row(25, t0 + 4)])
    tracker.poll()
    assert tracker.state == "running", tracker.state
    insert(con, [giant_cycle_row(26, t0 + 4)])
    tracker.poll()
    assert tracker.state == "running", tracker.state
    insert(con, [reasoning(27, t0 + 4)])
    tracker.poll()
    assert tracker.state == "thinking", tracker.state
    print("3b) tool call completed keeps running; reasoning -> thinking OK")

    # 3c) 同一 submission 的后台噪音行（token usage / item_type message / reasoning）
    #     不是用户消息：状态与 last_response_ts 都不该被重置
    insert(con, [same_sub_noise(22, t0 + 4), same_sub_noise(23, t0 + 4, kind="message"), same_sub_noise(24, t0 + 4, kind="reasoning")])
    tracker.poll()
    assert tracker.state == "thinking", tracker.state
    print("3c) same-submission noise not treated as user msg OK")

    # 4) 文本响应完成 -> thinking + last_response_ts（可判 done）
    insert(con, [reasoning(30, t0 + 5), text_completed(31, t0 + 5)])
    tracker.poll()
    assert tracker.state == "thinking" and tracker.last_response_ts == t0 + 5
    print("4) text response.completed -> thinking + armed OK")

    # 5) 其他线程的工具 trace 行被忽略，不打断当前状态
    #    （SSE 行无线程归属，按"最近使用的会话"假设放行——单会话场景无影响）
    insert(con, [
        (40, t0 + 4, 'session_loop{thread_id=%s}:submission_dispatch{otel.name="op.dispatch.user_input" submission.id="%s"} handle_output_item_done: ToolCall: shell_command {"command":"echo other"}' % (OTHER, OTHER_SID)),
        (41, t0 + 4, 'session_loop{thread_id=%s}:submission_dispatch{otel.name="op.dispatch.user_input" submission.id="%s"} tool call completed total_duration_ms=100' % (OTHER, OTHER_SID)),
    ])
    tracker.poll()
    assert tracker.state == "thinking", tracker.state
    print("5) other-thread tool rows ignored OK")

    # 6) 其他线程的真实用户消息 -> 切换绑定
    insert(con, [user_msg(50, t0 + 5, tid=OTHER, sid=OTHER_SID)])
    tracker.poll()
    assert tracker.state == "thinking" and tracker.watched_thread == OTHER and tracker.last_submission_id == OTHER_SID
    print("6) other-thread user msg switches watch OK")

    # 7) 工具行里混入 reasoning 文本（提到 function_call）不误判 running
    insert(con, [(60, t0 + 6, 'SSE event: {"type":"response.reasoning_text.delta","delta":"maybe call function_call here"}' )])
    tracker.poll()
    assert tracker.state == "thinking", tracker.state
    print("7) reasoning mentioning function_call stays thinking OK")

    # 8) 同线程新的 submission.id（用户在当前会话又发了一条消息）-> 新回合 thinking
    insert(con, [user_msg(70, t0 + 7, tid=OTHER, sid=NEW_SID)])
    tracker.poll()
    assert tracker.state == "thinking" and tracker.last_response_ts == 0 and tracker.last_submission_id == NEW_SID
    print("8) new submission same thread -> new turn OK")

    # 9) 助手文本消息完成 -> 兜底完成点（last_content_ts）
    insert(con, [message_item(80, t0 + 8, tid=OTHER, sid=NEW_SID)])
    tracker.poll()
    assert tracker.state == "thinking" and tracker.last_content_ts == t0 + 8, (tracker.state, tracker.last_content_ts)
    print("9) message item arms content-done OK")

    # 10) post sampling needs_follow_up=true -> 撤销兜底完成点
    insert(con, [post_sampling(81, t0 + 9, "true", tid=OTHER, sid=NEW_SID)])
    tracker.poll()
    assert tracker.last_content_ts == 0, tracker.last_content_ts
    print("10) nf=true clears content-done OK")

    # 11) post sampling needs_follow_up=false -> 兜底完成点
    insert(con, [post_sampling(82, t0 + 10, "false", tid=OTHER, sid=NEW_SID)])
    tracker.poll()
    assert tracker.state == "thinking" and tracker.last_content_ts == t0 + 10, (tracker.state, tracker.last_content_ts)
    print("11) nf=false arms content-done OK")

    # 12) 工具行到达 -> 撤销兜底完成点并转 running
    insert(con, tool_rows(83, t0 + 11, tid=OTHER, sid=NEW_SID))
    tracker.poll()
    assert tracker.state == "running" and tracker.last_content_ts == 0, (tracker.state, tracker.last_content_ts)
    print("12) tool rows clear content-done + running OK")

    # 13) 回答文字开始输出 -> 直接 done（绿色），不再停留在 thinking
    insert(con, [reasoning_row(90, t0 + 12), output_text(91, t0 + 12)])
    tracker.poll()
    assert tracker.state == "done", tracker.state
    print("13) output_text.delta -> done OK")

    # 14) 输出后的 response.completed / message item / nf=false 不把绿拉回紫
    insert(con, [text_completed(92, t0 + 13, tid=OTHER, sid=NEW_SID),
                 message_item(93, t0 + 13, tid=OTHER, sid=NEW_SID),
                 post_sampling(94, t0 + 13, "false", tid=OTHER, sid=NEW_SID)])
    tracker.poll()
    assert tracker.state == "done", tracker.state
    print("14) post-output rows keep done OK")

    # 15) 新用户消息 -> 立即回 thinking
    insert(con, [user_msg(95, t0 + 14, tid=OTHER, sid="019f0000-0000-0000-0000-000000000004")])
    tracker.poll()
    assert tracker.state == "thinking", tracker.state
    print("15) new user msg after done -> thinking OK")

    # 16) 输出后 3 秒内的工具行 = 回合收尾噪音，保持 done（不横跳）
    insert(con, [reasoning_row(96, t0 + 15), output_text(97, t0 + 15)] + tool_rows(98, t0 + 15, tid=OTHER, sid="019f0000-0000-0000-0000-000000000004"))
    tracker.poll()
    assert tracker.state == "done", tracker.state
    print("16) tool rows within 3s of done -> stays done OK")

    # 17) 输出后超过 3 秒的工具行 -> 转 running（真正的新活动）
    insert(con, tool_rows(110, t0 + 20, tid=OTHER, sid="019f0000-0000-0000-0000-000000000004"))
    tracker.poll()
    assert tracker.state == "running", tracker.state
    print("17) tool rows after 3s hold -> running OK")

    # 18) transport 回显行（HTTP 请求体，含历史引用文本）不触发任何状态变化
    insert_t(con, [(120, t0 + 21, "codex_http_client::transport",
                    'session_loop{thread_id=%s}: HTTP request body: 历史笔记提到 '
                    'needs_follow_up=false / tool call completed / response.completed '
                    '都是纯文本引用，不是事件' % OTHER)])
    tracker.poll()
    assert tracker.state == "running", tracker.state
    print("18) transport echo rows skipped OK")

    con.close()
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
