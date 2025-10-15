# app/status.py
from collections import deque
import threading
import time

_lock = threading.Lock()

_state = {
    "running": False,
    "batch": 0,
    "current_item_idx": 0,     # 1..6
    "phase": "idle",           # idle / insert / upgrade / swipe
    "counts": {
        "processed": 0,
        "+5": 0,
        "broken": 0,
        "skipped": 0,
        "errors": 0
    },
    "last_message": "",
    "last_update_ts": 0.0,
}

_logs = deque(maxlen=500)

def set_running(v: bool):
    with _lock:
        _state["running"] = v
        _state["last_update_ts"] = time.time()

def set_phase(phase: str):
    with _lock:
        _state["phase"] = phase
        _state["last_update_ts"] = time.time()

def set_batch(n: int):
    with _lock:
        _state["batch"] = n
        _state["last_update_ts"] = time.time()

def set_current_item(i: int):
    with _lock:
        _state["current_item_idx"] = i
        _state["last_update_ts"] = time.time()

def inc_count(key: str, add: int = 1):
    with _lock:
        _state["counts"][key] = _state["counts"].get(key, 0) + add
        _state["last_update_ts"] = time.time()

def set_counts(processed=None, plus5=None, broken=None, skipped=None, errors=None):
    with _lock:
        if processed is not None: _state["counts"]["processed"] = processed
        if plus5 is not None:     _state["counts"]["+5"] = plus5
        if broken is not None:    _state["counts"]["broken"] = broken
        if skipped is not None:   _state["counts"]["skipped"] = skipped
        if errors is not None:    _state["counts"]["errors"] = errors
        _state["last_update_ts"] = time.time()

def log(msg: str, level: str = "info"):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {level.upper()}: {msg}"
    with _lock:
        _logs.append(line)
        _state["last_message"] = msg
        _state["last_update_ts"] = time.time()

def snapshot():
    with _lock:
        return {
            "state": dict(_state),
            "logs": list(_logs),
        }

def clear_logs():
    with _lock:
        _logs.clear()
        _state["last_message"] = ""
        _state["last_update_ts"] = time.time()
