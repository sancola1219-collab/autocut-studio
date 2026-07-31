# -*- coding: utf-8 -*-
"""
autocut — 自動剪片一條龍
  模式A：影片有人聲 → Whisper 聽打字幕（自動轉繁體）→ 燒進影片
  模式B：無旁白素材 → 文字稿 TTS 配音（台灣腔）→ 字幕 → 燒進影片
  共通：背景音樂自動混音（人聲出現時音樂自動壓低）、橫式 16:9 / 直式 9:16 輸出

用法範例：
  python autocut.py 影片.mp4                          # 模式A，橫式
  python autocut.py 片1.mp4 片2.mp4 片3.mp4            # 多支依序串接再上字幕
  python autocut.py 影片.mp4 --layout both --music auto
  python autocut.py 素材.mp4 --script 稿子.txt --layout v
  python autocut.py 素材.mp4 --narration 我的錄音.m4a  # 模式C：用自己的聲音當旁白
  python autocut.py 影片.mp4 --subs 改好的字幕.srt     # 用改過的字幕重燒
  python autocut.py --list-voices                      # 看所有配音聲音
  python autocut.py --interactive                      # 問答模式（拖拉bat用這個）

文字稿對話：行首寫 [男] / [活潑女] / [yunjhe] 可切換聲音，例如
  [溫和女] 大家好，今天要介紹一個好用的工具。
  [男] 真的假的，快講重點！
"""
import argparse
import asyncio
import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
MUSIC_DIR = BASE / "背景音樂"
OUT_DIR = BASE / "輸出"
MEDIA_DIR = BASE / "素材"
MUSIC_EXTS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm",
              ".ts", ".mts", ".wmv", ".flv"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".avif"}
MEDIA_EXTS = VIDEO_EXTS | IMAGE_EXTS


def list_media(limit=9):
    """素材資料夾裡最新的影片與照片（含子資料夾），新→舊。"""
    if not MEDIA_DIR.exists():
        return []
    vids = [p for p in MEDIA_DIR.rglob("*")
            if p.is_file() and p.suffix.lower() in MEDIA_EXTS]
    vids.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return vids[:limit]

VOICES = {
    # 台灣
    "hsiaochen": "zh-TW-HsiaoChenNeural",
    "hsiaoyu":   "zh-TW-HsiaoYuNeural",
    "yunjhe":    "zh-TW-YunJheNeural",
    # 中國大陸
    "xiaoxiao":  "zh-CN-XiaoxiaoNeural",
    "xiaoyi":    "zh-CN-XiaoyiNeural",
    "yunxi":     "zh-CN-YunxiNeural",
    "yunjian":   "zh-CN-YunjianNeural",
    "yunyang":   "zh-CN-YunyangNeural",
    # 香港粵語
    "hiugaai":   "zh-HK-HiuGaaiNeural",
    "hiumaan":   "zh-HK-HiuMaanNeural",
    "wanlung":   "zh-HK-WanLungNeural",
}
VOICE_DESC = {
    "hsiaochen": "台灣女聲・溫和（預設）",
    "hsiaoyu":   "台灣女聲・活潑",
    "yunjhe":    "台灣男聲・沉穩",
    "xiaoxiao":  "大陸女聲・標準",
    "xiaoyi":    "大陸女聲・甜美",
    "yunxi":     "大陸男聲・陽光",
    "yunjian":   "大陸男聲・渾厚旁白",
    "yunyang":   "大陸男聲・新聞播報",
    "hiugaai":   "粵語女聲",
    "hiumaan":   "粵語女聲・成熟",
    "wanlung":   "粵語男聲",
}
VOICE_ALIASES = {
    "溫和女": "hsiaochen", "女": "hsiaochen", "活潑女": "hsiaoyu",
    "沉穩男": "yunjhe", "男": "yunjhe",
    "大陸女": "xiaoxiao", "甜美女": "xiaoyi", "大陸男": "yunxi",
    "渾厚男": "yunjian", "新聞男": "yunyang",
    "粵語女": "hiumaan", "粵語男": "wanlung",
}
AF = "aformat=sample_rates=48000:channel_layouts=stereo"


def resolve_voice(name: str):
    """代號／中文別名／完整聲音 ID → 完整聲音 ID；認不得回傳 None。"""
    name = name.strip()
    if name.lower() in VOICES:
        return VOICES[name.lower()]
    if name in VOICE_ALIASES:
        return VOICES[VOICE_ALIASES[name]]
    if re.fullmatch(r"[a-z]{2,3}-[A-Z]{2,4}-\w+Neural", name):
        return name
    return None


def print_voices():
    print("可用聲音（--voice 代號；文字稿行首寫 [代號] 或 [中文別名] 可切換）：")
    for code, vid in VOICES.items():
        print(f"  {code:10s} {VOICE_DESC.get(code, ''):20s} {vid}")
    print("中文別名：" + "、".join(f"[{k}]={v}" for k, v in VOICE_ALIASES.items()))
    print("也可直接給完整聲音 ID；全部語言的清單：python -m edge_tts --list-voices")


# ---------------------------------------------------------------- 小工具

def die(msg: str):
    print(f"\n【錯誤】{msg}")
    sys.exit(1)


