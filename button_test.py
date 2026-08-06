"""AgentPad 按键测试固件（MicroPython / ESP32-C3）

用途：验证"按键 -> 串口 -> PC"链路。实验阶段用，成品固件会重写。

接线：
    - 按钮一头接 BUTTON_PINS 里的 GPIO，另一头接 GND
    - 按下 = 导通到 GND（内部上拉，默认高电平）
    - 没有外接按钮时：直接用板子自带的 BOOT 按钮（GPIO9）当测试键

行为：
    - 每个按键按下打印 {"ev":"key","i":N,"s":1}，松开打印 {"ev":"key","i":N,"s":0}
    - 板载 RGB（如有，ESP32-C3-DevKitM-1 是 GPIO8）按下亮青、松开熄灭

上传：Thonny 打开本文件 -> 保存到板子为 main.py -> 复位运行。
"""

import json
import machine
import time

# 要测试的按键 GPIO（按你实际接线改）
# 现在用板子自带的 BOOT 按钮（GPIO9）测试，不需要额外按键
BUTTON_PINS = [9]

# 板载 RGB（ESP32-C3-DevKitM-1 是 GPIO8 的 WS2812；没有就设为 None）
LED_PIN = 8

buttons = []
for pin in BUTTON_PINS:
    buttons.append({
        "i": len(buttons) + 1,
        "pin": machine.Pin(pin, machine.Pin.IN, machine.Pin.PULL_UP),
        "last": 1,       # 1 = 松开（上拉）
        "debounce": 0,
    })

led = None
if LED_PIN is not None:
    try:
        from neopixel import NeoPixel
        led = NeoPixel(machine.Pin(LED_PIN), 1)
        led[0] = (0, 0, 0)
        led.write()
    except Exception:
        led = None

print("AgentPad button test ready, buttons:", BUTTON_PINS)

while True:
    now = time.ticks_ms()
    for b in buttons:
        level = b["pin"].value()
        # 去抖：电平变化后等 20ms 再确认
        if level != b["last"]:
            if b["debounce"] == 0:
                b["debounce"] = now
            elif time.ticks_diff(now, b["debounce"]) >= 20:
                b["last"] = level
                b["debounce"] = 0
                print(json.dumps({"ev": "key", "i": b["i"], "s": 1 if level == 0 else 0}))
                if led is not None:
                    led[0] = (0, 229, 160) if level == 0 else (0, 0, 0)
                    led.write()
    time.sleep_ms(5)
