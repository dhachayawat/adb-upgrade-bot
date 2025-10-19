# app/rtlog.py
# ยูทิลิตีสำหรับ runtime logs: พิมพ์หน้าคอนโซล + เขียนลงไฟล์ + อ่านกลับมาโชว์บน Web UI
import io
import os
import threading
import time

from app import config as C

# ตำแหน่งไฟล์ log (เก็บในโฟลเดอร์ debug/cachedir ที่แมป volume ไว้)
LOG_DIR = C.DEBUG_DIR
LOG_FILE = os.path.join(LOG_DIR, "runtime.log")

# ขนาดสูงสุดก่อน rotate (ประมาณ 2MB)
MAX_BYTES = int(os.getenv("RUNTIME_LOG_MAX_BYTES", str(2 * 1024 * 1024)))
# ส่วนที่เก็บไว้เมื่อ rotate (ประมาณ 512KB ท้ายไฟล์)
TAIL_KEEP = int(os.getenv("RUNTIME_LOG_TAIL_KEEP", str(512 * 1024)))

_lock = threading.Lock()

def _ensure_dir():
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
    except Exception:
        pass

def _ts():
    return time.strftime("%Y-%m-%d %H:%M:%S")

def _rotate_if_needed():
    try:
        if not os.path.isfile(LOG_FILE):
            return
        sz = os.path.getsize(LOG_FILE)
        if sz <= MAX_BYTES:
            return
        # เก็บท้ายไฟล์ไว้ประมาณ TAIL_KEEP
        with open(LOG_FILE, "rb") as f:
            if sz > TAIL_KEEP:
                f.seek(-TAIL_KEEP, os.SEEK_END)
            data = f.read()
        with open(LOG_FILE, "wb") as f:
            f.write(b"[log rotated]\n")
            f.write(data)
    except Exception:
        # ไม่ต้องให้โปรแกรมล้ม
        pass

def tee(msg: str):
    """
    พิมพ์ข้อความ (พร้อม timestamp) ออกหน้าจอ และเขียนลงไฟล์ log
    """
    line = f"[{_ts()}] {msg}"
    print(line, flush=True)
    _ensure_dir()
    with _lock:
        _rotate_if_needed()
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")

def write_raw(line: str):
    """
    เขียนบรรทัดลงไฟล์ log โดยไม่เติม timestamp (ใช้เฉพาะกรณีพิเศษ)
    """
    _ensure_dir()
    with _lock:
        _rotate_if_needed()
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)

def read_all(max_bytes: int = 256 * 1024) -> str:
    """
    อ่าน log แบบ tail: คืนข้อความท้ายไฟล์ (สูงสุด max_bytes)
    ใช้ใน /api/logs เพื่อไม่โหลดไฟล์ใหญ่เกิน
    """
    try:
        if not os.path.isfile(LOG_FILE):
            return ""
        size = os.path.getsize(LOG_FILE)
        with open(LOG_FILE, "rb") as f:
            if size > max_bytes:
                f.seek(-max_bytes, os.SEEK_END)
            data = f.read()
        # พยายาม decode แบบทนทาน
        return data.decode("utf-8", errors="ignore")
    except Exception:
        return ""

def clear():
    """
    ล้างไฟล์ log
    """
    try:
        _ensure_dir()
        with _lock:
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.write("")
    except Exception:
        pass

def path() -> str:
    """คืน path ของไฟล์ log (เผื่อใช้ debug แสดงบน UI)"""
    return LOG_FILE
