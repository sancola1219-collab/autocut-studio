# -*- coding: utf-8 -*-
"""檢查合成出來的旁白有沒有念錯字。

高 temperature 的 TTS 偶爾會念錯（多音字選錯、吃字）。這支用 Whisper 把旁白
聽打回來跟原稿比對，把不一樣的地方列出來，免得錯的東西直接進成品。

用法：python voicecut/checkspeech.py <旁白.wav> <原稿.txt> [--model medium]
"""
import argparse
import difflib
import re
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
import autocut

# Whisper 會把國字數字寫成阿拉伯數字，這不算念錯
D = {"零": 0, "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4,
     "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _cn2num(m):
    """十一→11、二十→20、二十三→23、十→10、五→5。"""
    t = m.group()
    if "十" not in t:
        return "".join(str(D[c]) for c in t)
    a, _, b = t.partition("十")
    return str((D[a] if a else 1) * 10 + (D[b] if b else 0))


def normalize(s: str) -> str:
    s = re.sub(r"[^\w一-鿿]", "", s)
    # 先處理「十」的組合，再處理單獨的數字，否則十一會變成 101
    s = re.sub(r"[一二兩三四五六七八九]?十[一二兩三四五六七八九]?", _cn2num, s)
    s = re.sub(r"[零一二兩三四五六七八九]", _cn2num, s)
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("script")
    ap.add_argument("--model", default="medium")
    ap.add_argument("--min-ratio", type=float, default=0.97,
                    help="低於這個吻合度就視為有問題（回傳碼 1）")
    a = ap.parse_args()

    wav, script = Path(a.wav).resolve(), Path(a.script).resolve()
    for p in (wav, script):
        if not p.exists():
            sys.exit(f"找不到：{p}")

    with tempfile.TemporaryDirectory() as td:
        segs = autocut.transcribe(wav, a.model, Path(td))
    heard_raw = "".join(s[2] for s in segs)
    want_raw = autocut.read_text_smart(script)
    heard, want = normalize(heard_raw), normalize(want_raw)

    sm = difflib.SequenceMatcher(None, want, heard)
    ratio = sm.ratio()
    print(f"\n原稿：{want_raw.strip()[:120]}")
    print(f"聽到：{heard_raw.strip()[:120]}")
    print(f"\n吻合度 {ratio * 100:.1f}%")

    bad = [(t, want[i1:i2], heard[j1:j2])
           for t, i1, i2, j1, j2 in sm.get_opcodes() if t != "equal"]
    if not bad:
        print("沒有念錯的地方。")
        return 0
    print("差異：")
    for t, x, y in bad:
        名 = {"replace": "念錯", "delete": "漏念", "insert": "多念"}[t]
        print(f"  {名}：稿「{x}」→ 唸「{y}」")
    if ratio < a.min_ratio:
        print(f"\n⚠ 吻合度低於 {a.min_ratio * 100:.0f}%，建議換個說法或換 --seed 重跑。")
        return 1
    print("\n（差異很小，多半是聽打的寫法差異，可接受）")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
