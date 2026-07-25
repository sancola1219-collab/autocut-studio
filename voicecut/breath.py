# -*- coding: utf-8 -*-
"""從一段錄音裡找出真實的呼吸／換氣聲，切出來存成素材。

合成的旁白句子之間是純靜音，聽起來很「乾」。把說話者自己的呼吸聲插進停頓裡，
口氣音就是真的，不是模型猜的。

用法：python voicecut/breath.py <錄音檔> [輸出資料夾]
"""
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

BASE = Path(__file__).resolve().parent.parent
BREATH_LO, BREATH_HI = -50.0, -28.0   # 呼吸聲大致的音量範圍（dB）
MIN_LEN, MAX_LEN = 0.13, 0.60         # 太短是雜訊、太長是說話


def to_wav(src: Path, dst: Path, sr=48000):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(src),
         "-af", "highpass=f=70", "-ar", str(sr), "-ac", "1",
         "-c:a", "pcm_s16le", str(dst)],
        check=True, capture_output=True)


def read_wav(p: Path):
    with wave.open(str(p)) as w:
        sr = w.getframerate()
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return a.astype(np.float32) / 32768.0, sr


def write_wav(p: Path, a: np.ndarray, sr: int):
    a = np.clip(a, -1, 1)
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((a * 32767).astype(np.int16).tobytes())


def frame_db(a: np.ndarray, sr: int, hop=0.02):
    win = int(sr * hop)
    n = (len(a) - win) // win
    rms = np.array([np.sqrt((a[i * win:(i + 1) * win] ** 2).mean() + 1e-12)
                    for i in range(max(n, 0))])
    return 20 * np.log10(rms + 1e-12), win


def find_breaths(a: np.ndarray, sr: int):
    db, win = frame_db(a, sr)
    if not len(db):
        return []
    mask = (db > BREATH_LO) & (db < BREATH_HI)
    runs, start = [], None
    for i, m in enumerate(list(mask) + [False]):
        if m and start is None:
            start = i
        elif not m and start is not None:
            dur = (i - start) * 0.02
            if MIN_LEN <= dur <= MAX_LEN:
                runs.append((start * 0.02, i * 0.02))
            start = None
    return runs


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    src = Path(sys.argv[1]).expanduser().resolve()
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else BASE / "素材/我的聲音/_呼吸聲"
    out.mkdir(parents=True, exist_ok=True)
    tmp = out / "_raw.wav"
    to_wav(src, tmp)
    a, sr = read_wav(tmp)
    db, _ = frame_db(a, sr)
    print(f"錄音 {len(a)/sr:.2f} 秒｜音量 最大 {db.max():.1f} / "
          f"中位 {np.median(db):.1f} / 最小 {db.min():.1f} dB")

    runs = find_breaths(a, sr)
    if not runs:
        print("找不到明顯的呼吸聲（可能整段都在講話，或底噪太高）")
        tmp.unlink(missing_ok=True)
        return
    print(f"\n找到 {len(runs)} 段候選：")
    kept = []
    for i, (s, e) in enumerate(runs, 1):
        seg = a[int(s * sr):int(e * sr)]
        peak = 20 * np.log10(np.abs(seg).max() + 1e-12)
        # 呼吸聲高頻多、波形平緩；用零交越率粗略排除喀噠雜音
        zcr = float((np.diff(np.sign(seg)) != 0).mean())
        ok = 0.02 < zcr < 0.40
        print(f"  {i:2d}. {s:5.2f}–{e:5.2f}s ({e-s:.2f}s) "
              f"峰值 {peak:6.1f}dB  zcr {zcr:.3f}  {'採用' if ok else '略過'}")
        if ok:
            f = out / f"breath_{len(kept)+1:02d}.wav"
            # 頭尾各加短淡入淡出，插進旁白時不會有爆音
            fade = min(int(sr * 0.03), len(seg) // 3)
            env = np.ones(len(seg), dtype=np.float32)
            if fade > 1:
                env[:fade] = np.linspace(0, 1, fade)
                env[-fade:] = np.linspace(1, 0, fade)
            write_wav(f, seg * env, sr)
            kept.append(f)
    tmp.unlink(missing_ok=True)
    print(f"\n存了 {len(kept)} 個呼吸聲到 {out}")
    for f in kept:
        print(f"  {f.name}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
