# app/core/controller.py
from __future__ import annotations
from typing import Optional
import time

try:
    import multiprocessing as mp
except Exception:
    mp = None
try:
    import threading as th
except Exception:
    th = None


class Controller:
    """
    คอนโทรลเลอร์บาง ๆ ให้ worker_loop / worker_step ใช้งาน
    - มี stop_event / pause_event
    - มีฟิลด์ที่ worker_loop อ้างถึง: id, device, target_level, state, last_tick, last_error, adb
    """
    def __init__(self,
                 device: str,
                 target_level: int,
                 stop_event=None,
                 pause_event=None):
        self.id = device                 # ใช้เป็น dev_id (ต้อง unique ต่อดีไวซ์)
        self.device = device             # serial/adb-id
        self.target_level = target_level

        # รองรับทั้ง multiprocessing.Event และ threading.Event
        if stop_event is None:
            if mp is not None:
                stop_event = mp.Event()
            elif th is not None:
                stop_event = th.Event()
            else:
                raise RuntimeError("No Event type available")
        if pause_event is None:
            if mp is not None:
                pause_event = mp.Event()
            elif th is not None:
                pause_event = th.Event()
            else:
                raise RuntimeError("No Event type available")

        self.stop_event = stop_event
        self.pause_event = pause_event

        # worker_step/loop จะเซ็ต/อ่านพวกนี้
        self.state: str = "running"
        self.last_tick: float = 0.0
        self.last_error: Optional[str] = None
        self.adb = None  # จะถูก new ใน worker_step เมื่อยังไม่มี

        # สำหรับ heartbeat (worker_loop อาจอ่าน)
        self.items_upgraded_done: int = 0

    # เผื่ออยากใช้งานรูปแบบ ctrl.step() ก็มี method ให้เรียก worker_step ได้ตรง ๆ
    def step(self):
        from app.worker_step import worker_step  # lazy import เลี่ยงวงจรอิมพอร์ต
        return worker_step(self)
