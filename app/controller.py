# app/controller.py
import threading
import time
import os
import cv2

from app import cv_utils as CV
from app import config as C
from app.worker import worker_loop
from app.calibrate import calibrate_once
from app import status as ST

_lock = threading.Lock()
_stop_ev = threading.Event()
_thread = None
_running = False

def is_running():
    with _lock:
        return _running and _thread is not None and _thread.is_alive()

def start():
    global _thread, _running
    with _lock:
        if _running and _thread and _thread.is_alive():
            return False, "Already running"
        _stop_ev.clear()
        _thread = threading.Thread(target=worker_loop, args=(_stop_ev,), daemon=True)
        _thread.start()
        _running = True
        ST.set_running(True)
        ST.log("Bot started", "ok")
        return True, "Started"

def stop():
    global _thread, _running
    with _lock:
        if not _running:
            return False, "Not running"
        _stop_ev.set()
        if _thread and _thread.is_alive():
            _thread.join(timeout=2.0)
        _running = False
        ST.set_running(False)
        ST.log("Bot stopped", "warn")
        return True, "Stopped"

def snapshot(tag="web"):
    os.makedirs(C.CACHE_DIR, exist_ok=True)
    img = CV.screencap_bgr(save_tag=tag)
    ts = time.strftime("%Y%m%d-%H%M%S")
    out_path = os.path.join(C.CACHE_DIR, f"snap_{tag}_{ts}.png")
    cv2.imwrite(out_path, img)
    ST.log(f"Screenshot saved: {out_path}", "ok")
    return out_path, (img.shape[1], img.shape[0])

def preview():
    out_name = "calib_preview.png"
    out_dir = getattr(C, "DEBUG_DIR", os.path.join(C.CACHE_DIR, "debug"))
    path = os.path.join(out_dir, out_name)
    calibrate_once(out_name, tag="preview")
    ST.log(f"Preview saved: {path}", "ok")
    return path

def get_status():
    return ST.snapshot()
