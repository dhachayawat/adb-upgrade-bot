# rtlog.py  — per-device logs use canonical "id" (e.g., app.log.d1)
# -------------------------------------------------------------------
from __future__ import annotations

import os
import sys
import io
import time
import threading
import logging
import queue as _queue
from collections import deque
from logging.handlers import RotatingFileHandler
from typing import Optional, Dict, Any, List

# ---------------------- Global config ----------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_DIR = os.getenv("LOG_DIR", "./logs")
os.makedirs(LOG_DIR, exist_ok=True)

MAX_BYTES = int(os.getenv("LOG_ROTATE_MAX_BYTES", str(5_000_000)))
BACKUP_COUNT = int(os.getenv("LOG_ROTATE_BACKUP_COUNT", "3"))

# trace helper (toggle with LOG_TRACE_DEVID=1)
_LOG_TRACE_DEVID = os.getenv("LOG_TRACE_DEVID", "0") == "1"
def _trace(msg: str):
    if _LOG_TRACE_DEVID:
        _root.info(f"[trace-device-id] {msg}")

# ---------------------- Root logger ------------------------
_root = logging.getLogger("adb-upgrade-bot")
_root.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
_root.propagate = False

for h in list(_root.handlers):
    _root.removeHandler(h)

_fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

_sh = logging.StreamHandler(sys.stdout)
_sh.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
_sh.setFormatter(_fmt)
_root.addHandler(_sh)

_fh = RotatingFileHandler(
    os.path.join(LOG_DIR, "app.log"),
    maxBytes=MAX_BYTES,
    backupCount=BACKUP_COUNT,
    encoding="utf-8",
)
_fh.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
_fh.setFormatter(_fmt)
_root.addHandler(_fh)

# --------------------- Canonical device-id registry -----------------
# จุดประสงค์: map alias/serial/host:port => canonical id (เช่น "d1")
# ตัวอย่าง: {"d1":"d1","ดาบ100":"d1","host.docker.internal:5565":"d1"}
_CANON_LOCK = threading.Lock()
_CANON: Dict[str, str] = {}   # key(normalized) -> canonical_id("d1")

def _norm_key(s: str) -> str:
    return (s or "").strip().lower().replace("\\", "_").replace("/", "_")

def _sanitize_dev_id(s: str) -> str:
    # ใช้ทำชื่อไฟล์เท่านั้น (ไม่กระทบ canonical id ซึ่งควรจะเป็น "d1")
    return str(s).replace(":", "-").replace("/", "_").replace("\\", "_").strip()

def set_canonical_map(mapping: Dict[str, str]) -> None:
    """ตั้งค่า mapping ครั้งเดียวเป็นก้อน (เช่นจาก device.json)"""
    with _CANON_LOCK:
        _CANON.clear()
        for k, v in (mapping or {}).items():
            if not k or not v:
                continue
            _CANON[_norm_key(k)] = str(v).strip()
        # ใส่ self-map ของทุก canonical id ด้วย
        for v in list(set(_CANON.values())):
            _CANON[_norm_key(v)] = v
    _trace(f"set_canonical_map: {len(_CANON)} entries")

def register_device(canonical_id: str, *, name: str = None, device: str = None, serial: str = None, alias: str = None, host: str = None, port: int = None) -> None:
    """ลงทะเบียนอุปกรณ์ทีละตัว"""
    can = str(canonical_id).strip()
    with _CANON_LOCK:
        if can:
            _CANON[_norm_key(can)] = can
        for k in [name, device, serial, alias, (f"{host}:{port}" if host and port is not None else None), host]:
            if k:
                _CANON[_norm_key(str(k))] = can
    _trace(f"register_device: can={can} keys={ [k for k in [name, device, serial, alias, host] if k] }")

def canonical_id_of(device_key: Optional[str]) -> Optional[str]:
    """แปลงค่าใด ๆ ให้เป็น canonical id ถ้ามีใน registry"""
    if not device_key:
        return None
    key = _norm_key(str(device_key))
    with _CANON_LOCK:
        can = _CANON.get(key)
    _trace(f"canonical_id_of: in={device_key!r} -> {can!r}")
    return can

# --------------------- Device file loggers -----------------
_dev_handlers_lock = threading.Lock()
_dev_handlers: Dict[str, logging.Logger] = {}

