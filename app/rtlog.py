# app/rtlog.py
import logging, sys, os, time
from logging.handlers import RotatingFileHandler
from collections import deque
from threading import Lock
from typing import Optional, List, Dict, Any

# --- config ---
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_DIR   = os.getenv("LOG_DIR", "/app/logs")
os.makedirs(LOG_DIR, exist_ok=True)

# --- root logger -> stdout + file ---
_logger = logging.getLogger("adb-upgrade-bot")
_logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
_logger.propagate = False  # กันซ้ำ

# clear old handlers (กรณี reload)
for h in list(_logger.handlers):
    _logger.removeHandler(h)

fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

# stdout
sh = logging.StreamHandler(sys.stdout)
sh.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
sh.setFormatter(fmt)
_logger.addHandler(sh)

# rotating file
fh = RotatingFileHandler(os.path.join(LOG_DIR, "app.log"),
                         maxBytes=5_000_000, backupCount=3, encoding="utf-8")
fh.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
fh.setFormatter(fmt)
_logger.addHandler(fh)

# --- simple API (text logs) ---
def i(msg): _logger.info(msg)
def w(msg): _logger.warning(msg)
def e(msg): _logger.error(msg)

# =========================
# In-memory TEXT log buffer (for /api/logs?plain)
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
# In-memory WEB-HTML log buffer (สำหรับ Web UI สี/ฟอร์แมต)
# =========================
# โครงสร้างบันทึก: {"ts": float, "dev": str, "html": str, "lvl": "INFO|WARN|ERROR"}
_WEB_MAX = int(os.getenv("WEB_LOG_MAXLEN", "4000"))
_web_buf: deque = deque(maxlen=_WEB_MAX)
_web_lock = Lock()

def web(dev_id: str, html: str, level: str = "INFO") -> None:
    """
    เพิ่ม HTML log ลงบัฟเฟอร์สำหรับ Web UI
    - dev_id: อ้างอิงอุปกรณ์/การรัน
    - html: สตริง HTML พร้อมสไตล์ (เช่น <span style="color:#e53935">4</span>)
    - level: "INFO" | "WARN" | "ERROR" (เอาไว้ให้ UI ใส่สีกรอบ/ไอคอน)
    """
    try:
        level = (level or "INFO").upper()
        if level not in ("INFO", "WARN", "ERROR"):
            level = "INFO"
        rec = {
            "ts": time.time(),
            "dev": dev_id or "",
            "html": html or "",
            "lvl": level
        }
        with _web_lock:
            _web_buf.append(rec)
    except Exception as ex:
        # fallback ไป text log ถ้ามีอะไรพลาด
        _logger.warning(f"[rtlog.web] fail: {ex}; msg={html}")

def web_dump(lines: int = 400, device: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    ดึง HTML logs ล่าสุดสำหรับ Web UI
    - lines: จำนวนรายการล่าสุด (ดีฟอลต์ 400)
    - device: ถ้ากำหนด จะกรองเฉพาะ dev_id นั้น
    คืนค่า: list ของ dict [{ts, dev, html, lvl}, ...] เรียงเวลาจากเก่าไปใหม่
    """
    with _web_lock:
        buf = list(_web_buf)
    if device:
        buf = [x for x in buf if x.get("dev") == device]
    if lines > 0:
        buf = buf[-lines:]
    # เรียงเวลาจากเก่า -> ใหม่ (deque อยู่แล้ว แต่กันกรณี lines slice)
    return buf

def web_clear(device: Optional[str] = None) -> int:
    """
    ล้าง HTML logs:
    - ไม่ระบุ device: ล้างทั้งหมด
    - ระบุ device: ล้างเฉพาะของ dev นั้น
    คืนค่า: จำนวนที่ถูกลบ
    """
    with _web_lock:
        if not device:
            n = len(_web_buf)
            _web_buf.clear()
            return n
        # ล้างเฉพาะบาง dev
        old = list(_web_buf)
        remain = [x for x in old if x.get("dev") != device]
        # เขียนกลับ (เคลียร์ + extend)
        _web_buf.clear()
        for x in remain[-_WEB_MAX:]:
            _web_buf.append(x)
        return len(old) - len(remain)

# =========================
# Sugar helpers (optional)
# =========================
def web_info(dev_id: str, html: str):  web(dev_id, html, "INFO")
def web_warn(dev_id: str, html: str):  web(dev_id, html, "WARN")
def web_error(dev_id: str, html: str): web(dev_id, html, "ERROR")
