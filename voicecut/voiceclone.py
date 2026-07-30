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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prosody

os.environ.setdefault("COQUI_TOS_AGREED", "1")   # 免互動同意授權
BASE = Path(__file__).resolve().parent.parent
MODEL = "tts_models/multilingual/multi-dataset/xtts_v2"

TEST_LINE = "這間廠房，白天沒人的時候，其實蠻好看的。"


def split_sentences(text: str):
    """跟 autocut 同一套切句規則，並保留段落資訊（空行＝換段）。
    回傳 [(句子, 後面停多久, 語氣標記)]"""
    GAP_SHORT, GAP_SENT, GAP_PARA = 0.16, 0.34, 0.9
    out = []
    for line in text.replace("\r", "\n").split("\n"):
        line = line.strip()
        if not line:
            if out:
                out[-1] = (out[-1][0], GAP_PARA, out[-1][2])
            continue
        # 句尾的 {拖}{揚}{強} 標記先收起來，等切完句再貼回最後一句
        line, line_tags = prosody.parse_tags(line)
        if not line:
            continue
        first = len(out)
        for s in re.split(r"(?<=[。！？!?；;…])", line):
            s = s.strip()
            if len(re.sub(r"[^\w一-鿿]", "", s)) < 2:
                if out:
                    out[-1] = (out[-1][0] + s, out[-1][1], out[-1][2])
                continue
            out.append((s, GAP_SHORT if s[-1] in "，,、；;：:" else GAP_SENT, set()))
        if line_tags and len(out) > first:
            out[-1] = (out[-1][0], out[-1][1], line_tags)
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


def read_wav_mono(p: Path):
    import numpy as np
    with wave.open(str(p)) as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    dt = {1: np.int8, 2: np.int16, 4: np.int32}[sw]
    a = np.frombuffer(raw, dtype=dt).astype(np.float32) / float(2 ** (8 * sw - 1))
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    return a, sr


def resample_linear(a, src_sr, dst_sr):
    import numpy as np
    if src_sr == dst_sr:
        return a
    n = int(round(len(a) * dst_sr / src_sr))
    return np.interp(np.linspace(0, len(a) - 1, n),
                     np.arange(len(a)), a).astype(np.float32)