def _device_log_path(device_id: str) -> str:
    return os.path.join(LOG_DIR, f"app.log.{device_id}")

def _get_dev_logger(device_id: str) -> logging.Logger:
    """logger ต่ออุปกรณ์ (ใช้ id อย่างเดียว)"""
    if not device_id:
        return _root  # กันพลาด: ถ้าไม่มี id เขียนลง global

    safe_id = _sanitize_dev_id(str(device_id))
    with _dev_handlers_lock:
        if safe_id in _dev_handlers:
            return _dev_handlers[safe_id]

        lg = logging.getLogger(f"adb-upgrade-bot.dev.{safe_id}")
        lg.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
        lg.propagate = False
        for h in list(lg.handlers):
            lg.removeHandler(h)

        fh = RotatingFileHandler(
            _device_log_path(safe_id),
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        fh.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
        fh.setFormatter(_fmt)
        lg.addHandler(fh)

        _dev_handlers[safe_id] = lg
        return lg

# --------------------- Public: plain log (global) ----------
def i(msg: str): _root.info(msg)
def w(msg: str): _root.warning(msg)
def e(msg: str): _root.error(msg)

# --------------------- Public: plain log (per device) ------
def dev_info(device_id: str, msg: str):
    key = (device_id or "").strip()
    if not key or ":" in key or "." in key:
        key = "global"
    _get_dev_logger(key).info(msg)

def dev_warn(device_id: str, msg: str):
    key = (device_id or "").strip()
    if not key or ":" in key or "." in key:
        key = "global"
    _get_dev_logger(key).warning(msg)

def dev_error(device_id: str, msg: str):
    key = (device_id or "").strip()
    if not key or ":" in key or "." in key:
        key = "global"
    _get_dev_logger(key).error(msg)

# --------------------- Rich web log buffer -----------------
_WEB_BUF_MAX = int(os.getenv("WEBLOG_BUFFER_MAX", "5000"))
_web_buf: deque = deque(maxlen=_WEB_BUF_MAX)
_web_lock = threading.Lock()

def web(device_id: Optional[str], html: str, level: str = "INFO"):
    key = (device_id or "").strip()
    if not key or ":" in key or "." in key:
        key = "global"
    ent = {"ts": time.time(), "lvl": level.upper(), "dev": key, "html": html}
    with _web_lock:
        _web_buf.append(ent)
    _get_dev_logger(key).info("[WEB] " + level.upper() + " " + html)


def web_info(device_key: Optional[str], html: str):  web(device_key, html, "INFO")
def web_warn(device_key: Optional[str], html: str):  web(device_key, html, "WARN")
def web_error(device_key: Optional[str], html: str): web(device_key, html, "ERROR")

def web_dump(lines: int = 200, device: Optional[str] = None) -> List[Dict[str, Any]]:
    with _web_lock:
        buf = list(_web_buf)

    if device:
        want = _sanitize_dev_id(str(device))
        out = [x for x in buf if _sanitize_dev_id(x.get("dev","")) == want]
    else:
        out = buf

    return out[-lines:] if lines and lines > 0 else out

def web_clear(device: Optional[str] = None) -> int:
    """ล้าง rich-log ทั้งหมดหรือเฉพาะ canonical id ที่ระบุ"""
    removed = 0
    with _web_lock:
        if device:
            can = canonical_id_of(device) or str(device)
            safe = _sanitize_dev_id(can)
            old = len(_web_buf)
            remain = [x for x in list(_web_buf) if _sanitize_dev_id(x.get("dev","")) != safe]
            removed = old - len(remain)
            _web_buf.clear(); _web_buf.extend(remain)
        else:
            removed = len(_web_buf); _web_buf.clear()
    return removed

# --------------------- Readers (plain files) ----------------
def read_all(lines: int = 200) -> str:
    return _tail_file(os.path.join(LOG_DIR, "app.log"), lines)

def get_device_text(device_id: str, lines: int = 200) -> str:
    if not device_id:
        return ""
    path = _device_log_path(_sanitize_dev_id(str(device_id)))
    return _tail_file(path, lines)

def _tail_file(path: str, lines: int) -> str:
    if not os.path.exists(path): return ""
    try:
        with open(path, "rb") as f:
            return _tail_bytes(f, lines).decode("utf-8", errors="ignore")
    except Exception:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f2:
                return "".join(f2.readlines()[-lines:])
        except Exception:
            return ""

def _tail_bytes(f, lines: int) -> bytes:
    if lines <= 0: return b""
    avg_line_len = 120
    to_read = lines * avg_line_len
    f.seek(0, io.SEEK_END)
    file_size = f.tell()
    offset = max(file_size - to_read, 0)
    f.seek(offset)
    data = f.read()
    parts = data.splitlines()
    return b"\n".join(parts[-lines:])

# -------- REPLACE THIS FUNCTION IN app/rtlog.py --------
# rtlog.py
def device_key(src=None, *, alias=None, serial=None, host=None, port=None) -> str:
    # --- force id only ---
    if src is not None:
        dev_id = getattr(src, "id", None)
        if dev_id:
            return _sanitize_dev_id(str(dev_id))
    # ไม่สนใจ alias/serial/host:port แล้ว
    return "global"


def _dev_match_flexible(a: str, b: str) -> bool:
    return _sanitize_dev_id(a) == _sanitize_dev_id(b)

# ====================== Multiprocess WebLog Bridge ======================
_MP_Q = None
_MP_THREAD = None
_MP_STOP = threading.Event()

def set_weblog_queue(q):
    global _MP_Q
    _MP_Q = q

def _mp_bridge_loop():
    while not _MP_STOP.is_set():
        if _MP_Q is None:
            time.sleep(0.2); continue
        try:
            msg = _MP_Q.get(timeout=0.2)
        except _queue.Empty:
            continue
        except Exception as ex:
            _root.error(f"weblog bridge: queue error: {ex}")
            time.sleep(0.2); continue

        try:
            if isinstance(msg, dict):
                dev  = msg.get("dev") or msg.get("device") or msg.get("device_id") or ""
                html = msg.get("html") or msg.get("msg") or ""
                lvl  = (msg.get("lvl") or msg.get("level") or "INFO").upper()
            else:
                parts = list(msg)
                dev  = parts[0] if len(parts) > 0 else ""
                html = parts[1] if len(parts) > 1 else ""
                lvl  = str(parts[2]).upper() if len(parts) > 2 else "INFO"
            web(dev, html, lvl)
        except Exception as ex:
            _root.error(f"weblog bridge: handle error: {ex}")

def start_mp_bridge():
    global _MP_THREAD
    if _MP_THREAD and _MP_THREAD.is_alive():
        return True
    if _MP_Q is None:
        _root.warning("[rtlog] start_mp_bridge called but no queue set")
        return False
    _MP_STOP.clear()
    _MP_THREAD = threading.Thread(target=_mp_bridge_loop, name="weblog-bridge", daemon=True)
    _MP_THREAD.start()
    _root.info("[rtlog] weblog bridge started")
    return True

def stop_mp_bridge():
    global _MP_THREAD
    if _MP_THREAD and _MP_THREAD.is_alive():
        _MP_STOP.set()
        try: _MP_THREAD.join(timeout=1.0)
        except Exception: pass
        _root.info("[rtlog] weblog bridge stopped")
    return True

# ====================== MP helpers (child process API) ======================
def attach_mp_queue(q):
    global _MP_Q
    _MP_Q = q
    try: _root.info("[rtlog] attach_mp_queue: ok")
    except Exception: pass
    return True

def mp_web(device_key: str, html: str, level: str = "INFO"):
    try:
        if _MP_Q is not None:
            _trace(f"mp_web(): queue-put dev={device_key!r} level={level}")
            _MP_Q.put_nowait({"dev": device_key, "html": html, "lvl": level})
            return True
    except Exception as ex:
        _trace(f"mp_web(): queue error={ex} → fallback")

    if not device_key:
        _trace("mp_web(): empty dev → GLOBAL")
        _root.info(f"[WEB] {level.upper()} {html}")
        return False

    _trace(f"mp_web(): fallback dev={device_key!r}")
    web(device_key, html, level)
    return False

def mp_web_info(device_key: str, html: str):  return mp_web(device_key, html, "INFO")
def mp_web_warn(device_key: str, html: str):  return mp_web(device_key, html, "WARN")
def mp_web_error(device_key: str, html: str): return mp_web(device_key, html, "ERROR")
