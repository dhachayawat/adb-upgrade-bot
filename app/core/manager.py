# app/core/manager.py
from __future__ import annotations
import time
import threading
from collections import deque
from dataclasses import dataclass
from typing import Dict, Optional, Callable, List, Any

from app import rtlog as LOG
from app.config_store import ConfigStore, Device as DeviceModel


@dataclass
class _RingLogger:
    """เก็บ log รายอุปกรณ์แบบ ring buffer"""
    max_lines: int = 2000

    def __post_init__(self):
        self._buf = deque(maxlen=self.max_lines)
        self._lock = threading.Lock()

    def write(self, line: str) -> None:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            self._buf.append(f"{ts} {line}")

    def tail(self, n: int = 400) -> str:
        with self._lock:
            lines = list(self._buf)[-max(0, n):]
        return "\n".join(lines)


class DeviceController:
    """
    ตัวควบคุมอุปกรณ์ 1 ตัว (thread + state)
    """
    def __init__(self, dev: DeviceModel, step_fn: Optional[Callable] = None):
        self.id: str = dev.id
        self.name: str = dev.name
        self.device: str = dev.device      # host:port หรือ usb:SERIAL หรือ serial
        self.target_level: int = dev.target_level

        # runtime
        self.step_fn: Optional[Callable] = step_fn
        self.state: str = "idle"           # idle|running|paused|stopping|error
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.thread: Optional[threading.Thread] = None

        self.started_at: float = 0.0
        self.last_tick: float = 0.0
        self.last_error: str = ""
        self.items_upgraded_done: int = 0

        # per-device log buffer
        self._rlog = _RingLogger(max_lines=3000)

    # --------- logging helpers (device-local) ---------
    def log_i(self, msg: str) -> None:
        line = f"[{self.id}] {msg}"
        self._rlog.write(line)
        LOG.i(line)

    def log_w(self, msg: str) -> None:
        line = f"[{self.id}] {msg}"
        self._rlog.write(line)
        LOG.w(line)

    def log_e(self, msg: str) -> None:
        line = f"[{self.id}] {msg}"
        self._rlog.write(line)
        LOG.e(line)

    def log_tail(self, n: int = 400) -> str:
        return self._rlog.tail(n)

    # --------- items counter (ให้ worker เรียกได้) ---------
    def add_items_done(self, n: int = 1) -> None:
        self.items_upgraded_done += int(n)

    # --------- lifecycle ---------
    def start(self) -> bool:
        if self.state in ("running", "stopping"):
            raise RuntimeError("already running or stopping")
        if not callable(self.step_fn):
            raise RuntimeError("step_fn is not set")
        self.stop_event.clear()
        self.pause_event.clear()
        self.state = "running"
        self.started_at = time.time()
        self.last_error = ""
        self.log_i("start")

        self.thread = threading.Thread(
            target=self._run_loop, name=f"dev:{self.id}", daemon=True
        )
        self.thread.start()
        return True

    def _run_loop(self) -> None:
        try:
            # lazy import กัน path issue และให้ hot reload worker_step ได้ง่ายขึ้น
            from app.worker_step import worker_loop  # type: ignore
            worker_loop(self, self.step_fn)  # step_fn(self) ภายใน loop
        except Exception as e:
            self.last_error = str(e)
            self.state = "error"
            self.log_e(f"fatal in _run_loop: {e}")

    def pause(self) -> bool:
        if self.state != "running":
            raise RuntimeError("not running")
        self.pause_event.set()
        self.state = "paused"
        self.log_i("paused")
        return True

    def resume(self) -> bool:
        if self.state != "paused":
            raise RuntimeError("not paused")
        self.pause_event.clear()
        self.state = "running"
        self.log_i("resumed")
        return True

    def stop(self) -> bool:
        if self.state not in ("running", "paused", "error"):
            raise RuntimeError("not running/paused")
        self.state = "stopping"
        self.stop_event.set()
        self.log_i("stopping...")
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5.0)
        self.state = "idle"
        self.log_i("stopped")
        return True

    # --------- reporting ---------
    def uptime_min(self) -> float:
        if not self.started_at:
            return 0.0
        return max(0.0, (time.time() - self.started_at) / 60.0)

    def status(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "device": self.device,
            "state": self.state,
            "target_level": self.target_level,
            "items_upgraded_done": self.items_upgraded_done,
            "uptime_min": self.uptime_min(),
            "last_tick": self.last_tick,
            "last_error": self.last_error,
        }


