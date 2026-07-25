# -*- coding: utf-8 -*-
"""句尾語氣加工：拖尾音、尾音上揚、加強音量。

TTS 念出來的句子語調是模型決定的，光靠參數不好指定「這句要拖長」「這句要往上揚」。
這支直接對波形動手，只處理句子的尾巴，前面的內容不動。

稿子裡在句子後面加標記就會套用：
    有時候就這樣看看，也不錯啊。{拖}
    居然還有幾戶沒睡欸！{揚}{強}

  {拖}  尾音拉長（音高不變，像講話尾巴慢慢放掉）
  {揚}  尾音音高往上爬（疑問、驚訝的感覺）
  {降}  尾音音高往下掉（感嘆、放鬆的感覺）
  {強}  整句音量提高（強調）
  {弱}  整句音量降低（喃喃自語的感覺）
可以疊加，例如 {揚}{強}。
"""
import re
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np

TAG_RE = re.compile(r"\{(拖|揚|降|強|弱)\}")


def parse_tags(line: str):
    """把句尾標記拆出來，回傳 (乾淨句子, 標記集合)。"""
    tags = set(TAG_RE.findall(line))
    return TAG_RE.sub("", line).strip(), tags


def _write(p: Path, a: np.ndarray, sr: int):
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(a, -1, 1) * 32767).astype(np.int16).tobytes())


def _read(p: Path):
    with wave.open(str(p)) as w:
        sr = w.getframerate()
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return a.astype(np.float32) / 32768.0, sr


def _ffmpeg(a: np.ndarray, sr: int, af: str) -> np.ndarray:
    """把一段波形丟給 ffmpeg 套濾鏡再讀回來。"""
    with tempfile.TemporaryDirectory() as td:
        i, o = Path(td) / "i.wav", Path(td) / "o.wav"
        _write(i, a, sr)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(i),
                        "-af", af, "-c:a", "pcm_s16le", str(o)],
                       check=True, capture_output=True)
        b, _ = _read(o)
    return b


def find_tail(a: np.ndarray, sr: int, max_tail=0.55):
    """找句子最後一段有聲音的部分，回傳起點索引。
    先去掉結尾的靜音，再往前抓 max_tail 秒。"""
    win = max(1, int(sr * 0.01))
    n = len(a) // win
    rms = np.array([np.sqrt((a[i * win:(i + 1) * win] ** 2).mean() + 1e-12)
                    for i in range(n)])
    if not len(rms):
        return 0, len(a)
    thr = max(rms.max() * 0.06, 1e-4)
    voiced = np.where(rms > thr)[0]
    if not len(voiced):
        return 0, len(a)
    end = min(len(a), (voiced[-1] + 1) * win)
    start = max(0, end - int(sr * max_tail))
    return start, end


def _pitch_ramp(seg: np.ndarray, sr: int, total_ratio: float, slices=5):
    """把一段音高逐步推上去（或拉下來），長度維持不變。"""
    if len(seg) < sr * 0.06:
        return seg
    out, n = [], len(seg)
    bounds = np.linspace(0, n, slices + 1).astype(int)
    for k in range(slices):
        part = seg[bounds[k]:bounds[k + 1]]
        if len(part) < 64:
            out.append(part)
            continue
        # 從 1.0 線性走到 total_ratio
        r = 1.0 + (total_ratio - 1.0) * (k + 1) / slices
        r = float(np.clip(r, 0.6, 1.6))
        af = f"asetrate={int(sr * r)},aresample={sr},atempo={1 / r:.6f}"
        try:
            out.append(_ffmpeg(part, sr, af))
        except subprocess.CalledProcessError:
            out.append(part)
    return np.concatenate(out) if out else seg


def apply_tags(a: np.ndarray, sr: int, tags: set) -> np.ndarray:
    """依標記加工一句話的波形。"""
    if not tags:
        return a
    a = a.astype(np.float32).copy()

    if "強" in tags or "弱" in tags:
        g = 1.5 if "強" in tags else 0.62
        a = np.clip(a * g, -0.99, 0.99)

    if not ({"拖", "揚", "降"} & tags):
        return a

    s, e = find_tail(a, sr)
    head, tail, rest = a[:s], a[s:e], a[e:]
    if len(tail) < sr * 0.05:
        return a

    if "拖" in tags:                      # 尾音拉長 1.55 倍（音高不變）
        try:
            tail = _ffmpeg(tail, sr, "atempo=0.645")
        except subprocess.CalledProcessError:
            pass
    if "揚" in tags:                      # 尾音往上爬 4 個半音左右
        tail = _pitch_ramp(tail, sr, 1.26)
    elif "降" in tags:                    # 尾音往下掉
        tail = _pitch_ramp(tail, sr, 0.84)

    # 尾巴收尾淡出一點，避免拼接處有稜角
    fade = min(int(sr * 0.05), len(tail) // 4)
    if fade > 1:
        tail = tail.copy()
        tail[-fade:] *= np.linspace(1, 0.75, fade)
    return np.concatenate([head, tail, rest])
