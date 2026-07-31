# -*- coding: utf-8 -*-
"""字幕風格庫：把之前一次性腳本裡的字卡樣式抽成可重複用的預設。

給 srt + 影片尺寸，產出對應風格的 .ass 覆蓋層。
autocut.py 用 --style 選，也可以單獨產 ass 檔自己拿去燒。

  plain   乾淨白字（autocut 原本的樣式）
  cute    大字＋粉紅粗邊＋彈跳進場（短影音常見）
  cinema  細字＋上下黑邊＋淡入淡出（紀錄片感）
  rec     可愛字幕＋錄影機 UI（REC 閃爍紅點／電量／4K 60FPS）
  neon    霓虹光暈雙層字（夜景、招牌感）
"""
import sys
from pathlib import Path

STYLES = ("plain", "cute", "cinema", "rec", "neon")


def _t(x: float) -> str:
    h, r = divmod(max(0.0, x), 3600)
    m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{int(s):02d}.{min(int(round(s % 1 * 100)), 99):02d}"


def wrap_text(tx: str, limit: int) -> str:
    """太長就在最靠中間的頓點折行，避免大字爆出畫面。"""
    if len(tx) <= limit:
        return tx
    cuts = [i for i, c in enumerate(tx) if c in "，、；—"]
    if not cuts:
        mid = len(tx) // 2
        return tx[:mid] + r"\N" + tx[mid:]
    best = min(cuts, key=lambda i: abs(i - len(tx) / 2))
    a, b = tx[:best + 1], tx[best + 1:]
    return a + r"\N" + (wrap_text(b, limit) if len(b) > limit + 2 else b)


def _header(w: int, h: int, styles: str) -> str:
    return (f"[Script Info]\nPlayResX: {w}\nPlayResY: {h}\n"
            f"WrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
            f"[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, "
            f"SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
            f"Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
            f"BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, "
            f"MarginV, Encoding\n{styles}\n[Events]\nFormat: Layer, Start, "
            f"End, Style, Name, MarginL, MarginR, MarginV, Text\n")