class DeviceManager:
    """
    จัดการหลายอุปกรณ์ + ผูกกับ ConfigStore
    """
    def __init__(self, cfg_store: ConfigStore, step_fn: Optional[Callable] = None):
        self.cfg = cfg_store
        self.step_fn: Optional[Callable] = step_fn
        self._controllers: Dict[str, DeviceController] = {}

        # bootstrap controllers จากไฟล์ device.json
        for dev in self.cfg.load_devices():
            self._controllers[dev.id] = DeviceController(dev, step_fn=self.step_fn)

    # --------- utilities ---------
    def set_step_fn(self, fn: Callable) -> None:
        self.step_fn = fn
        for c in self._controllers.values():
            c.step_fn = fn

    def get(self, device_id: str) -> Optional[DeviceController]:
        return self._controllers.get(device_id)

    def list_devices(self) -> List[Dict[str, Any]]:
        return [c.status() for c in self._controllers.values()]

    # --------- mutate device set ---------
    def upsert_device(self, dev: DeviceModel) -> None:
        ctrl = self._controllers.get(dev.id)
        if ctrl:
            if ctrl.state in ("running", "paused", "stopping"):
                # อนุญาตแก้ไข name/target ได้ แต่เปลี่ยน serial ขณะรันไม่ปลอดภัย
                if dev.device != ctrl.device:
                    raise RuntimeError("cannot change device while running; stop first")
            ctrl.name = dev.name
            ctrl.device = dev.device
            ctrl.target_level = dev.target_level
            ctrl.step_fn = self.step_fn
        else:
            self._controllers[dev.id] = DeviceController(dev, step_fn=self.step_fn)

        # persist
        self.cfg.upsert_device(dev)

    def delete_device(self, device_id: str) -> bool:
        ctrl = self._controllers.get(device_id)
        if not ctrl:
            return False
        if ctrl.state in ("running", "paused", "stopping"):
            raise RuntimeError("device is running; stop it before delete")
        # remove & persist
        ok = self.cfg.delete_device(device_id)
        if ok:
            self._controllers.pop(device_id, None)
        return ok

    # --------- runtime controls ---------
    def start(self, device_id: str) -> bool:
        ctrl = self._controllers[device_id]
        return ctrl.start()

    def pause(self, device_id: str) -> bool:
        ctrl = self._controllers[device_id]
        return ctrl.pause()

    def resume(self, device_id: str) -> bool:
        ctrl = self._controllers[device_id]
        return ctrl.resume()

    def stop(self, device_id: str) -> bool:
        ctrl = self._controllers[device_id]
        return ctrl.stop()

    # --------- target level ---------
    def set_target_level(self, device_id: str, level: int, *, persist: bool = False) -> None:
        if level < 1 or level > 10:
            raise ValueError("target_level must be between 1 and 10")
        ctrl = self._controllers[device_id]
        ctrl.target_level = level

        if persist:
            # อัปเดตไฟล์ device.json
            devices = self.cfg.load_devices()
            found = False
            for i, d in enumerate(devices):
                if d.id == device_id:
                    devices[i] = DeviceModel(
                        id=d.id, name=d.name, device=d.device, target_level=level
                    )
                    found = True
                    break
            if not found:
                # กรณีในหน่วยความจำมี แต่ไฟล์ไม่มี → เพิ่มเข้าไฟล์
                devices.append(DeviceModel(
                    id=ctrl.id, name=ctrl.name, device=ctrl.device, target_level=level
                ))
            self.cfg.save_devices(devices)

    # --------- dashboard / summary ---------
    def dashboard(self) -> Dict[str, Any]:
        """
        รวมภาพรวมแบบเร็ว: รายอุปกรณ์ + สรุปรวม
        """
        devs = [c.status() for c in self._controllers.values()]
        total_running = sum(1 for d in devs if d["state"] == "running")
        total_items = sum(int(d.get("items_upgraded_done", 0)) for d in devs)
        return {
            "devices": devs,
            "totals": {
                "devices": len(devs),
                "running": total_running,
                "items_upgraded_done": total_items,
            }
        }
