# app/controller.py
# ตัวควบคุมบอท: start/stop/pause/resume + สถานะสำหรับ Web UI
import threading
import time
from typing import Tuple

from app import rtlog as LOG
from app import config as C
from app.worker import worker_loop

# ================== Module state ==================
_t: threading.Thread | None = None
_stop_ev = threading.Event()
_pause_ev = threading.Event()

# ================== Helpers ==================
def _running() -> bool:
    return bool(_t and _t.is_alive() and not _stop_ev.is_set())

# ================== Public API ==================
def start() -> Tuple[bool, str]:
    """เริ่มทำงานบอท (spawn worker thread)"""
    global _t
    if _running():
        return True, "บอทยังทำงานอยู่แล้ว"

    # reload config เผื่อผู้ใช้เพิ่งกด Save จาก UI
    try:
        C.reload()
    except Exception as e:
        LOG.tee(f"[controller] reload config ล้มเหลว: {e}")

    _stop_ev.clear()
    _pause_ev.clear()
    _t = threading.Thread(target=worker_loop, args=(_stop_ev, _pause_ev), daemon=True)
    _t.start()
    LOG.tee("[controller] เริ่มทำงานบอทแล้ว")
    return True, "เริ่มทำงานบอทแล้ว"

def stop() -> Tuple[bool, str]:
    """หยุดทำงานบอท (สั่ง stop event แล้ว join เธรด)"""
    global _t
    if not _t:
        return True, "บอทยังไม่ได้เริ่ม"
    _stop_ev.set()
    try:
        _t.join(timeout=3.0)
    except Exception:
        pass
    _t = None
    LOG.tee("[controller] หยุดทำงานบอทแล้ว")
    return True, "หยุดทำงานบอทแล้ว"

def pause() -> Tuple[bool, str]:
    """พักการทำงานชั่วคราว"""
    if not _running():
        return False, "บอทยังไม่ทำงาน"
    _pause_ev.set()
    LOG.tee("[controller] พักการทำงาน")
    return True, "พักการทำงาน"

def resume() -> Tuple[bool, str]:
    """ยกเลิกพัก กลับมาทำงานต่อ"""
    if not _running():
        return False, "บอทยังไม่ทำงาน"
    _pause_ev.clear()
    LOG.tee("[controller] ทำงานต่อ")
    return True, "ทำงานต่อ"

def is_running() -> bool:
    """สถานะกำลังทำงาน (สำหรับ /api/status)"""
    return _running()

def is_paused() -> bool:
    """สถานะพักอยู่ (สำหรับ /api/status)"""
    return _pause_ev.is_set()
