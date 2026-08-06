"""打印 AgentPad 当前状态（调试用）

用法：
    python check_status.py
"""

import json
import os

STATES_CN = {
    "thinking": "思考中(紫)",
    "running": "运行工具(青)",
    "waiting": "等你输入(琥珀)",
    "done": "已完成(绿)",
    "error": "出错(红)",
    "idle": "空闲(灰)",
}


def main():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "display_state_v2.json")
    try:
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
    except OSError:
        print("display file not found - is the daemon running? (start_live.ps1)")
        raise SystemExit(1)
    a = d["agents"][0]
    label = STATES_CN.get(a["state"], a["state"])
    print(f"slot1: {a['state']} / {a['fresh']}  ->  {label}")
    print(f"source: {a.get('src', '?')}   summary: {a.get('summary', '')}")


if __name__ == "__main__":
    main()
