# -*- coding: utf-8 -*-
"""照片變影片：Ken Burns 緩慢推拉平移，讓靜態照片有呼吸感。

一張照片直接放進影片會像投影片，加上緩慢的推近／平移就有紀錄片的味道。
每張自動輪換運鏡方向，連續幾張不會都往同一邊跑。

單獨用：
  python voicecut/photos.py 輸出資料夾 4.0 照片1.jpg 照片2.png …
被 autocut.py 呼叫時會自動處理混在影片裡的照片。
"""
import subprocess
import sys
from pathlib import Path

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".avif"}
# HEIC 要另外的解碼器，ffmpeg 8 多半不支援 → 給清楚的錯誤訊息而不是默默失敗
UNSUPPORTED = {".heic", ".heif"}

# 六種運鏡，依序輪換
MOVES = ["in_center", "out_center", "pan_left", "pan_right", "in_top", "in_bottom"]


def is_image(p) -> bool:
    return Path(p).suffix.lower() in IMAGE_EXTS


def ken_burns(src: Path, dst: Path, dur: float, w: int, h: int,
              move: str = "in_center", zoom: float = 0.18, fps: int = 30):
    """把一張照片做成 dur 秒的影片片段。

    zoom：整段總共推進多少比例（0.18＝從 1.0 走到 1.18）。
    先放大到目標尺寸的 2 倍再 zoompan，避免像素不足產生鋸齒。
    """
    if Path(src).suffix.lower() in UNSUPPORTED:
        raise ValueError(
            f"{Path(src).name} 是 HEIC 格式，ffmpeg 讀不了。\n"
            f"  請先在「照片」App 或手機設定裡轉成 JPG 再放進來。")
    frames = max(2, int(round(dur * fps)))
    z_end = 1.0 + zoom
    # zoompan 的 z 用 on（frame 編號）線性推進；x/y 依運鏡決定
    if move == "out_center":
        z = f"{z_end}-{zoom}*on/{frames}"
        x, y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif move == "pan_left":
        z = f"1+{zoom * 0.5}*on/{frames}"
        x, y = f"(iw-iw/zoom)*(1-on/{frames})", "ih/2-(ih/zoom/2)"
    elif move == "pan_right":
        z = f"1+{zoom * 0.5}*on/{frames}"
        x, y = f"(iw-iw/zoom)*(on/{frames})", "ih/2-(ih/zoom/2)"
    elif move == "in_top":
        z = f"1+{zoom}*on/{frames}"
        x, y = "iw/2-(iw/zoom/2)", f"(ih-ih/zoom)*0.15"
    elif move == "in_bottom":
        z = f"1+{zoom}*on/{frames}"
        x, y = "iw/2-(iw/zoom/2)", f"(ih-ih/zoom)*0.85"
    else:                                    # in_center
        z = f"1+{zoom}*on/{frames}"
        x, y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"

    vf = (
        # 先鋪滿目標比例（不足處用模糊自身填），再放大 2 倍給 zoompan 用
        f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,"
        f"crop={w * 2}:{h * 2},"
        f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps={fps},"
        f"setsar=1,format=yuv420p"
    )
    cmd = ["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(src),
           "-vf", vf, "-t", f"{dur:.3f}", "-frames:v", str(frames),
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", str(dst)]
    r = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not Path(dst).exists():
        raise RuntimeError(f"照片轉影片失敗：{src.name}\n{(r.stderr or '')[-400:]}")
    return dst


def convert_mixed(paths, tmp: Path, w: int, h: int, photo_dur: float = 4.0,
                  quiet=False):
    """把清單裡的照片換成 Ken Burns 影片，影片原樣保留，順序不變。"""
    out, n = [], 0
    for p in paths:
        p = Path(p)
        if not is_image(p):
            out.append(p)
            continue
        move = MOVES[n % len(MOVES)]
        dst = tmp / f"photo_{n:03d}.mp4"
        ken_burns(p, dst, photo_dur, w, h, move)
        if not quiet:
            print(f"  照片 → 影片：{p.name}（{photo_dur:.1f}s，{move}）", flush=True)
        out.append(dst)
        n += 1
    return out, n


def main():
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    outdir = Path(sys.argv[1])
    outdir.mkdir(parents=True, exist_ok=True)
    dur = float(sys.argv[2])
    srcs = [Path(x) for x in sys.argv[3:]]
    # 直式為預設，跟專案其他工具一致
    made, n = convert_mixed(srcs, outdir, 1080, 1920, dur)
    print(f"\n轉了 {n} 張照片 → {outdir}")
    for m in made:
        print(f"  {m}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
