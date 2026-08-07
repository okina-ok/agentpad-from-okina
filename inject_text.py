"""把文字注入 Codex 桌面端输入框（剪贴板 + 点击 + Ctrl+V）

用法：
    python inject_text.py "帮我写一个快速排序算法"
    echo 文字 | python inject_text.py --stdin

定位：优先用 UIA 找到输入框控件（窗口不全屏/移动过也准）；
      找不到则退回"窗口底部中心"几何估算。
流程：收起置顶模拟器 -> 激活 Codex 窗口 -> 点击输入框 -> 粘贴。
文字留在输入框供审阅，不自动发送。
"""

import argparse
import os
import sys
import threading
import time

import pyperclip
import win32api
import win32con
import win32gui
from pywinauto.keyboard import send_keys
from pywinauto import Desktop


def _force_utf8_stdio():
    """stdout/stderr 统一 UTF-8：ptt.py 按 UTF-8 读注入结果，否则中文丢失。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace",
                               line_buffering=True)
        except Exception:
            pass


def _arm_watchdog(seconds=15):
    """自杀看门狗：任何一步意外卡死时强制退出（os._exit 无视阻塞线程），
    避免 ptt.py 那边干等 30 秒。打印的最后一行就是卡点。"""
    def kill():
        time.sleep(seconds)
        try:
            print("WATCHDOG KILL after %ds" % seconds, flush=True)
        except Exception:
            pass
        os._exit(2)
    threading.Thread(target=kill, daemon=True).start()


def find_windows(title_part):
    results = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            t = win32gui.GetWindowText(hwnd)
            if t and title_part.lower() in t.lower():
                results.append((hwnd, t))
        return True

    win32gui.EnumWindows(cb, None)
    return results


def _activate(hwnd):
    """把窗口提到前台。Windows 有前台锁，SetForegroundWindow 可能被拒，
    先重试，再走"置顶->取消置顶"的强制路径。"""
    for _ in range(3):
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
        if win32gui.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.15)
    try:
        win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                              win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
        win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0,
                              win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
        win32gui.SetForegroundWindow(hwnd)
    except Exception as exc:
        print("activate failed:", exc)
    return win32gui.GetForegroundWindow() == hwnd


def _uia_find_composer(hwnd, rect, timeout=1.5):
    """后台线程跑 UIA 定位输入框，超时返回 {}（不阻塞）。
    Codex 是 Electron，descendants() 遍历在窗口忙碌时会无限挂起，
    绝不能在主流程里硬等（ptt 30 秒超时就是被它耗光的）。"""
    result = {}

    def work():
        try:
            desktop = Desktop(backend="uia")
            w = desktop.window(handle=hwnd)
            for e in w.descendants(control_type="Edit"):
                r = e.rectangle()
                if r.top >= rect[3] - 400 and 30 <= (r.bottom - r.top) <= 200:
                    result["xy"] = ((r.left + r.right) // 2, (r.top + r.bottom) // 2)
                    result["name"] = e.element_info.name[:40]
                    return
        except Exception as exc:
            result["err"] = repr(exc)

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout)
    return result


def _uia_read_composer(hwnd, rect, timeout=1.0):
    """后台线程读回输入框内容，超时返回 {}（视为无法验证，不阻塞）。"""
    result = {}

    def work():
        try:
            desktop = Desktop(backend="uia")
            w = desktop.window(handle=hwnd)
            for e in w.descendants(control_type="Edit"):
                r = e.rectangle()
                if r.top >= rect[3] - 400 and 30 <= (r.bottom - r.top) <= 200:
                    result["val"] = e.get_value() or ""
                    return
        except Exception as exc:
            result["err"] = repr(exc)

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout)
    return result


def _clear_click_point(agent_wins, x, y):
    """若 AgentPad 窗口盖住点击点，临时挪到屏幕右上角（注入后挪回）。
    不再最小化：窗口全程可见，只是可能短暂挪位。"""
    moved = []
    sw = win32api.GetSystemMetrics(0)
    for hwnd in agent_wins:
        try:
            r = win32gui.GetWindowRect(hwnd)
            if r[0] <= x <= r[2] and r[1] <= y <= r[3]:
                w, h = r[2] - r[0], r[3] - r[1]
                win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST,
                                      sw - w - 24, 24, 0, 0,
                                      win32con.SWP_NOSIZE)
                moved.append((hwnd, r))
                print("moved sim out of the way")
        except Exception:
            pass
    return moved


def _restore_moved(moved):
    for hwnd, r in moved:
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST,
                                  r[0], r[1], 0, 0, win32con.SWP_NOSIZE)
        except Exception:
            pass


def send_enter(title="ChatGPT"):
    """聚焦 Codex 输入框并回车发送（模拟器"确定"键）。
    复用注入的几何定位 + 临时挪开模拟器逻辑，不走 UIA。"""
    agent_wins = [hwnd for hwnd, _ in find_windows("AgentPad")]
    wins = find_windows(title)
    if not wins:
        print("ERROR: no window:", title)
        return False
    hwnd, _ = wins[0]
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    _activate(hwnd)
    time.sleep(0.3)
    rect = win32gui.GetWindowRect(hwnd)
    cx, cy = (rect[0] + rect[2]) // 2, rect[3] - 100
    moved = _clear_click_point(agent_wins, cx, cy)
    try:
        win32api.SetCursorPos((cx, cy))
        time.sleep(0.15)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        time.sleep(0.25)
        send_keys("{ENTER}")
        time.sleep(0.2)
        print("enter sent")
        return True
    except Exception as exc:
        print("send failed:", exc)
        return False
    finally:
        _restore_moved(moved)


def inject_text(text, title="ChatGPT"):
    # 1) 找 AgentPad 窗口（不最小化；挡住点击点时才临时挪开，保持 UI 可见）
    #    标题匹配用 "AgentPad"：新模拟器标题是 "AgentPad v1 模拟器 · 4×4 布局"。
    agent_wins = [hwnd for hwnd, _ in find_windows("AgentPad")]

    # 2) 找 Codex 桌面端窗口
    wins = find_windows(title)
    if not wins:
        print("ERROR: no window:", title)
        return False
    hwnd, title = wins[0]
    print("target:", title)

    # 3) 还原并激活窗口（带重试）
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    ok_fg = _activate(hwnd)
    print("activated:", ok_fg)
    time.sleep(0.4)

    rect = win32gui.GetWindowRect(hwnd)
    return inject_into_composer(text, hwnd, rect, agent_wins)


def inject_into_composer(text, hwnd, rect, agent_wins=()):
    """定位输入框 -> 点击 -> 粘贴 -> 验证。"""
    # 4) 定位输入框。默认几何点击（快且稳）：UIA 在你的环境里每次挂起，
    #    只会在设置 AGENTPAD_UIA=1 时尝试（带 1.5s 超时）。
    cx, cy = None, None
    if os.environ.get("AGENTPAD_UIA") == "1":
        found = _uia_find_composer(hwnd, rect)
        if found.get("xy"):
            cx, cy = found["xy"]
            print("composer control:", repr(found.get("name")), (cx, cy))
        elif found.get("err"):
            print("uia locate failed:", found["err"])
        else:
            print("uia locate timeout, use geometry fallback")
    else:
        print("uia disabled, geometry click")
    if cx is None:
        cx = (rect[0] + rect[2]) // 2
        cy = rect[3] - 100
        print("fallback geometry click:", (cx, cy))
    x, y = cx, cy
    moved = _clear_click_point(agent_wins, x, y)
    try:
        win32api.SetCursorPos((x, y))
        time.sleep(0.2)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        time.sleep(0.3)
        print("clicked composer at:", (x, y))

        # 5) 粘贴
        pyperclip.copy(text)
        time.sleep(0.1)
        print("clipboard set")
        send_keys("^v")
        time.sleep(0.3)
        print("pasted")
    except Exception as exc:
        print("paste failed:", exc)
        _restore_moved(moved)
        return False

    # 6) 验证：读回输入框内容（仅 AGENTPAD_UIA=1 时；几何模式不做验证）
    ok = False
    if os.environ.get("AGENTPAD_UIA") == "1":
        got = _uia_read_composer(hwnd, win32gui.GetWindowRect(hwnd))
        val = got.get("val")
        if val is not None:
            ok = text in val
            if ok:
                print("verify OK")
            else:
                print("verify FAIL, composer:", repr(val[:60]))
        elif got.get("err"):
            print("verify skipped:", got["err"])
            ok = True
        else:
            print("verify skipped (uia timeout)")
            ok = True
    else:
        print("verify skipped (geometry mode)")
        ok = True

    _restore_moved(moved)
    print("restored sim windows")
    return ok


def main():
    _force_utf8_stdio()
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="?", default="")
    ap.add_argument("--stdin", action="store_true")
    ap.add_argument("--title", default="ChatGPT", help="Codex 桌面端窗口标题关键字")
    args = ap.parse_args()

    text = sys.stdin.read().strip() if args.stdin else args.text
    if not text:
        print("no text")
        return 1
    _arm_watchdog(15)
    return 0 if inject_text(text, title=args.title) else 1


if __name__ == "__main__":
    sys.exit(main())
