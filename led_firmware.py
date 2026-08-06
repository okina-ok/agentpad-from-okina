"""AgentPad 灯珠原型固件（ESP32-C3 / MicroPython，上传为板子 /main.py）

下行帧（PC→板）：{"v":1,"seq":N,"keys":[{"i":1,"s":"running","c":"#00E5A0","e":"solid"},...]}
  - i：1 起始的灯序号；c：颜色 hex；e：solid / breathe / flash / off
上行（板→PC）：ACK {"v":1,"ack":N}；按键 {"v":1,"ev":"key","i":1,"s":1/0}

原型先用板载 WS2812（GPIO8）单灯验证链路；接 13 键灯条后改 LED_PIN / LED_COUNT。
"""

import json
import machine
import sys
import time
from neopixel import NeoPixel

LED_PIN = 8
LED_COUNT = 1          # 原型 1 颗；13 键灯条时改 13
KEY_PIN = 9            # BOOT 键（原型当 agent 键 1）

colors = {}             # idx -> (r,g,b)
effects = {}            # idx -> effect 名
led = NeoPixel(machine.Pin(LED_PIN), LED_COUNT)
for i in range(LED_COUNT):
    led[i] = (0, 0, 0)
led.write()


def parse_hex(s):
    s = (s or "#555555").lstrip("#")
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return (85, 85, 85)


def apply_key(i, color, effect):
    idx = i - 1
    if 0 <= idx < LED_COUNT:
        colors[idx] = color
        effects[idx] = effect


def render(now):
    # 三角波呼吸（整数运算）：phase 0..255 -> factor 0..255..0
    phase = (now >> 2) & 0xFF
    factor = phase * 2 if phase < 128 else (255 - phase) * 2
    for i in range(LED_COUNT):
        c = colors.get(i, (0, 0, 0))
        e = effects.get(i, "off")
        if e == "solid":
            led[i] = c
        elif e == "breathe":
            led[i] = tuple((x * factor) // 255 for x in c)
        elif e == "flash":
            led[i] = c if (now // 250) % 2 == 0 else (0, 0, 0)
        else:
            led[i] = (0, 0, 0)
    led.write()


def handle_line(line):
    try:
        ev = json.loads(line)
    except ValueError:
        return
    if not isinstance(ev, dict):
        return
    for k in ev.get("keys") or []:
        try:
            apply_key(int(k.get("i", 1)), parse_hex(k.get("c")), k.get("e", "off"))
        except (ValueError, TypeError):
            pass
    seq = ev.get("seq")
    if seq is not None:
        print(json.dumps({"v": 1, "ack": seq}))


try:
    import select
    poller = select.poll()
    poller.register(sys.stdin, select.POLLIN)
    HAVE_POLL = True
except Exception:
    HAVE_POLL = False

key = machine.Pin(KEY_PIN, machine.Pin.IN, machine.Pin.PULL_UP)
last = 1
debounce = 0

print("AgentPad LED firmware ready, leds=%d" % LED_COUNT)

while True:
    now = time.ticks_ms()
    level = key.value()
    if level != last:
        if debounce == 0:
            debounce = now
        elif time.ticks_diff(now, debounce) >= 20:
            last = level
            debounce = 0
            print(json.dumps({"v": 1, "ev": "key", "i": 1, "s": 1 if level == 0 else 0}))

    if HAVE_POLL and poller.poll(0):
        line = sys.stdin.readline()
        if line:
            handle_line(line.strip())

    render(now)
    time.sleep_ms(10)