def remove_stale(path: Path, tries=6, wait=0.7) -> Path:
    """刪掉舊的輸出檔，回傳實際可用的路徑。

    Windows 的縮圖產生器（dllhost）、防毒、剛關掉的播放器常會短暫抓著檔案，
    幾秒後自己會放，所以先重試幾次；真的一直鎖著就自動換一個檔名，
    免得辛苦跑完的成品沒地方放。
    """
    if not path.exists():
        return path
    for i in range(tries):
        try:
            path.unlink()
            return path
        except OSError:
            if i == 0:
                print("  舊檔被其他程式佔用（縮圖／播放器？），等一下再試…")
            time.sleep(wait)
    for n in range(2, 100):
        alt = path.with_name(f"{path.stem}_{n}{path.suffix}")
        if not alt.exists():
            print(f"  舊檔一直被鎖住，這次改存成：{alt.name}")
            return alt
    die(f"輸出檔被鎖住又找不到可用的替代檔名，請先關掉播放器再重跑：\n{path}")


def run(cmd, cwd=None, quiet=True):
    """跑外部指令，出錯就把 stderr 印出來。"""
    r = subprocess.run(
        [str(c) for c in cmd], cwd=cwd,
        capture_output=quiet, encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        tail = (r.stderr or "")[-2000:] if quiet else ""
        die(f"指令失敗：{cmd[0]}\n{tail}")
    return r


def ffprobe_json(path: Path) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, encoding="utf-8", errors="replace",
    )
    if r.returncode != 0 or not r.stdout:
        die(f"讀不到媒體資訊：{path}")
    return json.loads(r.stdout)


def probe_duration(path: Path) -> float:
    info = ffprobe_json(path)
    d = info.get("format", {}).get("duration")
    if d is None:
        for s in info.get("streams", []):
            if s.get("duration"):
                d = s["duration"]
                break
    return float(d or 0)


def probe_vstream_duration(path: Path) -> float:
    """影像軌本身的長度（音軌較長時 format duration 會偏大，串接要用這個）。"""
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-select_streams", "v:0",
         "-show_entries", "stream=duration", "-of", "json", str(path)],
        capture_output=True, encoding="utf-8", errors="replace",
    )
    try:
        d = float(json.loads(r.stdout)["streams"][0]["duration"])
        if d > 0:
            return d
    except Exception:
        pass
    return probe_duration(path)


def probe_video(path: Path):
    """回傳 (寬, 高, 時長, 有沒有聲音軌)"""
    info = ffprobe_json(path)
    w = h = None
    has_audio = False
    for s in info.get("streams", []):
        if s.get("codec_type") == "video" and w is None:
            w, h = int(s["width"]), int(s["height"])
            # 手機直拍常帶旋轉標記，寬高要對調
            rot = 0
            sd_list = s.get("side_data_list") or []
            for sd in sd_list:
                if "rotation" in sd:
                    rot = int(sd["rotation"])
            if abs(rot) in (90, 270):
                w, h = h, w
        if s.get("codec_type") == "audio":
            has_audio = True
    if w is None:
        die(f"這個檔案裡找不到影像軌：{path}")
    return w, h, probe_duration(path), has_audio


def read_text_smart(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "cp950", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def to_traditional(text: str) -> str:
    try:
        from opencc import OpenCC
        return OpenCC("s2twp").convert(text).replace("臺", "台")
    except Exception:
        return text


# ---------------------------------------------------------------- 字幕資料

def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def ass_time(t: float) -> str:
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def write_srt(segments, path: Path):
    lines = []
    for i, (st, en, tx) in enumerate(segments, 1):
        lines.append(f"{i}\n{srt_time(st)} --> {srt_time(en)}\n{tx}\n")
    path.write_text("\n".join(lines), encoding="utf-8-sig")


def parse_srt(path: Path):
    text = read_text_smart(path)
    pattern = re.compile(
        r"(\d+:\d+:\d+[,.]\d+)\s*-->\s*(\d+:\d+:\d+[,.]\d+)\s*\n(.*?)(?=\n\s*\n|\Z)",
        re.S,
    )

    def t2s(t):
        t = t.replace(",", ".")
        h, m, s = t.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)

    segs = []
    for m in pattern.finditer(text):
        tx = re.sub(r"\s*\n\s*", " ", m.group(3)).strip()
        if tx:
            segs.append((t2s(m.group(1)), t2s(m.group(2)), tx))
    if not segs:
        die(f"讀不到字幕內容：{path}")
    return segs


def smart_chunks(text: str, maxlen: int):
    """把長句子切成幾段好讀的字幕，優先在標點處斷。"""
    tokens = re.split(r"(?<=[，、,;；：:  ])", text)
    tokens = [t for t in tokens if t.strip()]
    chunks, cur = [], ""
    for t in tokens:
        if cur and len(cur) + len(t) > maxlen:
            chunks.append(cur)
            cur = t
        else:
            cur += t
    if cur:
        chunks.append(cur)
    out = []
    for c in chunks:
        c = c.strip().strip("，、,;；")
        while len(c) > maxlen:
            out.append(c[:maxlen])
            c = c[maxlen:]
        if c:
            out.append(c)
    return out or [text]


def split_for_display(segments, maxlen: int):
    """超過 maxlen 的字幕依字數比例切開時間。"""
    out = []
    for st, en, tx in segments:
        tx = tx.strip()
        if not tx:
            continue
        if len(tx) <= maxlen:
            out.append((st, en, tx))
            continue
        parts = smart_chunks(tx, maxlen)
        total = sum(len(p) for p in parts) or 1
        cur, dur = st, en - st
        for p in parts:
            d = dur * len(p) / total
            out.append((cur, cur + d, p))
            cur += d
    return out


