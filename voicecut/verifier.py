# -*- coding: utf-8 -*-
"""常駐的念字驗證員：載入一次 Whisper，之後每句話幾秒內回覆。

供 voiceclone.py 當子程序使用（跨環境：本體在主環境跑，有 faster-whisper）。
協定：stdin 每行「wav路徑\t預期文字」→ stdout 回一行「ratio\t聽到的字」。
"""
import difflib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from checkspeech import normalize          # 沿用同一套正規化（數字/語助詞）

from faster_whisper import WhisperModel

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

model = WhisperModel("medium", device="cpu", compute_type="int8")
print("READY", flush=True)

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    if line == "QUIT":
        break
    try:
        wav, expect = line.split("\t", 1)
        segs, _ = model.transcribe(wav, language="zh", beam_size=5,
                                   condition_on_previous_text=False,
                                   initial_prompt="以下是繁體中文的內容。")
        heard = "".join(s.text for s in segs).strip()
        ratio = difflib.SequenceMatcher(
            None, normalize(expect), normalize(heard)).ratio()
        print(f"{ratio:.3f}\t{heard}", flush=True)
    except Exception as e:
        print(f"0.0\tERROR:{e}", flush=True)
