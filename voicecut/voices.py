# -*- coding: utf-8 -*-
"""配音聲音庫：中文 14 種（含方言）＋其他語言 300 多種，可搜尋。

  python voicecut/voices.py            # 中文全部
  python voicecut/voices.py 日文        # 搜尋日文
  python voicecut/voices.py --all      # 全部語言統計
autocut.py 用 --voice 代號／中文別名／完整 ID 都行。
"""
import json
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CACHE = BASE / "voicecut" / "_voices_cache.json"
CACHE_DAYS = 30

# 中文系全部 14 種（edge-tts 實際有的），含官方沒歸類的方言
CHINESE = {
    # 台灣
    "hsiaochen": ("zh-TW-HsiaoChenNeural", "台灣女聲・溫和（預設）"),
    "hsiaoyu":   ("zh-TW-HsiaoYuNeural",   "台灣女聲・活潑"),
    "yunjhe":    ("zh-TW-YunJheNeural",    "台灣男聲・沉穩"),
    # 大陸 普通話
    "xiaoxiao":  ("zh-CN-XiaoxiaoNeural",  "大陸女聲・標準溫暖"),
    "xiaoyi":    ("zh-CN-XiaoyiNeural",    "大陸女聲・甜美活潑"),
    "yunxi":     ("zh-CN-YunxiNeural",     "大陸男聲・陽光年輕"),
    "yunxia":    ("zh-CN-YunxiaNeural",    "大陸男聲・少年感"),
    "yunjian":   ("zh-CN-YunjianNeural",   "大陸男聲・渾厚旁白（體育賽事感）"),
    "yunyang":   ("zh-CN-YunyangNeural",   "大陸男聲・新聞播報"),
    # 大陸 方言
    "xiaobei":   ("zh-CN-liaoning-XiaobeiNeural", "東北話女聲（遼寧）"),
    "xiaoni":    ("zh-CN-shaanxi-XiaoniNeural",   "陝西話女聲"),
    # 香港 粵語
    "hiugaai":   ("zh-HK-HiuGaaiNeural",   "粵語女聲・清亮"),
    "hiumaan":   ("zh-HK-HiuMaanNeural",   "粵語女聲・成熟"),
    "wanlung":   ("zh-HK-WanLungNeural",   "粵語男聲"),
}

# 常用外語（做雙語內容時方便）
FOREIGN = {
    "en-us-f":  ("en-US-AvaNeural",      "英文女聲・美式"),
    "en-us-m":  ("en-US-AndrewNeural",   "英文男聲・美式"),
    "en-gb-f":  ("en-GB-SoniaNeural",    "英文女聲・英式"),
    "ja-f":     ("ja-JP-NanamiNeural",   "日文女聲"),
    "ja-m":     ("ja-JP-KeitaNeural",    "日文男聲"),
    "ko-f":     ("ko-KR-SunHiNeural",    "韓文女聲"),
    "ko-m":     ("ko-KR-InJoonNeural",   "韓文男聲"),
    "th-f":     ("th-TH-PremwadeeNeural", "泰文女聲"),
    "vi-f":     ("vi-VN-HoaiMyNeural",   "越南文女聲"),
    "id-f":     ("id-ID-GadisNeural",    "印尼文女聲"),
}
ALL_CODES = {**CHINESE, **FOREIGN}

ALIASES = {
    "溫和女": "hsiaochen", "女": "hsiaochen", "台女": "hsiaochen",
    "活潑女": "hsiaoyu", "沉穩男": "yunjhe", "男": "yunjhe", "台男": "yunjhe",
    "大陸女": "xiaoxiao", "甜美女": "xiaoyi", "大陸男": "yunxi",
    "少年": "yunxia", "渾厚男": "yunjian", "旁白男": "yunjian",
    "新聞男": "yunyang", "東北女": "xiaobei", "東北話": "xiaobei",
    "陝西女": "xiaoni", "陝西話": "xiaoni",
    "粵語女": "hiumaan", "粵語男": "wanlung", "廣東話": "hiumaan",
    "英文女": "en-us-f", "英文男": "en-us-m", "英式女": "en-gb-f",
    "日文女": "ja-f", "日文男": "ja-m", "韓文女": "ko-f", "韓文男": "ko-m",
    "泰文女": "th-f", "越南女": "vi-f", "印尼女": "id-f",
}

import re
FULL_ID = re.compile(r"[a-z]{2,3}-[A-Z]{2,4}(-[a-z]+)?-\w+Neural")


def resolve(name: str):
    """代號／中文別名／完整 ID → 完整 ID；認不得回傳 None。"""
    if not name:
        return None
    n = name.strip()
    if n in ALIASES:
        n = ALIASES[n]
    low = n.lower()
    if low in ALL_CODES:
        return ALL_CODES[low][0]
    if FULL_ID.fullmatch(n):
        return n
    return None


def fetch_all(force=False):
    """跟 edge-tts 要完整聲音清單（322 種），結果快取 30 天。"""
    if CACHE.exists() and not force:
        age = (time.time() - CACHE.stat().st_mtime) / 86400
        if age < CACHE_DAYS:
            try:
                return json.loads(CACHE.read_text(encoding="utf-8"))
            except Exception:
                pass
    r = subprocess.run([sys.executable, "-m", "edge_tts", "--list-voices"],
                       capture_output=True, encoding="utf-8", errors="replace")
    out = []
    for line in (r.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and FULL_ID.fullmatch(parts[0]):
            out.append({"id": parts[0], "gender": parts[1]})
    if out:
        CACHE.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


def search(keyword: str):
    """在全部聲音裡找。可用語言碼（ja、en-GB）、性別、或中文語言名。"""
    LANG = {"日": "ja-", "日文": "ja-", "英": "en-", "英文": "en-",
            "韓": "ko-", "韓文": "ko-", "中": "zh-", "中文": "zh-",
            "粵": "zh-HK", "台": "zh-TW", "泰": "th-", "越": "vi-",
            "法": "fr-", "德": "de-", "西": "es-", "印尼": "id-"}
    k = LANG.get(keyword, keyword)
    return [v for v in fetch_all() if k.lower() in v["id"].lower()]


def print_curated():
    print("【中文系 14 種】--voice 用代號或中文別名")
    for code, (vid, desc) in CHINESE.items():
        alias = [a for a, c in ALIASES.items() if c == code]
        print(f"  {code:11s} {desc:22s} {vid}")
        if alias:
            print(f"              別名：{' / '.join(alias)}")
    print("\n【常用外語 10 種】")
    for code, (vid, desc) in FOREIGN.items():
        print(f"  {code:11s} {desc:16s} {vid}")
    total = len(fetch_all())
    print(f"\n（edge-tts 全部共 {total} 種聲音，"
          f"搜尋：python voicecut/voices.py 日文｜也可直接給完整 ID）")


def main():
    args = [a for a in sys.argv[1:] if a != "--all"]
    if "--all" in sys.argv:
        allv = fetch_all()
        by = {}
        for v in allv:
            by.setdefault(v["id"].split("-")[0], 0)
            by[v["id"].split("-")[0]] += 1
        print(f"全部 {len(allv)} 種，涵蓋 {len(by)} 種語言：")
        for lang, n in sorted(by.items(), key=lambda x: -x[1])[:25]:
            print(f"  {lang:6s} {n:3d} 種")
        return
    if args:
        hits = search(args[0])
        print(f"「{args[0]}」找到 {len(hits)} 種：")
        for v in hits[:40]:
            print(f"  {v['id']:38s} {v['gender']}")
        if len(hits) > 40:
            print(f"  …還有 {len(hits) - 40} 種")
        return
    print_curated()


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