def ass_escape(text: str) -> str:
    return (text.replace("\\", "＼").replace("{", "｛").replace("}", "｝")
            .replace("\n", "\\N"))


def build_ass(segments, w: int, h: int, vertical: bool, path: Path):
    if vertical:
        fs, outline, shadow = 64, 4, 0
        mv, ml = int(h * 0.30), 60
        maxlen = 20
    else:
        fs = max(30, int(h * 0.052))
        outline = max(2, round(fs * 0.06))
        shadow = 1
        mv, ml = int(h * 0.045), int(w * 0.06)
        maxlen = 30
    segs = split_for_display(segments, maxlen)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Microsoft JhengHei,{fs},&H00FFFFFF,&H00FFFFFF,&H00000000,&H66000000,-1,0,0,0,100,100,0,0,1,{outline},{shadow},2,{ml},{ml},{mv},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for i, (st, en, tx) in enumerate(segs):
        en = max(en, st + 0.5)
        # 拉長最短顯示時間時不可壓到下一句，避免兩句疊在畫面上
        if i + 1 < len(segs) and segs[i + 1][0] > st + 0.1:
            en = min(en, segs[i + 1][0])
        lines.append(
            f"Dialogue: 0,{ass_time(st)},{ass_time(en)},Default,,0,0,0,,{ass_escape(tx)}\n"
        )
    path.write_text("".join(lines), encoding="utf-8-sig")


# ---------------------------------------------------------------- 模式A：聽打

def transcribe(video: Path, model_name: str, tmp: Path):
    print(f"\n【聽打字幕】使用 Whisper {model_name} 模型（第一次會先下載模型檔，請稍等）")
    wav = tmp / "asr_in.wav"
    run(["ffmpeg", "-y", "-i", video, "-vn", "-ac", "1", "-ar", "16000", wav])

    from faster_whisper import WhisperModel

    def _collect(model):
        seg_iter, _info = model.transcribe(
            str(wav), language="zh", beam_size=5,
            vad_filter=True, vad_parameters={"min_silence_duration_ms": 400},
            condition_on_previous_text=False,
            initial_prompt="以下是繁體中文的內容。",
        )
        out, prev = [], None
        for s in seg_iter:
            tx = to_traditional(s.text.strip())
            if not tx:
                continue
            if tx == prev and (s.end - s.start) < 0.6:   # 幻聽重複句跳過
                continue
            prev = tx
            out.append((float(s.start), float(s.end), tx))
            print(f"  {srt_time(s.start)}  {tx}")
        return out

    try:
        segs = _collect(WhisperModel(model_name, device="auto", compute_type="auto"))
    except Exception:
        # GPU 初始化成功但推論才失敗（缺 cuDNN 之類）也會落到這裡
        print("（GPU 模式失敗，改用 CPU 重試）")
        segs = _collect(WhisperModel(model_name, device="cpu", compute_type="int8"))
    if not segs:
        die("整支影片都沒聽到人聲。如果是無旁白素材，請改用文字稿模式（--script）。")
    return segs


# ---------------------------------------------------------------- 模式B：TTS

def split_sentences(text: str):
    """把一段文字切成句子；純標點殘句併回前一句、開頭的直接丟。"""
    parts = re.split(r"(?<=[。！？!?；;…])", text)
    rough = [p.strip() for p in parts if p.strip()]
    sents = []
    for s in rough:
        stripped = re.sub(r"[^\w一-鿿]", "", s)
        if not stripped:            # 純標點殘渣：併回前一句，開頭的才丟
            if sents:
                sents[-1] += s
            continue
        if len(stripped) < 2 and sents:
            sents[-1] += s          # 短句併回前一句
        else:
            sents.append(s)         # 行首短句（嗯。好！）自成一句，不可丟失
    return sents


GAP_SHORT, GAP_SENT, GAP_PARA = 0.16, 0.34, 0.9


def parse_script(text: str, default_voice: str):
    """逐行解析文字稿。行首 [聲音] 或 聲音： 可切換配音員（沿用到下一次切換），
    認不得的名字當一般文字保留。回傳 [(voice_id, 句子, 後面停多久), ...]

    停頓長短照標點與段落決定，像真人那樣有快有慢：話沒說完（逗號結尾）停很短、
    一句講完停一下、遇到空行當成換段落，多喘一口氣。
    """
    out, cur = [], default_voice
    for line in text.replace("\r", "\n").split("\n"):
        line = line.strip()
        if not line:
            if out:            # 空行＝換段落，讓前一句後面多停一下換氣
                out[-1] = (out[-1][0], out[-1][1], GAP_PARA)
            continue
        m = (re.match(r"^\[([^\[\]]{1,40})\]\s*[：:]?\s*(.*)$", line)
             or re.match(r"^([^\s：:]{1,12})[：:]\s*(.*)$", line))
        if m:
            v = resolve_voice(m.group(1))
            if v:
                cur = v
                line = m.group(2).strip()
                if not line:
                    continue
        for s in split_sentences(line):
            gap = GAP_SHORT if s[-1] in "，,、；;：:" else GAP_SENT
            out.append((cur, s, gap))
    return out


async def _tts_batch(pairs, rate, pitch, tmp: Path):
    import edge_tts
    files = []
    for i, (v, s, _g) in enumerate(pairs):
        mp3 = tmp / f"tts_{i:04d}.mp3"
        for attempt in range(3):
            try:
                await edge_tts.Communicate(s, voice=v, rate=rate,
                                           pitch=pitch).save(str(mp3))
                break
            except Exception:
                if attempt == 2:
                    raise
                await asyncio.sleep(1.5)
        print(f"  配音 {i + 1}/{len(pairs)}  {s[:24]}")
        files.append(mp3)
    return files


