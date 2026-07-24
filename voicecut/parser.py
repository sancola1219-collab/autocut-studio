# -*- coding: utf-8 -*-
"""口說命令解析：把一句中文 + 當下時間點 → 剪輯操作 op。
保守策略：只在高信心時解析，其餘回 type='unknown' 交給 Claude 解讀。

op 欄位：
  type   cut / speed / mute / volume / subtitle / mark_start / unknown
  start, end  秒（None 表示待定）
  factor 速度倍率（speed）
  gain   音量倍率（volume）
  text   字幕文字，或 unknown 的原句
  raw    原始整句
  at     講這句話時的影片時間點
"""
import re

CN_NUM = {"零": 0, "一": 1, "兩": 2, "二": 2, "三": 3, "四": 4, "五": 5,
          "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _num(s: str):
    """把一段字串裡的數字抓出來（阿拉伯數字或簡單中文數字），抓不到回 None。"""
    if s is None:
        return None
    s = s.strip()
    m = re.search(r"\d+(?:\.\d+)?", s)
    if m:
        return float(m.group())
    if "半" in s and not re.search(r"[一二兩三四五六七八九十]", s):
        return 0.5
    # 中文數字：十、十五、二十、兩、五…（夠日常用就好）
    m = re.search(r"[一二兩三四五六七八九十]+", s)
    if not m:
        return None
    t = m.group()
    if t == "十":
        return 10.0
    if t.startswith("十"):
        return float(10 + CN_NUM.get(t[1:], 0))
    if "十" in t:
        a, _, b = t.partition("十")
        return float(CN_NUM.get(a, 1) * 10 + (CN_NUM.get(b, 0) if b else 0))
    return float(CN_NUM.get(t, 0)) or None


def _op(t, raw, at, **kw):
    d = {"type": t, "start": None, "end": None, "factor": None,
         "gain": None, "text": None, "raw": raw, "at": round(at, 3)}
    d.update(kw)
    return d


# 每條規則：(正則, 產生 op 的函式)。由上而下，先命中先贏。
def parse(text: str, at: float, pending_start=None):
    """回傳 op。pending_start：先前『從這裡開始』標記的時間點（沒有給 None）。"""
    raw = text.strip()
    t = re.sub(r"[，。！？、,.!?\s]+$", "", raw)   # 去尾標點
    t = t.replace("秒鐘", "秒")

    def clamp0(x):
        return max(0.0, x)

    # ---- 字幕：這裡加字幕XXX / 字幕：XXX / 加個字幕XXX ----
    m = re.search(r"(?:加|上|打)?\s*字幕\s*[:：]?\s*(.+)$", t)
    if m and m.group(1).strip():
        return _op("subtitle", raw, at, start=clamp0(at), end=clamp0(at) + 3.0,
                   text=m.group(1).strip())

    # ---- 範圍起點：從這裡開始 / 這裡開始 / 開始剪 / 標記 ----
    if re.search(r"(從這(裡|邊)|這(裡|邊))?\s*(開始|起)(剪|切|標記|標)?$", t) \
            or t in ("開始", "起點", "標記", "從這裡", "從這邊"):
        return _op("mark_start", raw, at, start=clamp0(at))

    # ---- 剪掉前面 N 秒 ----
    m = re.search(r"剪掉?|刪掉?|切掉?", t)
    is_cut = bool(m)
    m = re.search(r"(?:剪|刪|切)掉?.*前面\s*([0-9一二兩三四五六七八九十.]+)\s*秒", t)
    if m:
        n = _num(m.group(1)) or 0
        return _op("cut", raw, at, start=clamp0(at - n), end=clamp0(at))
    # ---- 剪掉後面 / 接下來 N 秒 ----
    m = re.search(r"(?:剪|刪|切)掉?.*(?:後面|接下來|之後)\s*([0-9一二兩三四五六七八九十.]+)\s*秒", t)
    if m:
        n = _num(m.group(1)) or 0
        return _op("cut", raw, at, start=clamp0(at), end=clamp0(at + n))
    # ---- 剪掉 N 秒到 M 秒 ----
    m = re.search(r"(?:剪|刪|切)掉?\s*([0-9一二兩三四五六七八九十.]+)\s*秒?\s*(?:到|至)\s*([0-9一二兩三四五六七八九十.]+)\s*秒", t)
    if m:
        a, b = _num(m.group(1)), _num(m.group(2))
        if a is not None and b is not None:
            return _op("cut", raw, at, start=clamp0(min(a, b)), end=clamp0(max(a, b)))
    # ---- 剪到這裡 / 到這裡（配合先前的 mark_start）----
    if re.search(r"(剪|切)?\s*(到|至)這(裡|邊)$", t) or t in ("到這裡", "到這邊", "結束"):
        if pending_start is not None:
            s, e = sorted((pending_start, at))
            return _op("cut", raw, at, start=clamp0(s), end=clamp0(e))
        # 沒有起點就當作『剪掉開頭到這裡』
        return _op("cut", raw, at, start=0.0, end=clamp0(at))
    # ---- 剪掉這段（配合 mark_start）----
    if is_cut and re.search(r"這(段|裡|邊)", t):
        if pending_start is not None:
            s, e = sorted((pending_start, at))
            return _op("cut", raw, at, start=clamp0(s), end=clamp0(e))

    # ---- 加速 / 放慢：這段加快 N 倍 / 放慢一半（用 mark_start 當範圍）----
    fast = re.search(r"(加快|加速|快轉|快一點)", t)
    slow = re.search(r"(放慢|變慢|慢動作|慢一點)", t)
    if fast or slow:
        # 只認「N倍」旁邊的數字，不要掃整句（「第二段加快三倍」不該抓到二）
        m2 = re.search(r"([0-9一二兩三四五六七八九十.]+)\s*倍", t)
        factor = _num(m2.group(1)) if m2 else (0.5 if "半" in t else None)
        if fast:
            # 要求 >1 才採用，「快一點」「加快一倍」退回預設而不是產生空操作
            factor = factor if factor and factor > 1 else 2.0
        else:  # 放慢：一半→0.5、兩倍慢→0.5
            factor = (1.0 / factor) if factor and factor > 1 else (factor if factor and factor < 1 else 0.5)
        s = pending_start if pending_start is not None else clamp0(at - 3)
        e = at if pending_start is not None else clamp0(at + 3)
        s, e = sorted((clamp0(s), clamp0(e)))
        return _op("speed", raw, at, start=s, end=e, factor=round(factor, 3))

    # ---- 靜音 ----
    if re.search(r"(靜音|消音|把聲音關|沒有聲音)", t):
        if pending_start is not None:
            s, e = sorted((pending_start, at))
        else:
            s, e = clamp0(at), clamp0(at + 3)
        return _op("mute", raw, at, start=clamp0(s), end=clamp0(e))

    # ---- 大聲 / 小聲 ----
    if re.search(r"(大聲|音量.*大|聲音.*大)", t):
        s, e = (pending_start, at) if pending_start is not None else (clamp0(at), clamp0(at + 3))
        s, e = sorted((clamp0(s), clamp0(e)))
        return _op("volume", raw, at, start=s, end=e, gain=1.6)
    if re.search(r"(小聲|音量.*小|聲音.*小)", t):
        s, e = (pending_start, at) if pending_start is not None else (clamp0(at), clamp0(at + 3))
        s, e = sorted((clamp0(s), clamp0(e)))
        return _op("volume", raw, at, start=s, end=e, gain=0.4)

    # ---- 聽不懂：交給 Claude ----
    return _op("unknown", raw, at, text=raw)


OP_LABEL = {
    "cut": "剪掉", "speed": "變速", "mute": "靜音", "volume": "調音量",
    "subtitle": "字幕", "mark_start": "標記起點", "unknown": "待解讀",
}


def describe(op) -> str:
    """給 UI 顯示的一句人話。"""
    def ts(x):
        if x is None:
            return "?"
        m, s = divmod(int(x), 60)
        return f"{m:02d}:{s:02d}"
    t = op["type"]
    if t == "cut":
        return f"剪掉 {ts(op['start'])}–{ts(op['end'])}"
    if t == "speed":
        f = op["factor"]
        word = f"加快 {f:g} 倍" if f and f >= 1 else f"放慢到 {f:g} 倍"
        return f"{ts(op['start'])}–{ts(op['end'])} {word}"
    if t == "mute":
        return f"{ts(op['start'])}–{ts(op['end'])} 靜音"
    if t == "volume":
        return f"{ts(op['start'])}–{ts(op['end'])} 音量 x{op['gain']:g}"
    if t == "subtitle":
        return f"{ts(op['start'])} 字幕：{op['text']}"
    if t == "mark_start":
        return f"標記起點 {ts(op['start'])}（等『到這裡』）"
    return f"待解讀：{op['raw']}"
