# -*- coding: utf-8 -*-
"""口說剪片台 — 本機網頁面板後端（Python 內建伺服器，零額外安裝）。
啟動：python voicecut/server.py   然後瀏覽器開 http://127.0.0.1:8765
"""
import json
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
import autocut
from voicecut import parser as P
from voicecut import engine as E

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
OUT_DIR = BASE / "輸出"
MUSIC_DIR = BASE / "背景音樂"
MEDIA_DIR = BASE / "素材"
HOST, PORT = "127.0.0.1", 8765

_state = {"video": None, "duration": 0.0, "w": 0, "h": 0}
_whisper = {"model": None}
_whisper_lock = threading.Lock()
_job = {"running": False, "progress": "", "output": None, "error": None}
_job_lock = threading.Lock()


# --------------------------------------------------------------- whisper

def get_whisper():
    if _whisper["model"] is None:
        with _whisper_lock:             # 併發辨識時只載入一份模型
            if _whisper["model"] is None:
                from faster_whisper import WhisperModel
                # medium 已下載、辨識準；命令句短，載一次重複用
                try:
                    _whisper["model"] = WhisperModel("medium", device="auto",
                                                     compute_type="auto")
                except Exception:
                    _whisper["model"] = WhisperModel("medium", device="cpu",
                                                     compute_type="int8")
    return _whisper["model"]


def transcribe_clip(wav: Path) -> str:
    model = get_whisper()
    seg_iter, _ = model.transcribe(
        str(wav), language="zh", beam_size=5,
        vad_filter=True, condition_on_previous_text=False,
        initial_prompt="以下是繁體中文的剪輯口令。")
    txt = "".join(s.text for s in seg_iter).strip()
    return autocut.to_traditional(txt)