def tts_narration(script_text: str, voice: str, rate: str, tmp: Path,
                  pitch: str = "+0Hz"):
    """回傳 (segments, 旁白音檔, 總長)"""
    pairs = parse_script(to_traditional(script_text), voice)
    if not pairs:
        die("文字稿是空的。")
    n_voices = len({v for v, _s, _g in pairs})
    who = f"多聲音對話（{n_voices} 種聲音）" if n_voices > 1 else f"聲音：{voice}"
    print(f"\n【TTS 配音】{who}（需要網路連線）")
    try:
        mp3s = asyncio.run(_tts_batch(pairs, rate, pitch, tmp))
    except Exception as e:
        die(f"TTS 失敗（檢查一下網路？）：{e}")

    # 每種停頓長度做一個靜音檔，句子之間照需要插入
    gap_wavs = {}
    for g in sorted({g for _v, _s, g in pairs}):
        gw = tmp / f"gap_{int(g * 1000)}.wav"
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
             "-t", f"{g}", "-c:a", "pcm_s16le", gw])
        gap_wavs[g] = gw

    segs, wavs, gaps, cursor = [], [], [], 0.0
    for mp3, (_v, sent, g) in zip(mp3s, pairs):
        wav = mp3.with_suffix(".wav")
        run(["ffmpeg", "-y", "-i", mp3, "-ar", "48000", "-ac", "2",
             "-c:a", "pcm_s16le", wav])
        d = probe_duration(wav)
        segs.append((cursor, cursor + d, sent))
        wavs.append(wav)
        gaps.append(g)
        cursor += d + g

    lst = tmp / "concat.txt"
    entries = []
    for i, w in enumerate(wavs):
        entries.append(f"file '{w.name}'")
        if i < len(wavs) - 1:
            entries.append(f"file '{gap_wavs[gaps[i]].name}'")
    lst.write_text("\n".join(entries), encoding="utf-8")

    narration = tmp / "narration.wav"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst,
         "-c:a", "pcm_s16le", narration], cwd=tmp)
    total = probe_duration(narration)
    print(f"  旁白完成，共 {len(pairs)} 句、{total:.1f} 秒")
    return segs, narration, total


# ---------------------------------------------------------------- 多支串接

def join_videos(paths, tmp: Path, transition: str = "fade"):
    """把多支影片統一規格後依序串接成一支，回傳串接檔（放在 tmp 內）。"""
    w, h, _, _ = probe_video(paths[0])
    w, h = max(2, w - w % 2), max(2, h - h % 2)
    print(f"\n【串接】共 {len(paths)} 支，統一為 {w}x{h}（以第一支為準）")
    fd = 0.4
    segs = []
    for i, p in enumerate(paths):
        _, _, _, has_aud = probe_video(p)
        dur = probe_vstream_duration(p)   # 以影像軌為準，音軌較長也不會凍格
        if dur <= 0:
            die(f"讀不到影片長度：{p}")
        seg = tmp / f"seg_{i:03d}.mp4"
        vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
              f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,fps=30,setsar=1")
        af = AF
        fd_i = min(fd, dur / 2)           # 極短片縮短轉場，避免 fade in/out 重疊變全黑
        if transition == "fade" and fd_i >= 0.05:
            st_out = max(0.0, dur - fd_i)
            vf += (f",fade=t=in:st=0:d={fd_i:.3f},"
                   f"fade=t=out:st={st_out:.3f}:d={fd_i:.3f}")
            af += (f",afade=t=in:st=0:d={fd_i:.3f},"
                   f"afade=t=out:st={st_out:.3f}:d={fd_i:.3f}")
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", p]
        if has_aud:
            cmd += ["-map", "0:v:0", "-map", "0:a:0"]
        else:   # 沒聲音的補靜音軌，串接後聲音才不會錯位
            cmd += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i",
                    "anullsrc=r=48000:cl=stereo",
                    "-map", "0:v:0", "-map", "1:a"]
        cmd += ["-t", f"{dur:.3f}", "-vf", vf, "-af", af,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                "-ar", "48000", seg]
        print(f"  [{i + 1}/{len(paths)}] {p.name}（{dur:.1f} 秒）")
        run(cmd)
        segs.append(seg)
    lst = tmp / "join.txt"
    lst.write_text("\n".join(f"file '{s.name}'" for s in segs), encoding="utf-8")
    joined = tmp / "joined.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst,
         "-c", "copy", joined], cwd=tmp)
    print(f"  串接完成，總長 {probe_duration(joined):.1f} 秒")
    return joined


# ---------------------------------------------------------------- 合成輸出

