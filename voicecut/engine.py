# -*- coding: utf-8 -*-
"""口說剪片引擎：把一串 op 套到影片上。
支援 cut（剪掉）/ speed（變速）/ mute（靜音）/ volume（音量）/ subtitle（字幕）。
剪掉與變速會讓時間軸位移，字幕時間點會自動重新對齊到輸出時間軸。
"""
import json
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
import autocut  # 重用 build_ass / probe / run 等（同一套風格）


def _run(cmd, cwd=None):
    r = subprocess.run([str(c) for c in cmd], cwd=cwd,
                       capture_output=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg 失敗：{cmd[0]}\n{(r.stderr or '')[-1500:]}")
    return r


def _merge_ranges(ranges):
    """合併重疊的 (start,end) 區間。"""
    ranges = sorted([(a, b) for a, b in ranges if b > a])
    out = []
    for a, b in ranges:
        if out and a <= out[-1][1] + 1e-6:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def build_plan(ops, duration):
    """把 ops 轉成一串輸出用的子片段。
    回傳 (segments, subtitles_out)：
      segments = [{o_start,o_end,speed,mute,gain}]  依原始時間軸排序、已扣掉剪掉段
      subtitles_out = [(out_start,out_end,text)]     時間已對齊到輸出軸
    """
    cuts = _merge_ranges([(o["start"], o["end"]) for o in ops
                          if o["type"] == "cut" and o["start"] is not None])

    # 邊界點：所有 speed/mute/volume 的起訖，讓每個子片段內屬性一致
    bounds = {0.0, duration}
    for o in ops:
        if o["type"] in ("speed", "mute", "volume") and o["start"] is not None:
            bounds.add(max(0.0, o["start"]))
            bounds.add(min(duration, o["end"]))
    for a, b in cuts:
        bounds.add(a)
        bounds.add(b)
    bounds = sorted(x for x in bounds if 0.0 <= x <= duration)

    def in_cut(t0, t1):
        mid = (t0 + t1) / 2
        return any(a <= mid < b for a, b in cuts)

    def attr(kind, t0, t1):
        mid = (t0 + t1) / 2
        val = None
        for o in ops:
            if o["type"] == kind and o["start"] is not None and o["start"] <= mid < o["end"]:
                val = o
        return val

    segments = []
    for t0, t1 in zip(bounds, bounds[1:]):
        if t1 - t0 < 1e-3 or in_cut(t0, t1):
            continue
        sp = attr("speed", t0, t1)
        mu = attr("mute", t0, t1)
        vo = attr("volume", t0, t1)
        segments.append({
            "o_start": t0, "o_end": t1,
            "speed": (sp["factor"] if sp and sp["factor"] else 1.0),
            "mute": bool(mu),
            "gain": (vo["gain"] if vo and vo["gain"] else 1.0),
        })

    # 建立 原始時間 → 輸出時間 的對應
    def orig_to_out(t):
        cur = 0.0
        for s in segments:
            if t < s["o_start"]:
                return cur   # 落在剪掉段，貼到下一段開頭
            if s["o_start"] <= t <= s["o_end"]:
                return cur + (t - s["o_start"]) / s["speed"]
            cur += (s["o_end"] - s["o_start"]) / s["speed"]
        return cur

    subs = []
    for o in ops:
        if o["type"] == "subtitle" and o["text"]:
            os_, oe_ = orig_to_out(o["start"]), orig_to_out(min(o["end"], duration))
            if oe_ <= os_ + 1e-6:
                continue            # 整段落在剪掉段，內容已不存在 → 不要留鬼影字幕
            if oe_ - os_ < 0.4:
                oe_ = os_ + 2.0     # 有留下但太短（例如落在加速段）→ 補到看得清
            subs.append((os_, oe_, o["text"]))
    subs.sort()
    return segments, subs


def apply_ops(video: Path, ops, out_path: Path, tmp: Path,
              burn_subs=True, progress=None):
    """套用 ops，輸出成品。progress：可選 callback(str)。"""
    def say(m):
        if progress:
            progress(m)

    # 燒字幕那步以 cwd=tmp 執行，輸入一律先轉絕對路徑才找得到檔
    video = Path(video).expanduser().resolve()
    w, h, duration, has_aud = autocut.probe_video(video)
    w, h = w - w % 2, h - h % 2
    segments, subs = build_plan(ops, duration)
    if not segments:
        raise RuntimeError("所有片段都被剪掉了，沒東西可輸出。")

    # 沒有任何剪掉／變速／音量 → 只需（可選）燒字幕，走快速路徑
    trivial = all(abs(s["speed"] - 1.0) < 1e-6 and not s["mute"]
                  and abs(s["gain"] - 1.0) < 1e-6 for s in segments) \
        and len(segments) == 1 and segments[0]["o_start"] < 0.05 \
        and segments[0]["o_end"] > duration - 0.05

    clips = []
    if trivial:
        base = video
    else:
        for i, s in enumerate(segments):
            clip = tmp / f"clip_{i:03d}.mp4"
            dur = s["o_end"] - s["o_start"]
            say(f"處理片段 {i + 1}/{len(segments)}（{dur:.1f} 秒）")
            vf = [f"scale={w}:{h}:force_original_aspect_ratio=decrease",
                  f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2", "fps=30", "setsar=1"]
            af = [autocut.AF]
            if abs(s["speed"] - 1.0) > 1e-6:
                vf.append(f"setpts={1.0 / s['speed']:.6f}*PTS")
                af.append(_atempo_chain(s["speed"]))
            if s["mute"]:
                af.append("volume=0")
            elif abs(s["gain"] - 1.0) > 1e-6:
                af.append(f"volume={s['gain']}")
            cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                   "-ss", f"{s['o_start']:.3f}", "-to", f"{s['o_end']:.3f}",
                   "-i", str(video)]
            if not has_aud:
                cmd += ["-f", "lavfi", "-t", f"{dur:.3f}",
                        "-i", "anullsrc=r=48000:cl=stereo"]
                amap = "1:a"
            else:
                amap = "0:a"
            cmd += ["-map", "0:v:0", "-map", amap,
                    "-vf", ",".join(vf), "-af", ",".join(af),
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                    "-ar", "48000", str(clip)]
            _run(cmd)
            clips.append(clip)

        say("串接片段…")
        lst = tmp / "concat.txt"
        lst.write_text("\n".join(f"file '{c.name}'" for c in clips), encoding="utf-8")
        base = tmp / "joined.mp4"
        _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst,
              "-c", "copy", str(base)], cwd=tmp)

    # 舊成品可能被縮圖程式／播放器抓著，重試後仍鎖住就自動換檔名
    out_path = autocut.remove_stale(out_path)

    # 燒字幕
    if burn_subs and subs:
        say("燒錄字幕…")
        ass = tmp / "subs.ass"
        bw, bh, _, _ = autocut.probe_video(base)
        autocut.build_ass(subs, bw - bw % 2, bh - bh % 2, False, ass)
        _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
              "-i", str(base),
              "-vf", f"crop=trunc(iw/2)*2:trunc(ih/2)*2,ass={ass.name}",
              "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
              "-pix_fmt", "yuv420p", "-c:a", "copy",
              "-movflags", "+faststart", str(out_path)], cwd=tmp)
    else:
        _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
              "-i", str(base), "-c", "copy",
              "-movflags", "+faststart", str(out_path)])
    say("完成")
    return out_path


def _atempo_chain(factor):
    """atempo 單次只吃 0.5~2.0，超出就串接。"""
    f = factor
    parts = []
    while f > 2.0:
        parts.append("atempo=2.0")
        f /= 2.0
    while f < 0.5:
        parts.append("atempo=0.5")
        f /= 0.5
    parts.append(f"atempo={f:.6f}")
    return ",".join(parts)


if __name__ == "__main__":
    # 快速自測：python engine.py 影片 '[{...ops...}]' 輸出
    v = Path(sys.argv[1])
    ops = json.loads(sys.argv[2])
    out = Path(sys.argv[3])
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        apply_ops(v, ops, out, Path(td), progress=print)
    print("OK", out)
