# app/controller.py
import threading
from app import config as C
from app.worker import worker_loop
from app import rtlog as LOG

_state_lock = threading.Lock()
_worker_th: threading.Thread | None = None
_stop_ev = threading.Event()
_pause_ev = threading.Event()

def _is_alive():
    global _worker_th
    return _worker_th is not None and _worker_th.is_alive()

def status():
    with _state_lock:
        s = "running" if _is_alive() else "stopped"
        p = _pause_ev.is_set()
        return {"state": s, "paused": p}

def start():
    global _worker_th, _stop_ev, _pause_ev
    with _state_lock:
        if _is_alive():
            return True, "Bot already running"
        _stop_ev.clear()
        _pause_ev.clear()
        _worker_th = threading.Thread(
            target=worker_loop,
            kwargs={"stop_ev": _stop_ev, "pause_ev": _pause_ev},
            daemon=True
        )
        _worker_th.start()
        LOG.tee("BOT STARTED")
        return True, "Started"

def stop():
    global _worker_th, _stop_ev
    with _state_lock:
        if not _is_alive():
            return True, "Bot already stopped"
        _stop_ev.set()
    LOG.tee("Stopping bot ...")
    return True, "Stopping"

def pause_resume():
    with _state_lock:
        if not _is_alive():
            return False, "Bot is not running"
        if _pause_ev.is_set():
            _pause_ev.clear()
            LOG.tee("RESUME")
            return True, "Resumed"
        else:
            _pause_ev.set()
            LOG.tee("PAUSE")
            return True, "Paused"