def render(video: Path, out_path: Path, segments, vertical: bool, tmp: Path, *,
           narration: Path = None, total: float = None,
           music: Path = None, music_vol: float = None, keep_ambient: bool = False,
           style: str = "plain", title: str = None):
    w, h, vdur, has_aud = probe_video(video)
    if vertical:
        out_w, out_h = 1080, 1920
    else:
        # x264 + yuv420p 不吃奇數寬高，先修成偶數（crop 只裁 1px 不重取樣）
        out_w, out_h = w - w % 2, h - h % 2

    ass_file = tmp / ("subs_v.ass" if vertical else "subs_h.ass")
    extra_vf = ""
    if style and style != "plain":
        sys.path.insert(0, str(BASE / "voicecut"))
        import styles as _styles
        ass_file.write_text(
            _styles.build(segments, out_w, out_h, style, title=title,
                          total=(total if total else None)),
            encoding="utf-8")
        extra_vf = _styles.video_filter(style)
        print(f"  字幕風格：{style}")
    else:
        build_ass(segments, out_w, out_h, vertical, ass_file)

    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning", "-stats"]
    if narration and total and vdur < total:
        args += ["-stream_loop", "-1"]          # 素材太短就循環播放
    args += ["-i", str(video)]
    idx, n_idx, m_idx = 1, None, None
    if narration:
        n_idx = idx
        args += ["-i", str(narration)]
        idx += 1
    if music:
        m_idx = idx
        args += ["-stream_loop", "-1", "-i", str(music)]
        idx += 1

    # ---- 影像鏈
    if vertical:
        # 背景模糊用「縮小→模糊→放大」省 CPU，比直接 gblur 快幾十倍
        vchain = (
            "[0:v]split=2[bga][fga];"
            "[bga]scale=270:480:force_original_aspect_ratio=increase,"
            "crop=270:480,boxblur=10:2,eq=brightness=-0.08,scale=1080:1920[bg];"
            "[fga]scale=1080:1920:force_original_aspect_ratio=decrease:"
            "force_divisible_by=2[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2"
            + (f",{extra_vf}" if extra_vf else "")
            + f",ass={ass_file.name}[v]"
        )
    else:
        vchain = (f"[0:v]crop=trunc(iw/2)*2:trunc(ih/2)*2"
                  + (f",{extra_vf}" if extra_vf else "")
                  + f",ass={ass_file.name}[v]")

    # ---- 聲音鏈
    T = total if total else vdur
    fade_st = max(0.0, T - 3.0)
    achain, voice = [], None
    if narration:
        if keep_ambient and has_aud:
            achain.append(f"[0:a]{AF},volume=0.15[amb]")
            achain.append(f"[{n_idx}:a]{AF}[nar]")
            achain.append("[nar][amb]amix=inputs=2:duration=first:normalize=0[voice]")
        else:
            achain.append(f"[{n_idx}:a]{AF}[voice]")
        voice = "[voice]"
    elif has_aud:
        achain.append(f"[0:a]{AF}[voice]")
        voice = "[voice]"

    if music and voice:
        mv = music_vol if music_vol is not None else 0.22
        achain.append(f"{voice}asplit=2[vo][sc]")
        achain.append(f"[{m_idx}:a]{AF},volume={mv}[bg0]")
        achain.append("[bg0][sc]sidechaincompress="
                      "threshold=0.02:ratio=20:attack=80:release=400[duck]")
        achain.append(f"[duck]afade=t=out:st={fade_st:.2f}:d=3[bgf]")
        achain.append("[vo][bgf]amix=inputs=2:duration=first:normalize=0,"
                      "alimiter=limit=0.95[aout]")
        amap = "[aout]"
    elif music:
        mv = music_vol if music_vol is not None else 0.5   # 沒人聲時音樂是主角
        achain.append(f"[{m_idx}:a]{AF},volume={mv},atrim=0:{T:.2f},"
                      f"afade=t=out:st={fade_st:.2f}:d=3[aout]")
        amap = "[aout]"
    elif voice:
        amap = voice
    else:
        amap = None

    fc = ";".join([vchain] + achain)
    args += ["-filter_complex", fc, "-map", "[v]"]
    if amap:
        args += ["-map", amap]
    else:
        args += ["-an"]
    if narration and total:
        args += ["-t", f"{total + 0.6:.2f}"]
    args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
             "-movflags", "+faststart", str(out_path)]

    print(f"\n【合成】{'直式 9:16' if vertical else '橫式'} → {out_path.name}")
    # 先清掉舊檔，避免合成失敗還誤判成功；被鎖住會自動換檔名
    out_path = remove_stale(out_path)
    args[-1] = str(out_path)
    r = subprocess.run([str(a) for a in args], cwd=tmp, check=False)
    if r.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
        die("ffmpeg 合成失敗，上面應該有錯誤訊息。")
    return out_path


# ---------------------------------------------------------------- 互動模式

def ask(prompt: str, default: str = "") -> str:
    try:
        v = input(prompt).strip().strip('"').strip("'")
    except EOFError:
        die("讀不到輸入（stdin 已關閉），互動模式需要在終端機視窗執行。")
    return v or default


def list_music():
    if not MUSIC_DIR.exists():
        return []
    return sorted(p for p in MUSIC_DIR.iterdir()
                  if p.suffix.lower() in MUSIC_EXTS)


