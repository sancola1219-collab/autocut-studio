#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把丟進「素材」資料夾的影片按拍攝時間自動歸檔標註：

    素材/隨手丟.mp4  →  素材/2026-07/2026-07-25_1430_隨手丟.mp4

・時間優先抓影片內建的拍攝時間（手機拍的都有），抓不到才用檔案時間
・檔名已有日期前綴的（整理過的）不會再動
・只整理「素材」根目錄的檔案，子資料夾不碰
"""
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
MEDIA_DIR = BASE / "素材"
EXTS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm",
        ".ts", ".mts", ".wmv", ".flv"}
DATED = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{4}_")


def shot_time(p: Path) -> datetime:
    """優先抓影片內建拍攝時間，抓不到用檔案修改時間。"""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries",
             "format_tags=creation_time", "-of", "csv=p=0", str(p)],
            capture_output=True, encoding="utf-8", errors="replace", timeout=15)
        s = (r.stdout or "").strip()
        if s:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone()
    except Exception:
        pass
    return datetime.fromtimestamp(p.stat().st_mtime)


def main():
    MEDIA_DIR.mkdir(exist_ok=True)
    loose = [p for p in MEDIA_DIR.iterdir()
             if p.is_file() and p.suffix.lower() in EXTS
             and not DATED.match(p.name)]
    if not loose:
        print("「素材」資料夾沒有需要整理的新影片～")
        print(f"（把影片丟進 {MEDIA_DIR} 再跑一次）")
        return
    for p in sorted(loose, key=lambda x: x.stat().st_mtime):
        dt = shot_time(p)
        folder = MEDIA_DIR / f"{dt:%Y-%m}"
        folder.mkdir(exist_ok=True)
        target = folder / f"{dt:%Y-%m-%d_%H%M}_{p.name}"
        i = 2
        while target.exists():
            target = folder / f"{dt:%Y-%m-%d_%H%M}_{p.stem}_{i}{p.suffix}"
            i += 1
        p.rename(target)
        print(f"  {p.name}  →  {target.relative_to(MEDIA_DIR)}")
    print(f"\n整理完成，共 {len(loose)} 個檔案，都標好時間收進月份資料夾了。")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