def build(segs, w: int, h: int, style: str = "cute", *,
          title: str = None, subtitle: str = None,
          total: float = None) -> str:
    """回傳 .ass 內容。segs = [(start, end, text), ...]"""
    if style not in STYLES:
        raise ValueError(f"不認識的風格：{style}（可用 {', '.join(STYLES)}）")
    vert = h > w
    E = total if total else (segs[-1][1] + 1.0 if segs else 10.0)
    S = h / 1920.0                       # 依畫面高度等比縮放字級

    def fs(px):
        return max(18, int(round(px * S)))

    lines = []
    if style == "plain":
        st = (f"Style: Main,Microsoft JhengHei,{fs(52 if vert else 44)},"
              f"&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,"
              f"100,100,1,0,1,3,2,2,60,60,{int(90 * S)},1\n")
        for a, b, tx in segs:
            lines.append(f"Dialogue: 0,{_t(a)},{_t(b)},Main,,0,0,0,"
                         + wrap_text(tx, 20))

    elif style == "cute":
        st = (f"Style: Cute,Microsoft JhengHei,{fs(72)},&H00FFFFFF,&H00FFFFFF,"
              f"&H00B469FF,&H66000000,-1,0,0,0,100,100,2,0,1,{max(2,int(5*S))},"
              f"2,2,{int(70*S)},{int(70*S)},{int(340*S)},1\n")
        pop = (r"{\fscx70\fscy70\t(0,150,\fscx108\fscy108)"
               r"\t(150,280,\fscx100\fscy100)\fad(80,80)}")
        for a, b, tx in segs:
            lines.append(f"Dialogue: 0,{_t(a)},{_t(b)},Cute,,0,0,0,"
                         + pop + wrap_text(tx, 13))

    elif style == "cinema":
        st = (f"Style: Cine,Microsoft JhengHei,{fs(50)},&H00EFEFEF,&H00FFFFFF,"
              f"&H00000000,&H96000000,0,0,0,0,100,100,6,0,1,2,3,2,"
              f"{int(80*S)},{int(80*S)},{int(250*S)},1\n")
        for a, b, tx in segs:
            lines.append(f"Dialogue: 0,{_t(a)},{_t(b)},Cine,,0,0,0,"
                         r"{\fad(280,280)}" + wrap_text(tx, 18))

    elif style == "neon":
        st = (f"Style: NeonG,Microsoft JhengHei,{fs(68)},&H00FFC8E8,&H00FFFFFF,"
              f"&H00FFC8E8,&H00000000,-1,0,0,0,100,100,4,0,1,{max(4,int(11*S))},"
              f"0,2,{int(70*S)},{int(70*S)},{int(330*S)},1\n"
              f"Style: Neon,Microsoft JhengHei,{fs(68)},&H00FFFFFF,&H00FFFFFF,"
              f"&H00B469FF,&H00000000,-1,0,0,0,100,100,4,0,1,{max(2,int(5*S))},"
              f"0,2,{int(70*S)},{int(70*S)},{int(330*S)},1\n")
        for a, b, tx in segs:
            body = wrap_text(tx, 13)
            lines.append(f"Dialogue: 0,{_t(a)},{_t(b)},NeonG,,0,0,0,"
                         r"{\blur14\fad(150,150)}" + body)
            lines.append(f"Dialogue: 1,{_t(a)},{_t(b)},Neon,,0,0,0,"
                         r"{\fad(150,150)}" + body)

    else:                                # rec
        st = (f"Style: Cute,Microsoft JhengHei,{fs(66)},&H00FFFFFF,&H00FFFFFF,"
              f"&H00B469FF,&H66000000,-1,0,0,0,100,100,2,0,1,{max(2,int(4*S))},"
              f"2,2,{int(60*S)},{int(60*S)},{int(330*S)},1\n"
              f"Style: UI,Consolas,{fs(34)},&H00FFFFFF,&H00FFFFFF,&H00000000,"
              f"&H00000000,-1,0,0,0,100,100,1,0,1,1,1,7,{int(52*S)},"
              f"{int(52*S)},{int(66*S)},1\n"
              f"Style: UIR,Consolas,{fs(34)},&H00FFFFFF,&H00FFFFFF,&H00000000,"
              f"&H00000000,-1,0,0,0,100,100,1,0,1,1,1,9,{int(52*S)},"
              f"{int(52*S)},{int(66*S)},1\n")
        pop = (r"{\fscx70\fscy70\t(0,150,\fscx108\fscy108)"
               r"\t(150,280,\fscx100\fscy100)\fad(80,80)}")
        # 每秒閃一次的 REC 紅點
        for s in range(int(E)):
            lines.append(f"Dialogue: 1,{_t(s)},{_t(min(s + .55, E))},UI,,0,0,0,"
                         r"{\c&H4040FF&}●{\c&HFFFFFF&} REC")
            lines.append(f"Dialogue: 1,{_t(min(s + .55, E))},"
                         f"{_t(min(s + 1., E))},UI,,0,0,0,"
                         r"{\alpha&HFF&}●{\alpha&H00&} REC")
        lines.append(f"Dialogue: 1,{_t(0)},{_t(E)},UIR,,0,0,0,"
                     r"{\c&H7FD34D&}▮▮▮{\c&HFFFFFF&}▯")
        lines.append(f"Dialogue: 1,{_t(0)},{_t(E)},UI,,0,0,0,"
                     rf"{{\an1\pos({int(60*S)},{h - int(64*S)})}}4K 60FPS")
        for a, b, tx in segs:
            lines.append(f"Dialogue: 0,{_t(a)},{_t(b)},Cute,,0,0,0,"
                         + pop + wrap_text(tx, 13))

    # 標題（所有風格通用，給了才加）
    if title:
        st += (f"Style: Ttl,Microsoft JhengHei,{fs(60)},&H00FFFFFF,&H00FFFFFF,"
               f"&H00B469FF,&H00000000,-1,0,0,0,100,100,14,0,1,"
               f"{max(2,int(4*S))},2,8,{int(60*S)},{int(60*S)},{int(150*S)},1\n")
        lines.append(f"Dialogue: 1,{_t(0.6)},{_t(E)},Ttl,,0,0,0,"
                     r"{\fad(500,300)}" + title)
    if subtitle:
        st += (f"Style: STtl,Arial,{fs(30)},&H00FFFFFF,&H00FFFFFF,&H00000000,"
               f"&H00000000,0,0,0,0,100,100,7,0,1,1,0,8,{int(60*S)},"
               f"{int(60*S)},{int(232*S)},1\n")
        lines.append(f"Dialogue: 1,{_t(1.0)},{_t(E)},STtl,,0,0,0,"
                     r"{\fad(500,300)\an8}" + subtitle)

    return _header(w, h, st) + "\n".join(lines) + "\n"


def video_filter(style: str) -> str:
    """該風格要不要對畫面本身做處理（回傳 ffmpeg 濾鏡片段，可能是空字串）。"""
    if style == "cinema":
        # 上下黑邊＋輕微調色，紀錄片感
        return ("eq=saturation=0.96:contrast=1.06,"
                "drawbox=y=0:h=ih*0.09:c=black:t=fill,"
                "drawbox=y=ih*0.91:h=ih*0.09:c=black:t=fill")
    if style == "neon":
        return "eq=saturation=1.12:contrast=1.05"
    return ""


def main():
    if len(sys.argv) < 4:
        sys.exit(__doc__ + "\n單獨產 ass：python voicecut/styles.py "
                           "<字幕.srt> <風格> <輸出.ass> [寬 高]")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import autocut
    srt, style, out = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    w = int(sys.argv[4]) if len(sys.argv) > 4 else 1080
    h = int(sys.argv[5]) if len(sys.argv) > 5 else 1920
    segs = autocut.parse_srt(srt)
    out.write_text(build(segs, w, h, style), encoding="utf-8")
    print(f"{style} 風格 → {out}（{len(segs)} 句，{w}x{h}）")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
