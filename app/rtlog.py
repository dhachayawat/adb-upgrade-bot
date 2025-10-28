# app/rtlog.py
import logging, sys, os, time, threading, queue, io
from logging.handlers import RotatingFileHandler
from collections import deque
from threading import Lock
from typing import Optional, List, Dict, Any

# --- config ---
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_DIR   = os.getenv("LOG_DIR", "/app/logs")
os.makedirs(LOG_DIR, exist_ok=True)

# ---------- root logger -> stdout + file (รวมทุกอย่าง) ----------
_logger = logging.getLogger("adb-upgrade-bot")
_logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
_logger.propagate = False

for h in list(_logger.handlers):
    _logger.removeHandler(h)

fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

sh = logging.StreamHandler(sys.stdout)
sh.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
sh.setFormatter(fmt)
_logger.addHandler(sh)

fh = RotatingFileHandler(os.path.join(LOG_DIR, "app.log"),
                         maxBytes=5_000_000, backupCount=3, encoding="utf-8")
fh.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
fh.setFormatter(fmt)
_logger.addHandler(fh)

# ---------- simple API (text logs) ----------
def i(msg): _logger.info(msg)
def w(msg): _logger.warning(msg)
def e(msg): _logger.error(msg)

# =========================
# In-memory TEXT log buffer (global)
# =========================
_text_buf = deque(maxlen=4000)
_text_lock = Lock()

class _BufHandler(logging.Handler):
    def emit(self, record):
        try:
            line = fmt.format(record)
            with _text_lock:
                _text_buf.append(line)
        except Exception:
            pass

_logger.addHandler(_BufHandler())

def get_text(lines: int = 400) -> str:
    with _text_lock:
        return "\n".join(list(_text_buf)[-max(0, lines):])

# =========================
# Per-device FILE loggers
# =========================
_SAFE_RE = __import__("re").compile(r"[^a-zA-Z0-9_.-]+")

def _safe_dev(dev_id: str) -> str:
    s = (_SAFE_RE.sub("-", str(dev_id or "dev")).strip("-")) or "dev"
    return s[:80]

_dev_loggers: Dict[str, logging.Logger] = {}
_dev_lock = Lock()

def device_log_path(dev_id: str) -> str:
    return os.path.join(LOG_DIR, f"app.log.{_safe_dev(dev_id)}")

def _get_dev_logger(dev_id: str) -> logging.Logger:
    """
    คืน logger เฉพาะ device (เขียนลง logs/app.log.{dev_id})
    - ใช้ RotatingFileHandler 3 ไฟล์เหมือน global
    - ไม่ propagate กลับ root
    """
    sid = _safe_dev(dev_id)
    with _dev_lock:
        lg = _dev_loggers.get(sid)
        if lg:
            return lg
        lg = logging.getLogger(f"adb-upgrade-bot.dev.{sid}")
        lg.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
        lg.propagate = False
        for h in list(lg.handlers):
            lg.removeHandler(h)
        path = device_log_path(sid)
        fh = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        fh.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
        fh.setFormatter(fmt)
        lg.addHandler(fh)
        _dev_loggers[sid] = lg
        return lg

def dev_info(dev_id: str, msg: str):  _get_dev_logger(dev_id).info(msg)
def dev_warn(dev_id: str, msg: str):  _get_dev_logger(dev_id).warning(msg)
def dev_error(dev_id: str, msg: str): _get_dev_logger(dev_id).error(msg)

def get_device_text(dev_id: str, lines: int = 400) -> str:
    """
    อ่าน tail ของไฟล์ logs/app.log.{dev_id}
    ถ้าไฟล์ไม่พบ → คืนสตริงว่าง
    """
    path = device_log_path(dev_id)
    if not os.path.exists(path):
        return ""
    try:
        # tail แบบง่าย ไม่โหลดทั้งไฟล์
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            chunk = 64 * 1024
            data = bytearray()
            while len(data) < 512 * 1024 and f.tell() > 0 and data.count(b"\n") < lines + 4:
                step = min(chunk, f.tell())
                f.seek(-step, os.SEEK_CUR)
                data.extend(f.read(step))
                f.seek(-step, os.SEEK_CUR)
                if f.tell() == 0:
                    break
            text = data.decode("utf-8", errors="replace")
            rows = text.splitlines()
            return "\n".join(rows[-lines:])
    except Exception:
        # ถ้าพลาดก็ fallback แบบอ่านทั้งหมด (ไฟล์เล็ก)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                rows = f.read().splitlines()
                return "\n".join(rows[-lines:])
        except Exception:
            return ""

# =========================
# In-memory WEB-HTML log buffer (สำหรับ Web UI)
# =========================
_WEB_MAX = int(os.getenv("WEB_LOG_MAXLEN", "4000"))
_web_buf: deque = deque(maxlen=_WEB_MAX)
_web_lock = Lock()