def interactive_fill(args):
    print("=" * 46)
    print("  自動剪片小工房")
    print("=" * 46)
    vids = [v for v in (args.video or []) if Path(v).exists()]
    lib = list_media()
    if not vids and lib:
        print("「素材」資料夾裡的影片（新→舊）：")
        for i, p in enumerate(lib, 1):
            print(f"  [{i}] {p.name}")

    def pick(ans):
        """輸入編號就換成素材清單裡對應的檔案路徑。"""
        if ans.isdecimal() and lib and 1 <= int(ans) <= len(lib):
            return str(lib[int(ans) - 1])
        return ans

    while not vids:
        p = pick(ask("影片路徑或上面的編號（也可直接把檔案拖進這個視窗）："))
        if p and Path(p).exists():
            vids.append(p)
        else:
            print("  找不到這個檔案，再試一次～")
    while True:
        more = pick(ask("要串接下一支就繼續貼路徑／編號，沒有就直接 Enter："))
        if not more:
            break
        if Path(more).exists():
            vids.append(more)
            print(f"  已加入，目前 {len(vids)} 支")
        else:
            print("  找不到這個檔案～")
    if len(vids) > 1:
        print("\n串接順序：")
        for i, v in enumerate(vids, 1):
            print(f"  [{i}] {Path(v).name}")
        while True:
            order = (ask("要調整就輸入新順序（例如 2 1 3），不用就 Enter：")
                     .replace("，", " ").replace(",", " ").split())
            if not order:
                break
            if (all(n.isdecimal() for n in order)
                    and sorted(int(n) for n in order) == list(range(1, len(vids) + 1))):
                vids = [vids[int(n) - 1] for n in order]
                print("  新順序：" + " → ".join(Path(v).name for v in vids))
                break
            print(f"  格式不對～要輸入 1 到 {len(vids)} 的完整順序，用空格分隔")
        print("轉場：[1] 淡入淡出（預設）  [2] 直接相接")
        args.transition = "none" if ask("選擇：", "1") == "2" else "fade"
    args.video = vids

    print("\n這支影片是哪一種？")
    print("  [1] 裡面有人講話 → 自動聽打上字幕")
    print("  [2] 無旁白素材   → 用文字稿配音＋字幕")
    if ask("選擇 (1/2)，預設 1：", "1") == "2":
        args.subs = None
        while not args.script:
            p = ask("文字稿 .txt 路徑（直接按 Enter 可改用貼上模式）：")
            if p and Path(p).exists():
                args.script = p
                break
            print("貼上文字稿（可多行），貼完後空一行按 Enter 結束：")
            pasted = [p] if p else []
            while True:
                try:
                    line = input()
                except EOFError:
                    break
                if not line.strip():
                    break
                pasted.append(line)
            text = "\n".join(pasted).strip()
            if len(text) >= 4:
                OUT_DIR.mkdir(exist_ok=True)
                tmp_txt = OUT_DIR / "_臨時文字稿.txt"
                tmp_txt.write_text(text, encoding="utf-8")
                args.script = str(tmp_txt)
        print("\n聲音要用哪一種？（台灣腔）")
        print("  [1] 女聲・溫和（預設）  [2] 女聲・活潑  [3] 男聲・沉穩")
        print("  [4] 其他（大陸腔／粵語…看完整清單）")
        c = ask("選擇：", "1")
        if c == "4":
            print_voices()
            args.voice = ask("輸入聲音代號：", "hsiaochen")
        else:
            args.voice = {"1": "hsiaochen", "2": "hsiaoyu",
                          "3": "yunjhe"}.get(c, "hsiaochen")
        print("小撇步：文字稿裡一行開頭寫 [男] 或 [活潑女] 之類，就能切聲音做對話。")
    else:
        args.script = None          # 蓋掉 CLI 給過的 --script，以互動選擇為準
        print("\n字幕辨識品質？")
        print("  [1] 精準（預設，較慢）  [2] 快速（可能有錯字）")
        args.model = "small" if ask("選擇：", "1") == "2" else args.model

    music = list_music()
    print("\n背景音樂：")
    print("  [0] 不加")
    for i, m in enumerate(music, 1):
        print(f"  [{i}] {m.name}")
    if music:
        print("  [R] 隨機挑一首")
    choice = ask("選擇（也可以直接貼音樂檔路徑）：", "0")
    if choice.upper() == "R" and music:
        args.music = str(random.choice(music))
    elif choice.isdigit() and 1 <= int(choice) <= len(music):
        args.music = str(music[int(choice) - 1])
    elif choice not in ("0", "") and Path(choice).exists():
        args.music = choice
    else:
        args.music = None           # 明確選了不加音樂，也蓋掉 CLI 給過的值

    print("\n輸出版式：")
    print("  [1] 橫式（預設）  [2] 直式 9:16  [3] 都要")
    args.layout = {"1": "h", "2": "v", "3": "both"}.get(ask("選擇：", "1"), "h")
    return args


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(description="自動剪片：字幕／配音／音樂一條龍")
    ap.add_argument("video", nargs="*", help="影片檔案（給多個＝依序串接成一支）")
    ap.add_argument("--transition", choices=["fade", "none"], default="fade",
                    help="多支串接的轉場：fade淡入淡出（預設）/ none直接相接")
    ap.add_argument("--photo-dur", type=float, default=4.0,
                    help="每張照片變成幾秒的動態片段（預設 4 秒）")
    ap.add_argument("--style", default="plain",
                    choices=["plain", "cute", "cinema", "rec", "neon"],
                    help="字幕風格：plain乾淨 cute可愛大字 cinema電影感 "
                         "rec錄影機UI neon霓虹")
    ap.add_argument("--title", help="畫面上方常駐的標題文字（選用）")
    ap.add_argument("--script", help="文字稿 .txt → 啟用 TTS 配音模式")
    ap.add_argument("--subs", help="現成 .srt 字幕（跳過聽打，直接燒）")
    ap.add_argument("--music", help="背景音樂檔，或 auto＝從「背景音樂」資料夾隨機挑")
    ap.add_argument("--music-vol", type=float, default=None,
                    help="音樂音量 0~1（預設：有人聲 0.22、純音樂 0.5）")
    ap.add_argument("--narration",
                    help="旁白音檔。單獨用＝模式C（用你自己錄的聲音，自動聽打上字幕）；"
                         "搭配 --subs＝重燒用（不重新配音）")
    ap.add_argument("--layout", choices=["h", "v", "both"], default="h",
                    help="h橫式 v直式 both都要")
    ap.add_argument("--model", default="medium",
                    help="Whisper 模型：small（快）/ medium（預設）/ large-v3（最準）")
    ap.add_argument("--voice", default="hsiaochen",
                    help="TTS 聲音代號（--list-voices 看清單），或完整聲音 ID")
    ap.add_argument("--list-voices", action="store_true", help="列出可用聲音")
    ap.add_argument("--rate", default="+0%", help="TTS 語速，例如 +10%% 或 --rate=-10%%")
    ap.add_argument("--pitch", default="+0Hz",
                    help="TTS 音高，壓低一點比較沉穩自然，例如 --pitch=-8Hz")
    ap.add_argument("--keep-ambient", action="store_true",
                    help="配音模式下保留素材原本的環境音（小聲墊底）")
    ap.add_argument("--interactive", action="store_true", help="問答模式")
    # argparse 會把 "-10%"、"-8Hz" 當成未知選項，先幫使用者併成 --rate=-10% 的寫法
    NEG_OK = {"--rate": r"[+-]?\d{1,3}%", "--pitch": r"[+-]?\d{1,3}Hz"}
    argv, skip = [], False
    raw = sys.argv[1:]
    for i, a in enumerate(raw):
        if skip:
            skip = False
            continue
        if (a in NEG_OK and i + 1 < len(raw)
                and re.fullmatch(NEG_OK[a], raw[i + 1], re.I)):
            argv.append(f"{a}={raw[i + 1]}")
            skip = True
        else:
            argv.append(a)
    args = ap.parse_args(argv)

    if args.list_voices:
        print_voices()
        return

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        die("找不到 ffmpeg／ffprobe，請先安裝並加入 PATH。")

    MUSIC_DIR.mkdir(exist_ok=True)
    OUT_DIR.mkdir(exist_ok=True)

    if args.interactive or not args.video:
        args = interactive_fill(args)

    if args.script and args.subs:
        die("--script 跟 --subs 不能同時用。\n"
            "想改內容→只給 --script 重新配音；只想修字幕錯字→用 --subs 搭配 --narration。")

    # 最終合成的 ffmpeg 以 temp 目錄為工作目錄，所有輸入一律先轉絕對路徑
    videos = [Path(v).expanduser().resolve() for v in args.video]
    for v in videos:
        if not v.exists():
            die(f"找不到檔案：{v}")
        if v.suffix.lower() not in MEDIA_EXTS:
            die(f"不認識的檔案格式：{v.name}\n"
                f"影片支援 {' '.join(sorted(VIDEO_EXTS))}\n"
                f"照片支援 {' '.join(sorted(IMAGE_EXTS))}（HEIC 請先轉成 JPG）")

    music = None
    if args.music:
        if args.music.lower() == "auto":
            pool = list_music()
            if pool:
                music = random.choice(pool)
                print(f"（隨機選了背景音樂：{music.name}）")
            else:
                print("（「背景音樂」資料夾是空的，這次先不加音樂）")
        else:
            music = Path(args.music).expanduser().resolve()
            if not music.exists():
                die(f"找不到音樂檔：{music}")

    voice = resolve_voice(args.voice)
    if not voice:
        die(f"不認識的聲音：{args.voice}（用 --list-voices 看清單）")
    if not re.fullmatch(r"[+-]\d{1,3}%", args.rate):
        die(f"--rate 格式要像 +10%% 這樣：{args.rate}")
    # edge-tts 只吃大寫 Hz，順手幫使用者把 -8hz 修正掉
    if re.fullmatch(r"[+-]\d{1,3}hz", args.pitch, re.I):
        args.pitch = args.pitch[:-2] + "Hz"
    if not re.fullmatch(r"[+-]\d{1,3}Hz", args.pitch):
        die(f"--pitch 格式要像 --pitch=-8Hz 這樣：{args.pitch}")
    # 三種來源同時給時講清楚誰贏，不要靜默忽略
    if args.subs and args.script:
        print("【提醒】同時給了 --subs 與 --script，這次用 --subs（不重新配音）")
    elif args.narration and args.script:
        print("【提醒】同時給了 --narration 與 --script，這次用 --narration"
              "（用你的錄音，不做 TTS）")

    with tempfile.TemporaryDirectory(prefix="autocut_") as td:
        tmp = Path(td)
        narration = total = narr_keep = None

        # 照片先變成 Ken Burns 影片片段（緩慢推拉），再跟影片一起處理
        orig_names = [v.stem for v in videos]
        orig_inputs = list(videos)          # 重燒提示要指回使用者原本給的檔案
        if any(v.suffix.lower() in IMAGE_EXTS for v in videos):
            sys.path.insert(0, str(BASE / "voicecut"))
            import photos as _photos
            pw, ph = (1080, 1920) if args.layout == "v" else (1920, 1080)
            # 有影片的話跟第一支影片的尺寸對齊，避免串接時被縮放
            first_vid = next((v for v in videos
                              if v.suffix.lower() in VIDEO_EXTS), None)
            if first_vid:
                vw, vh, _, _ = probe_video(first_vid)
                pw, ph = vw - vw % 2, vh - vh % 2
            print(f"\n【照片】轉成 Ken Burns 動態片段（每張 {args.photo_dur:.1f} 秒）")
            videos, n = _photos.convert_mixed(videos, tmp, pw, ph,
                                              args.photo_dur)
            print(f"  共處理 {n} 張")

        if len(videos) > 1:
            video = join_videos(videos, tmp, args.transition)
            stem = f"{orig_names[0]}_合併{len(videos)}支"
        else:
            video = videos[0]
            stem = orig_names[0]

        if args.subs:
            segments = parse_srt(Path(args.subs).expanduser().resolve())
            print(f"【字幕】使用現成字幕：{args.subs}（{len(segments)} 句）")
            if args.narration:
                narration = Path(args.narration).expanduser().resolve()
                if not narration.exists():
                    die(f"找不到旁白檔：{narration}")
                total = probe_duration(narration)
                if total <= 0:
                    die(f"讀不到旁白長度，檔案可能壞了：{narration}")
                print(f"【旁白】使用現成旁白：{narration.name}（{total:.1f} 秒）")
        elif args.narration:
            # 模式C：用自己錄的旁白，聽打它來取得字幕時間點
            narration = Path(args.narration).expanduser().resolve()
            if not narration.exists():
                die(f"找不到旁白檔：{narration}")
            # 一律先轉成乾淨的 wav：webm/m4a 常常讀不到時長，轉檔後才準
            norm = tmp / "narration_in.wav"
            run(["ffmpeg", "-y", "-i", narration, "-vn",
                 "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", norm])
            total = probe_duration(norm)
            if total <= 0:
                die(f"讀不到旁白長度，檔案可能壞了：{narration}")
            narration = norm
            print(f"【旁白】用你自己的錄音：{Path(args.narration).name}"
                  f"（{total:.1f} 秒）")
            segments = transcribe(narration, args.model, tmp)
            keep = OUT_DIR / f"{stem}_旁白.wav"
            # 使用者可能直接指定「輸出」裡的檔案，那就是同一個檔，不用也不能複製
            if keep.resolve() != Path(args.narration).expanduser().resolve():
                narr_keep = remove_stale(keep)
                shutil.copy2(narration, narr_keep)
        elif args.script:
            sp = Path(args.script).expanduser().resolve()
            if not sp.exists():
                die(f"找不到文字稿：{sp}")
            segments, narration, total = tts_narration(
                read_text_smart(sp), voice, args.rate, tmp, pitch=args.pitch)
            # 旁白留一份，之後只修字幕錯字就不用重新配音
            narr_keep = OUT_DIR / f"{stem}_旁白.wav"
            shutil.copy2(narration, narr_keep)
        else:
            _, _, _, has_aud = probe_video(video)
            if not has_aud:
                die("這支影片沒有聲音軌，沒辦法聽打字幕。\n"
                    "如果是無旁白素材，請給文字稿：--script 稿子.txt")
            segments = transcribe(video, args.model, tmp)

        srt_path = OUT_DIR / f"{stem}_字幕.srt"
        write_srt(segments, srt_path)

        layouts = {"h": [False], "v": [True], "both": [False, True]}[args.layout]
        outputs = []
        for vertical in layouts:
            suffix = "直式" if vertical else "橫式"
            out_path = OUT_DIR / f"{stem}_成品_{suffix}.mp4"
            outputs.append(render(          # 被鎖住時實際檔名可能不同
                video, out_path, segments, vertical, tmp,
                narration=narration, total=total,
                music=music, music_vol=args.music_vol,
                keep_ambient=args.keep_ambient,
                style=args.style, title=args.title))

    print("\n" + "=" * 46)
    print("完工！檔案在這裡：")
    for o in outputs:
        print(f"  {o}")
    print(f"  {srt_path}  ← 字幕檔，有錯字可以改")
    # 重燒提示要帶齊當次的參數，照抄才會得到一樣的成品
    # 用使用者原本給的路徑（照片轉出的暫存檔跟著 temp 目錄一起消失了）
    reburn = orig_inputs if 'orig_inputs' in dir() else videos
    vids_arg = " ".join(f'"{v}"' for v in reburn)
    opts = ""
    if any(v.suffix.lower() in IMAGE_EXTS for v in reburn) and args.photo_dur != 4.0:
        opts += f" --photo-dur {args.photo_dur}"
    if len(reburn) > 1 and args.transition == "none":
        opts += " --transition none"
    if args.layout != "h":
        opts += f" --layout {args.layout}"
    if music:
        opts += f' --music "{music}"'
    if args.music_vol is not None:
        opts += f" --music-vol {args.music_vol}"
    if args.keep_ambient:
        opts += " --keep-ambient"
    if args.style != "plain":
        opts += f" --style {args.style}"
    if args.title:
        opts += f' --title "{args.title}"'
    tts_opts = ""
    if args.voice != "hsiaochen":
        tts_opts += f" --voice {args.voice}"
    if args.rate != "+0%":
        tts_opts += f" --rate={args.rate}"
    if args.pitch != "+0Hz":
        tts_opts += f" --pitch={args.pitch}"
    if narr_keep:
        print("改完想重燒：")
        print(f'  只修字幕錯字：python autocut.py {vids_arg}{opts} --subs "{srt_path}" '
              f'--narration "{narr_keep}"')
        print(f'  改稿子重配音：python autocut.py {vids_arg}{opts}{tts_opts} '
              f'--script 稿子.txt')
    else:
        extra = f' --narration "{args.narration}"' if args.narration else ""
        print("改完字幕想重燒：")
        print(f'  python autocut.py {vids_arg}{opts} --subs "{srt_path}"{extra}')


if __name__ == "__main__":
    main()
