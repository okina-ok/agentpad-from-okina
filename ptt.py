"""AgentPad PTT 语音原型

按住板载 BOOT 键（GPIO9）说话 -> 松开 -> 本地 faster-whisper 转文字。

用法：
    python ptt.py --port COM5                # 正常模式（读板子串口）
    python ptt.py --demo < events.json       # 演示模式（从 stdin 读按键事件，不用板子）

结果会打印到屏幕，同时追加写入 ptt_result.txt。
"""

import argparse
import json
import os
import queue
import sys
import threading
import time
import wave

import numpy as np

MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "whisper-small")
RESULT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ptt_result.txt")
WAV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ptt_tmp.wav")

RATE = 16000


def log_result(text):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), text)
    print(line, flush=True)
    try:
        with open(RESULT_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


class Recorder:
    """按下开始录音，松开停止并保存 WAV。"""

    def __init__(self):
        self._frames = []
        self._stream = None
        self.lock = threading.Lock()

    def start(self):
        with self.lock:
            if self._stream is not None:
                return False
            self._frames = []
            self._stream = sd.InputStream(
                samplerate=RATE, channels=1, dtype="int16", callback=self._cb
            )
            self._stream.start()
            return True

    def _cb(self, indata, frames, time_info, status):
        self._frames.append(indata.copy())

    def stop(self):
        with self.lock:
            if self._stream is None:
                return None
            self._stream.stop()
            self._stream.close()
            self._stream = None
            if not self._frames:
                return None
            data = np.concatenate(self._frames)
            self._frames = []
            return data


def save_wav(data):
    with wave.open(WAV_FILE, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(RATE)
        wf.writeframes(data.tobytes())
    return WAV_FILE


def transcribe(model, wav_path):
    segments, _info = model.transcribe(
        wav_path, language="zh", vad_filter=True, beam_size=5
    )
    return "".join(seg.text for seg in segments).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM5")
    ap.add_argument("--model", default=MODEL_DIR)
    ap.add_argument("--demo", action="store_true", help="从 stdin 读按键事件，不用串口")
    args = ap.parse_args()

    log_result("loading model from %s ..." % args.model)
    model = WhisperModel(args.model, device="cpu", compute_type="int8")
    log_result("model ready. PTT 就绪：按住 BOOT 说话，松开转文字")

    rec = Recorder()
    busy = threading.Event()

    def handle_press():
        if rec.start():
            log_result(">>> 录音开始（说话吧）")

    def handle_release():
        data = rec.stop()
        if data is None:
            return
        dur = len(data) / RATE
        if dur < 0.25:
            log_result(">>> 太短，忽略（%.2fs）" % dur)
            return
        log_result(">>> 录音结束（%.1fs），转写中..." % dur)

        def work():
            try:
                wav = save_wav(data)
                text = transcribe(model, wav)
                log_result(">>> 转写结果：" + (text or "（没听清）"))
            except Exception as exc:
                log_result(">>> 转写出错：%r" % exc)
            finally:
                busy.clear()

        busy.set()
        threading.Thread(target=work, daemon=True).start()

    if args.demo:
        log_result("demo 模式：从 stdin 读 {"ev":"key","i":1,"s":1/0}")
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("ev") == "key" and ev.get("i") == 1:
                if ev.get("s") == 1:
                    handle_press()
                elif ev.get("s") == 0:
                    handle_release()
        return

    import serial

    s = serial.Serial(args.port, 115200, timeout=0.5)
    log_result("串口 %s 已连接，等待按键..." % args.port)
    buf = ""
    while True:
        try:
            chunk = s.read(512).decode("utf-8", errors="replace")
        except serial.SerialException:
            log_result("串口断开，重连中...")
            time.sleep(1)
            try:
                s = serial.Serial(args.port, 115200, timeout=0.5)
            except Exception:
                continue
            continue
        if not chunk:
            continue
        buf += chunk
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("ev") == "key" and ev.get("i") == 1:
                if ev.get("s") == 1:
                    handle_press()
                elif ev.get("s") == 0:
                    handle_release()


if __name__ == "__main__":
    main()
