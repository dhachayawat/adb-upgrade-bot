# app/state.py
import threading
import time

_lock = threading.RLock()
_state = {
    "item_index": None,        # 1..6 (ภายในรอบ)
    "pre_level": None,         # ระดับก่อนใส่ลง (0..n, None = ไม่ทราบ)
    "need_success": None,      # ต้องการสำเร็จอีกกี่ครั้งจนถึง +5
    "done_success": 0,         # นับสำเร็จไปแล้วกี่ครั้งในชิ้นปัจจุบัน
    "last_msg": "",            # ข้อความสถานะล่าสุด
    "ts": 0.0,                 # timestamp อัปเดต
}

def set_current(item_index=None, pre_level=None, need_success=None, done_success=None, last_msg=None):
    with _lock:
        if item_index is not None:
            _state["item_index"] = item_index
        if pre_level is not None:
            _state["pre_level"] = pre_level
        if need_success is not None:
            _state["need_success"] = need_success
        if done_success is not None:
            _state["done_success"] = done_success
        if last_msg is not None:
            _state["last_msg"] = last_msg
        _state["ts"] = time.time()

def add_success(count=1):
    with _lock:
        _state["done_success"] = int(_state.get("done_success") or 0) + int(count)
        _state["ts"] = time.time()

def clear():
    with _lock:
        for k in list(_state.keys()):
            _state[k] = None
        _state["done_success"] = 0
        _state["last_msg"] = ""
        _state["ts"] = time.time()

def snapshot():
    with _lock:
        return dict(_state)
