# -*- coding: utf-8 -*-
"""把素材調成剛好配合旁白段落的長度，場景切換自動落在旁白的換氣停頓裡。

用法：python voicecut/_fit.py 字幕.srt 工作資料夾 [--narr 旁白.wav]
                             [--groups 1,1,2] 影片1 影片2 …

--narr 給旁白音檔的話，總長用它的實際長度（最後一句念完後還有留白時比較準）。

段落邊界自動從字幕找（超過 0.6 秒的空隙＝換氣點）。段落數比影片數多時，
預設讓最後一支影片吃掉剩下的段落（結尾的「晚安」通常跟著最後一個場景）。
--groups 可以自己指定每支影片吃幾段，例如 1,1,2。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import autocut
from autocut import run, probe_duration

PARA_GAP = 0.6          # 超過這個秒數的空隙算換段落
SLOW_MIN, FAST_MAX = 0.6, 1.15   # 放慢不要低於 0.6x（會頓）、盡量不要加速

argv = sys.argv[1:]
groups, narr_wav = None, None
if "--groups" in argv:
    i = argv.index("--groups")
    groups = [int(x) for x in argv[i + 1].split(",")]
    del argv[i:i + 2]
if "--narr" in argv:
    i = argv.index("--narr")
    narr_wav = Path(argv[i + 1])
    del argv[i:i + 2]
srt, workdir, *vids = argv
srt, workdir = Path(srt), Path(workdir)
vids = [Path(v) for v in vids]
workdir.mkdir(parents=True, exist_ok=True)

segs = autocut.parse_srt(srt)
# 有旁白檔就用它的實際長度：最後一句念完常還有留白，只看字幕會短一截
narr_end = segs[-1][1]
if narr_wav and narr_wav.exists():
    real = probe_duration(narr_wav)
    if real > narr_end:
        print(f"（旁白實際 {real:.2f}s，比最後一句字幕晚 {real - narr_end:.2f}s）")
        narr_end = real

# 找出所有換段落的位置
para_ends = [i for i in range(len(segs) - 1)
             if segs[i + 1][0] - segs[i][1] > PARA_GAP]
n_para = len(para_ends) + 1
if groups is None:                      # 預設：前面各吃一段，最後一支吃剩下的
    groups = [1] * (len(vids) - 1) + [n_para - (len(vids) - 1)]
if not vids:
    sys.exit("至少要給一支影片")
if len(vids) > n_para:
    sys.exit(f"旁白只偵測到 {n_para} 個段落（換氣點 {len(para_ends)} 個），"
             f"最多只能配 {n_para} 支影片，你給了 {len(vids)} 支。\n"
             f"請減少影片，或在稿子裡多空幾行讓旁白多幾個換氣點。")
if len(groups) != len(vids):
    sys.exit(f"--groups 的個數要等於影片數 {len(vids)}，現在是 {len(groups)} 個")
if any(g < 1 for g in groups):
    sys.exit(f"--groups 每一項至少要 1（每支影片至少吃一段），現在是 {groups}")
if sum(groups) != n_para:
    sys.exit(f"--groups 加起來要等於段落數 {n_para}，現在加起來是 {sum(groups)}")

print(f"旁白 {narr_end:.2f}s，偵測到 {n_para} 個段落，分配 {groups}")
switches, k = [], 0
for g in groups[:-1]:
    k += g
    i = para_ends[k - 1]
    switches.append((segs[i][1] + segs[i + 1][0]) / 2)   # 切在空隙中間
bounds = [0.0] + switches + [narr_end + 0.7]
assert all(b > a for a, b in zip(bounds, bounds[1:])), f"切換點沒有遞增：{bounds}"
print("切換點：" + "、".join(f"{s:.2f}s" for s in switches))

outs, warn = [], []
for i, (src, a, b) in enumerate(zip(vids, bounds, bounds[1:])):
    have = autocut.probe_vstream_duration(src)
    want = b - a
    need = have / want                  # <1 要放慢、>1 要加速
    factor = min(max(need, SLOW_MIN), FAST_MAX)
    slow_to = have / factor
    freeze = max(0.0, want - slow_to)   # 放慢到上限還不夠長 → 凍結補
    trim = max(0.0, slow_to - want)     # 太長 → 直接切掉尾巴
    if need < SLOW_MIN:
        warn.append(f"{src.name} 太短（需 {need:.2f}x），已放慢到 {SLOW_MIN}x"
                    f"＋凍結 {freeze:.1f}s 收尾")
    if need > FAST_MAX:
        warn.append(f"{src.name} 太長（需 {need:.2f}x），加速到 {FAST_MAX}x"
                    f"後多的 {trim:.1f}s 直接切掉")
    vf = [f"setpts={1 / factor:.6f}*PTS", "fps=30", "setsar=1"]
    if freeze > 0.01:
        vf.append(f"tpad=stop_mode=clone:stop_duration={freeze:.3f}")
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", src, "-an",
           "-vf", ",".join(vf)]
    if trim > 0.01:
        cmd += ["-t", f"{want:.3f}"]
    out = autocut.remove_stale(workdir / f"{i}_{src.stem.split('_')[-1]}.mp4")
    run(cmd + ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
               "-pix_fmt", "yuv420p", out])
    print(f"  {src.name[:24]:26s} {have:5.2f}s → {probe_duration(out):5.2f}s"
          f"  {factor:.3f}x" + (f"＋凍結{freeze:.1f}s" if freeze > 0.01 else "")
          + (f"（切掉{trim:.1f}s）" if trim > 0.01 else "")
          + f"  旁白 {a:.1f}–{b:.1f}s")
    outs.append(out)

if warn:
    print("\n注意：")
    for w in warn:
        print(f"  ⚠ {w}")
print(f"\n合計 {sum(probe_duration(o) for o in outs):.2f}s / 目標 {bounds[-1]:.2f}s")
print(" ".join(f'"{o}"' for o in outs))
