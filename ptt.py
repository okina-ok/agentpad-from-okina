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
import subprocess
import sys
import threading
import time
import wave

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

INJECT_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "inject_text.py")

MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "whisper-small")
RESULT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ptt_result.txt")
WAV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ptt_tmp.wav")

RATE = 16000


def _force_utf8_stdio():
    """stdout/stderr 统一 UTF-8：子进程（模拟器）按 UTF-8 读管道，
    否则中文状态标记在 GBK 编码下无法被识别，UI 会卡在"转写中"。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def log_result(text):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), text)
    print(line, flush=True)
    try:
        with open(RESULT_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def log_error(exc):
    import traceback

    text = "!!! ERROR: %s\n%s" % (exc, traceback.format_exc())
    print(text, flush=True)  # 同时打到 stdout，让模拟器能识别异常状态
    try:
        with open(RESULT_FILE, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
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


def run_inject(text):
    """派生子进程执行注入（后台进程直接调 UI 自动化会被 Windows 限制焦点）。"""
    try:
        p = subprocess.run(
            [sys.executable, INJECT_SCRIPT, text],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        lines = (p.stdout or "").strip().splitlines()
        return p.returncode == 0, lines[-1] if lines else "rc=%d" % p.returncode
    except Exception as exc:
        return False, repr(exc)


def main():
    _force_utf8_stdio()
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM5")
    ap.add_argument("--model", default=MODEL_DIR)
    ap.add_argument("--demo", action="store_true", help="从 stdin 读按键事件，不用串口")
    ap.add_argument("--no-inject", action="store_true", help="转写后不自动填入 Codex 输入框")
    args = ap.parse_args()

    log_result("loading model from %s ..." % args.model)
    model = WhisperModel(args.model, device="cpu", compute_type="int8")
    log_result("model ready. PTT 就绪：按住 BOOT 说话，松开转文字")

    rec = Recorder()
    busy = threading.Event()

    def handle_press():
        # 按下：开始录音（重复按下事件由 Recorder 内部去重）
        try:
            if rec.start():
                log_result(">>> 录音开始（说话吧）")
        except Exception as exc:
            log_error(exc)

    def handle_release():
        try:
            data = rec.stop()
            if data is None:
                return
            dur = len(data) / RATE
            if dur < 0.25:
                # 太短的按压缩是误触，不转写
                log_result(">>> 太短，忽略（%.2fs）" % dur)
                return
            log_result(">>> 录音结束（%.1fs），转写中..." % dur)

            def work():
                # 转写 + 注入放后台线程，不阻塞主循环继续接收按键事件
                try:
                    wav = save_wav(data)
                    text = transcribe(model, wav)
                    result = text or "（没听清）"
                    log_result(">>> 转写结果：" + result)
                    if result != "（没听清）" and not args.no_inject:
                        log_result(">>> 正在填入 Codex 输入框...")
                        ok, detail = run_inject(result)
                        if ok:
                            log_result(">>> 已填入 Codex 输入框，请审阅后发送（%s）" % detail)
                        else:
                            log_result(">>> 注入失败：%s" % detail)
                except Exception as exc:
                    log_error(exc)
                finally:
                    busy.clear()

            busy.set()
            threading.Thread(target=work, daemon=True).start()
        except Exception as exc:
            log_error(exc)

    if args.demo:
        log_result("demo 模式：从 stdin 读 {'ev':'key','i':1,'s':1/0}")
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
        except serial.SerialException:
            log_result("串口断开，重连中...")
            time.sleep(1)
            try:
                s = serial.Serial(args.port, 115200, timeout=0.5)
            except Exception:
                continue
            continue
        except Exception as exc:
            log_error(exc)


if __name__ == "__main__":
    main()
