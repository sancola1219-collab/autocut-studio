# -*- coding: utf-8 -*-
"""轉場庫：ffmpeg xfade 全部 58 種，分組、中文別名、隨機／輪播模式。

以前只有「淡入淡出」跟「直接相接」兩種，現在整組開放：

  python autocut.py 片1.mp4 片2.mp4 --transition slideleft
  python autocut.py *.mp4 --transition 隨機          # 每個接點都不一樣
  python autocut.py *.mp4 --transition 推入          # 從「推入類」裡隨機挑
  python voicecut/transitions.py                     # 列出全部

技術重點：xfade 會吃掉重疊的時間，所以每段先用 tpad 把尾巴補回 duration 秒，
接起來以後每段「內容的起點」時間才不會位移——旁白對位才不會跑掉。
"""
import random
import subprocess
import sys
from pathlib import Path

# 依感覺分組（名稱→中文說明）
GROUPS = {
    "淡出": {
        "fade": "標準淡入淡出", "fadeblack": "轉黑再進", "fadewhite": "轉白再進",
        "fadefast": "快淡", "fadeslow": "慢淡", "fadegrays": "褪成灰再進",
        "dissolve": "溶解", "distance": "距離感溶解",
    },
    "推入": {
        "slideleft": "向左推", "slideright": "向右推",
        "slideup": "向上推", "slidedown": "向下推",
        "coverleft": "由左覆蓋", "coverright": "由右覆蓋",
        "coverup": "由上覆蓋", "coverdown": "由下覆蓋",
        "revealleft": "向左揭開", "revealright": "向右揭開",
        "revealup": "向上揭開", "revealdown": "向下揭開",
    },
    "擦除": {
        "wipeleft": "向左擦", "wiperight": "向右擦",
        "wipeup": "向上擦", "wipedown": "向下擦",
        "wipetl": "左上角擦", "wipetr": "右上角擦",
        "wipebl": "左下角擦", "wipebr": "右下角擦",
        "smoothleft": "柔邊左擦", "smoothright": "柔邊右擦",
        "smoothup": "柔邊上擦", "smoothdown": "柔邊下擦",
        "diagtl": "左上斜擦", "diagtr": "右上斜擦",
        "diagbl": "左下斜擦", "diagbr": "右下斜擦",
    },
    "幾何": {
        "circlecrop": "圓形收合", "rectcrop": "方形收合",
        "circleopen": "圓形展開", "circleclose": "圓形閉合",
        "vertopen": "垂直開門", "vertclose": "垂直關門",
        "horzopen": "水平開門", "horzclose": "水平關門",
        "radial": "雷達掃描", "zoomin": "推近進場",
        "squeezeh": "水平擠壓", "squeezev": "垂直擠壓",
    },
    "特效": {
        "pixelize": "馬賽克化", "hblur": "橫向模糊",
        "hlslice": "左切片", "hrslice": "右切片",
        "vuslice": "上切片", "vdslice": "下切片",
        "hlwind": "左風吹", "hrwind": "右風吹",
        "vuwind": "上風吹", "vdwind": "下風吹",
    },
}
ALL = {name: desc for g in GROUPS.values() for name, desc in g.items()}

# 中文別名（群組名也可直接當別名用＝從該組隨機挑）
ALIASES = {
    "淡入淡出": "fade", "淡出": "fade", "溶解": "dissolve",
    "轉黑": "fadeblack", "轉白": "fadewhite",
    "左推": "slideleft", "右推": "slideright", "上推": "slideup", "下推": "slidedown",
    "圓形": "circleopen", "開門": "horzopen", "雷達": "radial",
    "馬賽克": "pixelize", "模糊": "hblur", "推近": "zoomin",
    "無": "none", "直接": "none", "硬切": "none",
    "隨機": "random", "亂數": "random",
}
SPECIAL = ("none", "random")

# 適合快節奏短影音的挑選池（隨機模式偏好這些，不會挑到太花的）
NICE = ["fade", "dissolve", "slideleft", "slideright", "slideup",
        "coverleft", "coverup", "smoothleft", "smoothright",
        "circleopen", "zoomin", "wipeleft", "fadeblack", "pixelize"]


def resolve(name: str):
    """把使用者輸入轉成 (模式, 值)。模式＝none/random/group/fixed。"""
    if not name:
        return ("fixed", "fade")
    n = name.strip()
    n = ALIASES.get(n, n)
    if n in SPECIAL:
        return (n, None)
    if n in GROUPS:
        return ("group", n)
    if n in ALL:
        return ("fixed", n)
    raise ValueError(
        f"不認識的轉場：{name}\n"
        f"可用：{', '.join(sorted(ALL))}\n"
        f"或群組名：{', '.join(GROUPS)}；或 隨機 / 無")


def picker(name: str, seed: int = None):
    """回傳一個 f(i) → 轉場名稱 的函式（i 是第幾個接點）。"""
    mode, val = resolve(name)
    if mode == "none":
        return None
    rng = random.Random(seed if seed is not None else 20260731)
    if mode == "random":
        return lambda i: rng.choice(NICE)
    if mode == "group":
        pool = sorted(GROUPS[val])
        return lambda i: rng.choice(pool)
    return lambda i: val


def build_chain(n_clips: int, durs, dur: float, pick, label_in="v"):
    """組出 xfade 濾鏡鏈。

    durs：每段「內容」長度（不含為了轉場補的 tpad）。
    回傳 (filter_complex 片段清單, 最後輸出的 label, 總長)。
    """
    fc, prev = [], f"[0:{label_in}]"
    offset = 0.0
    for i in range(1, n_clips):
        offset += durs[i - 1]
        out = f"[x{i}]"
        fc.append(f"{prev}[{i}:{label_in}]xfade=transition={pick(i - 1)}:"
                  f"duration={dur:.3f}:offset={offset:.3f}{out}")
        prev = out
    return fc, prev, sum(durs)


def print_all():
    print(f"轉場總共 {len(ALL)} 種，分成 {len(GROUPS)} 類：\n")
    for g, items in GROUPS.items():
        print(f"【{g}】（--transition {g} ＝從這類隨機挑）")
        line = []
        for name, desc in items.items():
            line.append(f"{name}({desc})")
            if len(line) == 3:
                print("   " + "  ".join(line))
                line = []
        if line:
            print("   " + "  ".join(line))
        print()
    print("特殊：隨機（每個接點都不同）、無（直接硬切）")
    print("中文別名：" + "、".join(f"{k}={v}" for k, v in list(ALIASES.items())[:12]) + " …")


def available():
    """問這台 ffmpeg 實際支援哪些（版本不同會有差）。"""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-h", "filter=xfade"],
                       capture_output=True, encoding="utf-8", errors="replace")
    got = set()
    for line in (r.stdout or "").splitlines():
        s = line.strip().split()
        if len(s) >= 2 and s[0].isalpha() and s[1].lstrip("-").isdigit():
            got.add(s[0])
    return got


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if "--check" in sys.argv:
        have = available()
        missing = sorted(set(ALL) - have)
        print(f"這台 ffmpeg 支援 {len(have)} 種；清單裡缺 {len(missing)} 種"
              + (f"：{missing}" if missing else "（全部支援）"))
    else:
        print_all()
