# -*- coding: utf-8 -*-
"""用自己的聲音樣本複製音色來念稿（XTTS-v2，零訓練）。

必須用獨立環境跑，不要用主環境（會弄壞 faster-whisper）：
  .venv_voiceclone\\Scripts\\python.exe voicecut\\voiceclone.py --help

  # 先試一句，確認速度與像不像
  … voiceclone.py --ref 素材/我的聲音/_參考音色/ref_全段.wav --test

  # 整篇稿子
  … voiceclone.py --ref <參考音檔> --script <稿子.txt> --out <輸出.wav>

注意：XTTS-v2 授權為 CPML，禁止商業用途。要商用請改用 OpenVoice v2 / CosyVoice2。
"""
import argparse
import os
import re
import sys
import time
import wave
from pathlib import Path

os.environ.setdefault("COQUI_TOS_AGREED", "1")   # 免互動同意授權
BASE = Path(__file__).resolve().parent.parent
MODEL = "tts_models/multilingual/multi-dataset/xtts_v2"

TEST_LINE = "這間廠房，白天沒人的時候，其實蠻好看的。"


def split_sentences(text: str):
    """跟 autocut 同一套切句規則，並保留段落資訊（空行＝換段）。
    回傳 [(句子, 後面停多久)]"""
    GAP_SHORT, GAP_SENT, GAP_PARA = 0.16, 0.34, 0.9
    out = []
    for line in text.replace("\r", "\n").split("\n"):
        line = line.strip()
        if not line:
            if out:
                out[-1] = (out[-1][0], GAP_PARA)
            continue
        for s in re.split(r"(?<=[。！？!?；;…])", line):
            s = s.strip()
            if len(re.sub(r"[^\w一-鿿]", "", s)) < 2:
                if out:
                    out[-1] = (out[-1][0] + s, out[-1][1])
                continue
            out.append((s, GAP_SHORT if s[-1] in "，,、；;：:" else GAP_SENT))
    return out


def write_wav(path: Path, sr: int, chunks):
    """把多段 float 波形＋停頓寫成一個 16-bit wav。"""
    import numpy as np
    data = np.concatenate(chunks).astype(np.float32)
    peak = float(np.max(np.abs(data))) or 1.0
    pcm = (data / peak * 0.89 * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True, help="你的聲音樣本（wav，6~20 秒最好）")
    ap.add_argument("--script", help="要念的稿子 .txt")
    ap.add_argument("--out", help="輸出 wav")
    ap.add_argument("--test", action="store_true", help="只念一句試速度與像不像")
    ap.add_argument("--lang", default="zh-cn")
    args = ap.parse_args()

    ref = Path(args.ref).resolve()
    if not ref.exists():
        sys.exit(f"找不到參考音檔：{ref}")

    print("載入 XTTS-v2 模型…（第一次會下載約 1.8GB）", flush=True)
    t0 = time.time()
    import numpy as np
    from TTS.api import TTS
    tts = TTS(MODEL, progress_bar=True).to("cpu")
    print(f"模型就緒（{time.time() - t0:.0f} 秒）\n", flush=True)

    sr = tts.synthesizer.output_sample_rate
    if args.test:
        print(f"試念：{TEST_LINE}")
        t0 = time.time()
        wav = tts.tts(text=TEST_LINE, speaker_wav=str(ref), language=args.lang)
        dt = time.time() - t0
        out = BASE / "輸出/測試片/_音色複製試聽.wav"
        out.parent.mkdir(parents=True, exist_ok=True)
        write_wav(out, sr, [np.asarray(wav)])
        secs = len(wav) / sr
        print(f"\n完成：{out}")
        print(f"  聲音長 {secs:.1f} 秒，花了 {dt:.0f} 秒（{dt / secs:.1f}x 實時）")
        print(f"  整篇 10 句約需 {dt / secs * 50 / 60:.0f} 分鐘")
        return

    if not (args.script and args.out):
        sys.exit("整篇合成要給 --script 與 --out")
    pairs = split_sentences(Path(args.script).read_text(encoding="utf-8"))
    print(f"共 {len(pairs)} 句\n")
    chunks, marks, cursor = [], [], 0.0
    t0 = time.time()
    for i, (sent, gap) in enumerate(pairs, 1):
        w = np.asarray(tts.tts(text=sent, speaker_wav=str(ref), language=args.lang))
        d = len(w) / sr
        marks.append((cursor, cursor + d, sent))
        chunks.append(w)
        chunks.append(np.zeros(int(sr * gap), dtype=np.float32))
        cursor += d + gap
        print(f"  [{i}/{len(pairs)}] {d:5.1f}s  {sent[:26]}"
              f"   （已跑 {time.time() - t0:.0f}s）", flush=True)

    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    write_wav(out, sr, chunks)
    srt = out.with_name(out.stem.replace("旁白", "字幕") + ".srt")

    def ts(x):
        h, r = divmod(x, 3600)
        m, s = divmod(r, 60)
        return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round(s % 1 * 1000)):03d}"

    with open(srt, "w", encoding="utf-8") as f:
        for i, (a, b, tx) in enumerate(marks, 1):
            f.write(f"{i}\n{ts(a)} --> {ts(b)}\n{tx}\n\n")
    print(f"\n旁白 {cursor:.2f} 秒 → {out.name}")
    print(f"字幕 {len(marks)} 句 → {srt.name}")
    print(f"總共花了 {(time.time() - t0) / 60:.1f} 分鐘")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
