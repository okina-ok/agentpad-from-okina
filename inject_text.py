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
import sys
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
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


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


def inject_text(text, title="ChatGPT"):
    # 1) 收起置顶的模拟器，避免挡住输入区（结束后会恢复）
    #    标题匹配用 "AgentPad"：新模拟器标题是 "AgentPad v1 模拟器 · 4×4 布局"，
    #    不再包含连续串 "AgentPad 模拟器"。
    agent_wins = [hwnd for hwnd, _ in find_windows("AgentPad")]
    for hwnd in agent_wins:
        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)

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
    _activate(hwnd)
    time.sleep(0.4)

    # 4) 定位输入框并点击
    rect = win32gui.GetWindowRect(hwnd)
    cx, cy = None, None
    try:
        # 优先：UIA 找输入框 Edit 控件（窗口底部区域内的）
        desktop = Desktop(backend="uia")
        w = desktop.window(handle=hwnd)
        for e in w.descendants(control_type="Edit"):
            r = e.rectangle()
            if r.top >= rect[3] - 350 and 30 <= (r.bottom - r.top) <= 160:
                cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
                print("composer control:", repr(e.element_info.name[:40]), (r.left, r.top, r.right, r.bottom))
                break
    except Exception as exc:
        print("uia locate failed:", exc)
    if cx is None:
        # 退回：窗口底部中心
        cx = (rect[0] + rect[2]) // 2
        cy = rect[3] - 90
        print("fallback geometry click:", (cx, cy))
    x, y = cx, cy
    try:
        win32api.SetCursorPos((x, y))
        time.sleep(0.2)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        time.sleep(0.3)

        # 5) 粘贴
        pyperclip.copy(text)
        time.sleep(0.1)
        send_keys("^v")
        time.sleep(0.3)
        print("pasted")
    except Exception as exc:
        print("paste failed:", exc)
        return False

    # 6) 验证：读回输入框内容
    ok = False
    try:
        desktop = Desktop(backend="uia")
        w = desktop.window(handle=hwnd)
        r0 = win32gui.GetWindowRect(hwnd)
        for e in w.descendants(control_type="Edit"):
            r = e.rectangle()
            if r.top >= r0[3] - 350 and 30 <= (r.bottom - r.top) <= 160:
                val = e.get_value() or ""
                ok = text in val
                if ok:
                    print("verify OK")
                else:
                    print("verify FAIL, composer:", repr(val[:60]))
                break
    except Exception as exc:
        print("verify skipped:", exc)
        ok = True

    # 7) 恢复被收起的 AgentPad 窗口：回到桌面但不抢前台（Codex 保持在前，
    #    输入框仍可见，用户审阅后点"确定"或直接在 Codex 里发送）
    for hwnd in agent_wins:
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0,
                                  win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
        except Exception:
            pass
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
    return 0 if inject_text(text, title=args.title) else 1


if __name__ == "__main__":
    sys.exit(main())
