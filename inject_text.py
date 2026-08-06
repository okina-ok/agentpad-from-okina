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


def inject_text(text, title="ChatGPT"):
    # 1) 收起置顶的模拟器，避免挡住输入区
    for hwnd, _ in find_windows("AgentPad 模拟器"):
        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)

    # 2) 找 Codex 桌面端窗口
    wins = find_windows(title)
    if not wins:
        print("ERROR: 找不到 Codex 窗口")
        return False
    hwnd, title = wins[0]
    print("target:", title)

    # 3) 还原并激活窗口
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    win32gui.SetForegroundWindow(hwnd)
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
    print("injected:", text[:40])
    return True


def main():
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