def gap_filler(gap, sr, breaths, idx, np):
    """一段停頓：夠長就塞一段呼吸聲（置中），不夠長就純靜音。"""
    n = int(sr * gap)
    seg = np.zeros(n, dtype=np.float32)
    if not breaths or n <= 0:
        return seg
    b = breaths[idx % len(breaths)]
    if len(b) + int(sr * 0.06) > n:      # 停頓塞不下就算了，別擠掉語音
        return seg
    start = (n - len(b)) // 2
    seg[start:start + len(b)] += b
    return seg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True, help="你的聲音樣本（wav，6~20 秒最好）")
    ap.add_argument("--script", help="要念的稿子 .txt")
    ap.add_argument("--out", help="輸出 wav")
    ap.add_argument("--test", action="store_true", help="只念一句試速度與像不像")
    ap.add_argument("--lang", default="zh-cn")
    # ---- 情感／語調相關 ----
    ap.add_argument("--temperature", type=float, default=0.75,
                    help="語調變化幅度。調高更有起伏但也更容易失控（0.6~1.0）")
    ap.add_argument("--top-p", type=float, default=0.85,
                    help="取樣範圍，調高語氣更多樣（0.8~0.95）")
    ap.add_argument("--rep-penalty", type=float, default=10.0,
                    help="重複懲罰，調低語氣較放鬆（5~10）")
    ap.add_argument("--speed", type=float, default=1.0, help="語速倍率")
    ap.add_argument("--breath", metavar="資料夾",
                    help="句子之間插入真實呼吸聲（用 breath.py 切出來的資料夾）")
    ap.add_argument("--breath-vol", type=float, default=0.55,
                    help="呼吸聲音量倍率，預設 0.55")
    ap.add_argument("--seed", type=int, help="固定隨機種子，方便重現同一次結果")
    ap.add_argument("--verify", action="store_true",
                    help="每句合成完用 Whisper 驗念字，錯的句子自動重試（最多3次）")
    ap.add_argument("--verify-python",
                    default=r"C:\Users\user\AppData\Local\Programs\Python\Python312\python.exe",
                    help="主環境的 python（有 faster-whisper 的那個）")
    ap.add_argument("--verify-pass", type=float, default=0.92,
                    help="單句通過門檻（正規化後的吻合度）")
    args = ap.parse_args()

    ref = Path(args.ref).resolve()
    if not ref.exists():
        sys.exit(f"找不到參考音檔：{ref}")

    print("載入 XTTS-v2 模型…（第一次會下載約 1.8GB）", flush=True)
    t0 = time.time()
    import numpy as np
    import torch
    from TTS.api import TTS
    if args.seed is not None:
        torch.manual_seed(args.seed)
        np.random.seed(args.seed)
    tts = TTS(MODEL, progress_bar=True).to("cpu")
    print(f"模型就緒（{time.time() - t0:.0f} 秒）\n", flush=True)

    sr = tts.synthesizer.output_sample_rate
    # 語調參數一起帶進 XTTS
    tune = dict(temperature=args.temperature, top_p=args.top_p,
                repetition_penalty=args.rep_penalty, speed=args.speed)
    if (args.temperature, args.top_p, args.rep_penalty, args.speed) != (0.75, 0.85, 10.0, 1.0):
        print(f"語調設定：{tune}\n")

    def say(text):
        return np.asarray(tts.tts(text=text, speaker_wav=str(ref),
                                  language=args.lang, **tune))

    # 常駐驗證員：載一次 Whisper，之後每句幾秒內回覆念字對不對
    verifier = None
    if args.verify:
        import subprocess as sp
        vp = Path(__file__).resolve().parent / "verifier.py"
        print("啟動念字驗證員…（載入 Whisper，約 1 分鐘）", flush=True)
        verifier = sp.Popen([args.verify_python, str(vp)],
                            stdin=sp.PIPE, stdout=sp.PIPE,
                            encoding="utf-8", errors="replace", bufsize=1)
        line = verifier.stdout.readline().strip()
        while line and line != "READY":
            line = verifier.stdout.readline().strip()
        print("驗證員就緒\n", flush=True)

    def verified_say(text, tmpdir, idx):
        """合成＋驗證；沒過就換種子重試，回傳最好的一次。"""
        best, best_ratio = None, -1.0
        for attempt in range(3):
            w = say(text)
            if verifier is None:
                return w, 1.0, ""
            import torch
            probe = Path(tmpdir) / f"_v_{idx}_{attempt}.wav"
            write_wav(probe, sr, [w])
            verifier.stdin.write(f"{probe}\t{text}\n")
            ratio_s, _, heard = verifier.stdout.readline().partition("\t")
            try:
                ratio = float(ratio_s)
            except ValueError:
                ratio = 0.0
            probe.unlink(missing_ok=True)
            if ratio > best_ratio:
                best, best_ratio, best_heard = w, ratio, heard.strip()
            if ratio >= args.verify_pass:
                break
            print(f"      念字沒過（{ratio:.2f}：{heard.strip()[:24]}），重試…",
                  flush=True)
            torch.manual_seed((args.seed or 0) + idx * 100 + attempt + 1)
        return best, best_ratio, best_heard

    # 呼吸聲：載進來，插在句子之間讓停頓不那麼「乾」
    breaths = []
    if args.breath:
        bd = Path(args.breath).expanduser().resolve()
        for f in sorted(bd.glob("breath_*.wav")):
            b, bsr = read_wav_mono(f)
            if bsr != sr:
                b = resample_linear(b, bsr, sr)
            breaths.append(b.astype(np.float32) * args.breath_vol)
        print(f"呼吸聲：載入 {len(breaths)} 段（{bd.name}）\n")
    if args.test:
        print(f"試念：{TEST_LINE}")
        t0 = time.time()
        wav = say(TEST_LINE)
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
    out_dir = Path(args.out).resolve().parent
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, (sent, gap, tags) in enumerate(pairs, 1):
        w, ratio, _heard = verified_say(sent, out_dir, i)
        if tags:
            w = prosody.apply_tags(w, sr, tags)
        d = len(w) / sr
        marks.append((cursor, cursor + d, sent))
        chunks.append(w)
        chunks.append(gap_filler(gap, sr, breaths, i - 1, np))
        cursor += d + gap
        tag_txt = "".join(f"{{{t}}}" for t in sorted(tags)) if tags else ""
        ok = "" if verifier is None else f"  念字{ratio * 100:.0f}%"
        print(f"  [{i}/{len(pairs)}] {d:5.1f}s  {sent[:24]}{tag_txt}{ok}"
              f"   （已跑 {time.time() - t0:.0f}s）", flush=True)
    if verifier is not None:
        try:
            verifier.stdin.write("QUIT\n")
            verifier.wait(timeout=10)
        except Exception:
            verifier.kill()

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