# --------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", ctype="application/json; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _read_body(self):
        n = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(n) if n else b""

    # ---- GET ----
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            html = (HERE / "panel.html").read_bytes()
            return self._send(200, html, "text/html; charset=utf-8")
        if u.path == "/state":
            return self._json({**_state, "video": str(_state["video"]) if _state["video"] else None,
                               "music": [p.name for p in _list_music()]})
        if u.path == "/library":
            return self._json({"videos": _list_media(), "dir": str(MEDIA_DIR)})
        if u.path == "/video":
            return self._serve_video()
        if u.path == "/render_status":
            return self._json(_job)
        if u.path == "/output":
            if _job["output"] and Path(_job["output"]).exists():
                return self._serve_file(Path(_job["output"]), "video/mp4")
            return self._json({"error": "尚無輸出"}, 404)
        return self._json({"error": "not found"}, 404)

    # ---- POST ----
    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/open":
            data = json.loads(self._read_body() or b"{}")
            return self._open_video(data.get("path", ""))
        if u.path == "/parse":
            data = json.loads(self._read_body() or b"{}")
            op = P.parse(data.get("text", ""), float(data.get("at", 0)),
                         pending_start=data.get("pending_start"))
            return self._json({"op": op, "desc": P.describe(op)})
        if u.path == "/transcribe":
            at = float(q.get("at", [0])[0])
            pending = q.get("pending_start", [None])[0]
            pending = float(pending) if pending not in (None, "", "null") else None
            return self._transcribe(self._read_body(), at, pending)
        if u.path == "/render":
            data = json.loads(self._read_body() or b"{}")
            return self._render(data)
        return self._json({"error": "not found"}, 404)

    # ---- 影片開啟 ----
    def _open_video(self, path):
        p = Path(path.strip().strip('"')).expanduser().resolve()
        if not p.exists():
            return self._json({"error": f"找不到檔案：{p}"}, 400)
        try:
            w, h, dur, _ = autocut.probe_video(p)
        except Exception as e:
            return self._json({"error": f"讀不到影片：{e}"}, 400)
        _state.update(video=p, duration=dur, w=w, h=h)
        return self._json({"ok": True, "name": p.name, "duration": dur, "w": w, "h": h})

    # ---- 串流影片（支援 Range，才能拖進度）----
    def _serve_video(self):
        p = _state["video"]
        if not p or not Path(p).exists():
            return self._json({"error": "尚未開啟影片"}, 404)
        self._serve_file(Path(p), "video/mp4")

    def _serve_file(self, p: Path, ctype):
        size = p.stat().st_size
        rng = self.headers.get("Range")
        start = end = None
        if rng and rng.startswith("bytes=") and "," not in rng:   # 不支援多段
            try:
                s, _, e = rng[6:].partition("-")
                if s == "":                       # suffix：要最後 e 個 bytes
                    start, end = max(0, size - int(e)), size - 1
                else:
                    start = int(s)
                    end = min(int(e), size - 1) if e else size - 1
            except ValueError:                    # 格式怪就當沒帶 Range
                start = end = None

        if start is not None:
            if start >= size or start > end:      # 要不到的區間 → 416
                self.send_response(416)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            length = end - start + 1
            self.send_response(206)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Content-Length", str(length))
            self.end_headers()
            with open(p, "rb") as f:
                f.seek(start)
                remaining = length
                try:
                    while remaining > 0:
                        chunk = f.read(min(65536, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                except (ConnectionError, BrokenPipeError):
                    return          # 使用者拖進度／關分頁，正常現象
        else:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with open(p, "rb") as f:
                try:
                    while True:
                        chunk = f.read(65536)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                except (ConnectionError, BrokenPipeError):
                    return

    # ---- 語音轉文字＋解析 ----
    def _transcribe(self, audio: bytes, at: float, pending):
        if not audio:
            return self._json({"error": "沒收到聲音"}, 400)
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            raw = Path(td) / "in.webm"
            raw.write_bytes(audio)
            wav = Path(td) / "in.wav"
            try:
                r = subprocess.run(
                    ["ffmpeg", "-y", "-v", "error", "-i", str(raw),
                     "-ar", "16000", "-ac", "1", str(wav)],
                    capture_output=True, encoding="utf-8", errors="replace")
            except (FileNotFoundError, OSError):
                return self._json(
                    {"error": "找不到 ffmpeg，請確認已安裝並加入 PATH"}, 500)
            if r.returncode != 0 or not wav.exists():
                return self._json({"error": "聲音轉檔失敗"}, 400)
            try:
                text = transcribe_clip(wav)
            except Exception as e:
                return self._json({"error": f"辨識失敗：{e}"}, 500)
        if not text:
            return self._json({"text": "", "op": None, "desc": "沒聽到內容"})
        op = P.parse(text, at, pending_start=pending)
        return self._json({"text": text, "op": op, "desc": P.describe(op)})

    # ---- 算圖 ----
    def _render(self, data):
        if not _state["video"]:
            return self._json({"error": "尚未開啟影片"}, 400)
        ops = [o for o in data.get("ops", []) if o.get("type") != "unknown"]
        if not ops:
            return self._json({"error": "沒有可套用的操作"}, 400)
        opts = data.get("options", {})
        burn = bool(opts.get("subs", True))
        # 檢查與佔位要在同一把鎖內，否則連點會同時開兩條 ffmpeg 寫同一個檔
        with _job_lock:
            if _job["running"]:
                return self._json({"error": "正在算圖中，請稍候"}, 409)
            _job.update(running=True, progress="開始…", output=None, error=None)
        video = Path(_state["video"])
        OUT_DIR.mkdir(exist_ok=True)
        out_path = OUT_DIR / f"{video.stem}_口說剪片.mp4"

        def work():
            try:
                import tempfile
                with tempfile.TemporaryDirectory(prefix="voicecut_") as td:
                    # 舊檔被鎖住時實際存成的檔名可能不同，用回傳值才對
                    real = E.apply_ops(video, ops, out_path, Path(td),
                                       burn_subs=burn,
                                       progress=lambda m: _job.update(progress=m))
                _job.update(output=str(real), progress="完成")
            except Exception as e:
                _job.update(error=str(e), progress="失敗")
            finally:
                _job.update(running=False)

        threading.Thread(target=work, daemon=True).start()
        return self._json({"ok": True})


def _list_music():
    if not MUSIC_DIR.exists():
        return []
    return sorted(p for p in MUSIC_DIR.iterdir()
                  if p.suffix.lower() in autocut.MUSIC_EXTS)


def _list_media(limit=50):
    """素材資料夾的影片（含子資料夾），新→舊，給面板的素材庫用。"""
    from datetime import datetime
    if not MEDIA_DIR.exists():
        return []
    vids = [p for p in MEDIA_DIR.rglob("*")
            if p.is_file() and p.suffix.lower() in autocut.VIDEO_EXTS]
    vids.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for p in vids[:limit]:
        st = p.stat()
        out.append({
            "name": p.name, "path": str(p),
            "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%m-%d %H:%M"),
            "size": f"{st.st_size / 1048576:.0f}MB",
        })
    return out


def main():
    OUT_DIR.mkdir(exist_ok=True)
    MUSIC_DIR.mkdir(exist_ok=True)
    MEDIA_DIR.mkdir(exist_ok=True)
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}"
    print("=" * 46)
    print("  口說剪片台已啟動")
    print(f"  請用瀏覽器開啟： {url}")
    print("  關閉：在這個視窗按 Ctrl+C")
    print("=" * 46)
    if "--no-open" not in sys.argv:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已關閉，掰掰～")


if __name__ == "__main__":
    main()