def web(dev_id: str, html: str, level: str = "INFO") -> None:
    """
    เพิ่ม HTML log ลงบัฟเฟอร์สำหรับ Web UI + เขียนลงไฟล์ต่อ-device
    """
    try:
        level = (level or "INFO").upper()
        if level not in ("INFO", "WARN", "ERROR"):
            level = "INFO"
        rec = {"ts": time.time(), "dev": dev_id or "", "html": html or "", "lvl": level}
        with _web_lock:
            _web_buf.append(rec)
        # duplicate ลงไฟล์ต่อ-device ในรูปแบบอ่านง่าย
        msg = f"[WEB] {level} {html}"
        if level == "INFO":   dev_info(dev_id, msg)
        elif level == "WARN": dev_warn(dev_id, msg)
        else:                 dev_error(dev_id, msg)
    except Exception as ex:
        _logger.warning(f"[rtlog.web] fail: {ex}; msg={html}")

def _dev_match_flexible(dev: str, want: str) -> bool:
    if not want:
        return True
    a = (dev or "").strip().lower()
    b = (want or "").strip().lower()
    if not a or not b:
        return False
    return a == b or (b in a) or a.endswith(b) or b.endswith(a)

def web_dump(lines: int = 400, device: Optional[str] = None) -> List[Dict[str, Any]]:
    with _web_lock:
        buf = list(_web_buf)
    out: List[Dict[str, Any]] = buf
    if device:
        out = [x for x in buf if _dev_match_flexible(x.get("dev", ""), device)]
    if lines > 0:
        out = out[-lines:]
    return out

def web_clear(device: Optional[str] = None) -> int:
    with _web_lock:
        if not device:
            n = len(_web_buf); _web_buf.clear(); return n
        old = list(_web_buf)
        remain = [x for x in old if not _dev_match_flexible(x.get("dev",""), device)]
        _web_buf.clear()
        for x in remain[-_WEB_MAX:]:
            _web_buf.append(x)
        return len(old) - len(remain)

def web_info(dev_id: str, html: str):  web(dev_id, html, "INFO")
def web_warn(dev_id: str, html: str):  web(dev_id, html, "WARN")
def web_error(dev_id: str, html: str): web(dev_id, html, "ERROR")

# =========================
# MP Bridge (optional; สำหรับ RUN_MODE=mp)
# =========================
_mp_queue: Optional["queue.Queue"] = None
_mp_consumer_thr: Optional[threading.Thread] = None

def _consumer():
    global _mp_queue
    while True:
        try:
            item = _mp_queue.get()
            if item is None:
                break
            if isinstance(item, dict):
                ts = float(item.get("ts", time.time()))
                dev = str(item.get("dev", ""))
                html = str(item.get("html", ""))
                lvl = str(item.get("lvl", "INFO")).upper()
                rec = {"ts": ts, "dev": dev, "html": html, "lvl": lvl if lvl in ("INFO","WARN","ERROR") else "INFO"}
                with _web_lock:
                    _web_buf.append(rec)
                # เขียนลงไฟล์ต่อ-device ด้วย
                msg = f"[WEB] {rec['lvl']} {rec['html']}"
                if rec["lvl"] == "INFO":   dev_info(dev, msg)
                elif rec["lvl"] == "WARN": dev_warn(dev, msg)
                else:                      dev_error(dev, msg)
        except Exception:
            _logger.exception("[rtlog] mp consumer error")

def start_mp_bridge():
    global _mp_queue, _mp_consumer_thr
    if _mp_queue is not None:
        return _mp_queue
    try:
        import multiprocessing as mp
        _mp_queue = mp.Queue(maxsize=int(os.getenv("WEB_LOG_QUEUE_MAX", "2000")))
        _mp_consumer_thr = threading.Thread(target=_consumer, name="rtlog-web-consumer", daemon=True)
        _mp_consumer_thr.start()
        _logger.info("[rtlog] mp bridge started")
    except Exception:
        _logger.exception("[rtlog] start_mp_bridge failed")
        _mp_queue = None
    return _mp_queue

def attach_mp_queue(q):
    try:
        if q is None:
            return
        class _Proxy:
            def __init__(self, qq): self.q = qq
            def send(self, dev, html, lvl):
                try:
                    self.q.put({"ts": time.time(), "dev": dev or "", "html": html or "", "lvl": (lvl or "INFO").upper()}, block=False)
                except Exception:
                    pass
        def _web_to_queue(dev_id: str, html: str, level: str = "INFO"):
            try: _Proxy(q).send(dev_id, html, level)
            except Exception: pass
        globals()["web"] = _web_to_queue
        globals()["web_info"] = lambda d,h: _web_to_queue(d,h,"INFO")
        globals()["web_warn"] = lambda d,h: _web_to_queue(d,h,"WARN")
        globals()["web_error"] = lambda d,h: _web_to_queue(d,h,"ERROR")
    except Exception:
        _logger.exception("[rtlog] attach_mp_queue failed")
