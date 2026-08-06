"""频道绑定与槽位稳定性自测（不依赖真实日志库，直接构造 ThreadState）"""

import os
import tempfile
import time

import log_state as ls

BASE = int(time.time())


def tid(n):
    return f"019f0000-0000-0000-0000-{n:012d}"


def mk_tracker(tmp):
    ls.CHANNEL_MAP_FILE = os.path.join(tmp, "channel_map.json")
    t = ls.MultiLogTracker(db_path=os.path.join(tmp, "none.sqlite"),
                           state_db_path="", cache_path=os.path.join(tmp, "cache.json"))
    t.pool = {tid(i): {"title": f"会话{i}", "recency": 1000 + i, "pinned": False}
              for i in range(1, 9)}
    t.active_tid = tid(8)
    # 建 8 个线程状态，recency 按序号递增（8 最新）
    for i in range(1, 9):
        st = t._ensure_thread(tid(i))
        st.recency = BASE + i
        st.state = "idle"
    return t


def main():
    tmp = tempfile.mkdtemp()
    t = mk_tracker(tmp)

    # 1) 首次分配：6 个槽，按 recency 最新在前
    r1 = t.get_slots()
    assert [s["slot"] for s in r1] == [1, 2, 3, 4, 5, 6], r1
    assert r1[0]["thread_id"] == tid(8), "频道1 应是最新会话"
    print("1) 首轮分配 OK:", [(s["slot"], s["title"]) for s in r1])

    # 2) 重复调用：映射稳定（不挪位）
    r2 = t.get_slots()
    assert [(s["slot"], s["thread_id"]) for s in r2] == [(s["slot"], s["thread_id"]) for s in r1]
    print("2) 重复调用稳定 OK")

    # 3) 第 9 个会话出现且最新 -> 逐出最不活跃的"非频道1"会话；频道1 不动
    st9 = t._ensure_thread(tid(9))
    st9.recency = BASE + 100
    st9.state = "running"
    r3 = t.get_slots()
    assert r3[0]["thread_id"] == tid(8), "频道1 被逐出了！"
    assert tid(9) in [s["thread_id"] for s in r3], "新会话没上槽"
    assert tid(1) not in [s["thread_id"] for s in r3], "应该逐出最旧的会话1"
    print("3) 频道1 锚定 + LRU 逐出 OK:", [(s["slot"], s["title"]) for s in r3])

    # 4) 让频道1 的会话变成最旧，也不被逐出（第 10 个会话出现）
    t.threads[tid(8)].recency = BASE - 1000
    st10 = t._ensure_thread(tid(10))
    st10.recency = BASE + 200
    r4 = t.get_slots()
    assert r4[0]["thread_id"] == tid(8), "频道1 仍被逐出！"
    print("4) 频道1 即使最旧也锚定 OK:", [(s["slot"], s["title"]) for s in r4])

    # 5) 手工绑定：绑定的会话固定到指定频道，且不占自动名额
    ls.save_channel_map({3: tid(9)}, {})
    r5 = t.get_slots()
    slot3 = next(s for s in r5 if s["slot"] == 3)
    assert slot3["thread_id"] == tid(9), "手工绑定未生效"
    print("5) 手工绑定 OK")

    # 6) 重启持久化：同一缓存重建 tracker，槽位映射不变
    t._save_cache()
    t2 = ls.MultiLogTracker(db_path=os.path.join(tmp, "none.sqlite"),
                            state_db_path="", cache_path=os.path.join(tmp, "cache.json"))
    t2.pool = dict(t.pool)
    r6 = t2.get_slots()
    assert [(s["slot"], s["thread_id"]) for s in r6] == [(s["slot"], s["thread_id"]) for s in r5], \
        "重启后映射变了"
    print("6) 重启持久化 OK")

    # 7) 取消手工绑定：manual 段清空，线程保留原槽（稳定性设计）
    ls.save_channel_map({}, {})
    r7 = t.get_slots()
    cm7 = ls.load_channel_map()
    assert cm7["manual"] == {}, "manual 未清空"
    assert r7[2]["thread_id"] == tid(9), "取消绑定后线程应保留原槽"
    print("7) 取消手工绑定 OK:", [(s["slot"], s["title"]) for s in r7])

    # 8) 12 个会话涌入，频道 1 仍锚定，槽数仍为 6
    for n in (11, 12):
        st = t._ensure_thread(tid(n))
        st.recency = BASE + n * 10
        st.state = "running"
    r8 = t.get_slots()
    assert r8[0]["thread_id"] == tid(8), "频道1 被 12 会话冲掉了"
    assert len(r8) == 6
    print("8) 12 会话下频道1 锚定 OK:", [(s["slot"], s["title"]) for s in r8])

    # 9) 截断 ID 一律忽略
    assert ls.extract_tid("thread_id=019fd6a2-46d2} ...") is None
    assert ls.extract_tid("thread_id=019fd6a2-46d2-7310-af72-582411385d96}") == \
        "019fd6a2-46d2-7310-af72-582411385d96"
    print("9) 截断 ID 忽略 OK")

    print("CHANNEL BIND TESTS PASSED")


if __name__ == "__main__":
    main()
