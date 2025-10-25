# app/core/mp_manager.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Optional, Callable, Any, List
import multiprocessing as mp
import time
import os

from ..config_store import ConfigStore, Device
from .. import rtlog as LOG          # สำหรับ text log ปกติ และใช้ start_mp_bridge() ใน main ผ่าน config_api
from .. import rtlog as RTLOG        # ใช้ชื่อ RTLOG ชัด ๆ เวลาผูก queue ใน child

from ..worker_step import worker_loop, worker_step
from .controller import Controller


@dataclass
class _DevRec:
    dev: Device
    proc: Optional[mp.Process] = None
    stop_ev: Optional[mp.Event] = None
    pause_ev: Optional[mp.Event] = None
    started_at: float = 0.0
    items_done: int = 0
    paused: bool = False  # << สำคัญ: 状態 pause


class MPDeviceManager:
    """
    Multiprocessing device manager:
      - โปรเซสหลัก (main) เรียก start()/stop()/pause()/resume()
      - โปรเซสลูก (worker) รัน worker_loop + ส่ง Rich logs กลับ main ผ่าน Queue
    """
    def __init__(self, cfg_store: ConfigStore, step_fn: Callable[..., Any] | None = None):
        self._cfg = cfg_store
        self._rec: Dict[str, _DevRec] = {}
        self._weblog_queue: Optional[mp.Queue] = None  # << Queue สำหรับ Rich logs (ตั้งจาก config_api.init_api)
        self._preload_from_cfg()

    # ---------------- MP Rich-log queue bridge ----------------
    def set_weblog_queue(self, q: Optional[mp.Queue]) -> None:
        """
        เรียกจากโปรเซสหลัก (เช่นใน config_api.init_api หลัง RTLOG.start_mp_bridge())
        เพื่อให้ manager ถือคิวกลางไว้ ส่งเข้าโปรเซสลูกทุกครั้งที่ start()
        """
        self._weblog_queue = q
        LOG.i(f"[mp_manager] weblog_queue set: {bool(q)}")

    # ---------------- preload ----------------
    def _preload_from_cfg(self) -> None:
        loaded_any = False
        # 1) list_devices()
        try:
            devs = self._cfg.list_devices()  # type: ignore[attr-defined]
            if devs:
                for d in devs:
                    if isinstance(d, Device):
                        self._rec.setdefault(d.id, _DevRec(dev=d))
                loaded_any = True
        except Exception:
            pass
        # 2) attributes dict
        if not loaded_any:
            for key in ("devices", "_devices", "devices_by_id"):
                obj = getattr(self._cfg, key, None)
                if isinstance(obj, dict) and obj:
                    for d in obj.values():
                        if isinstance(d, Device):
                            self._rec.setdefault(d.id, _DevRec(dev=d))
                        elif isinstance(d, dict) and "id" in d and "device" in d:
                            try:
                                self._rec.setdefault(d["id"], _DevRec(dev=Device(
                                    id=str(d["id"]),
                                    name=str(d.get("name", d["id"])) if d.get("name") else str(d["id"]),
                                    device=str(d["device"]),
                                    target_level=int(d.get("target_level", 5)),
                                )))
                            except Exception:
                                continue
                    loaded_any = True
                    break
        # 3) device.json (หรือ ENV CONFIG_DEVICES_FILE)
        if not loaded_any:
            try:
                from pathlib import Path
                import json
                cfg_path = Path(os.getenv("CONFIG_DEVICES_FILE", "/app/data/config/device.json"))
                if cfg_path.exists():
                    raw = cfg_path.read_text(encoding="utf-8") or ""
                    data = json.loads(raw) if raw.strip() else {}
                    arr = data if isinstance(data, list) else data.get("devices") or []
                    for item in arr:
                        if isinstance(item, dict) and "id" in item and "device" in item:
                            dev = Device(
                                id=str(item["id"]),
                                name=str(item.get("name", item["id"])) if item.get("name") else str(item["id"]),
                                device=str(item["device"]),
                                target_level=int(item.get("target_level", 5)),
                            )
                            self._rec.setdefault(dev.id, _DevRec(dev=dev))
                    loaded_any = bool(self._rec)
            except Exception as e:
                LOG.w(f"[mp_manager] preload from device.json failed: {e}")
        if not loaded_any:
            LOG.w("[mp_manager] no devices preloaded from ConfigStore")

    # ---------------- process target ----------------
    @staticmethod
    def _device_proc(dev_id: str, dev_serial: str, target_level: int,
                     stop_ev: mp.Event, pause_ev: mp.Event,
                     weblog_queue: Optional[mp.Queue],
                     sleep_sec: float = 0.10):
        """
        Entry point ของโปรเซสลูก:
          - ผูก RTLOG.attach_mp_queue(weblog_queue) เพื่อส่ง Rich logs กลับ main
          - สร้าง Controller แล้วเข้าลูป worker
        """
        try:
            # attach queue เพื่อให้ rtlog.web ส่งกลับ main
            try:
                if weblog_queue is not None:
                    RTLOG.attach_mp_queue(weblog_queue)
            except Exception as _e:
                LOG.w(f"[{dev_id}] attach_mp_queue failed: {_e}")

            LOG.i(f"[{dev_id}] MP worker start (target=+{target_level})")
            ctrl = Controller(device=dev_serial, target_level=target_level,
                              stop_event=stop_ev, pause_event=pause_ev)
            worker_loop(ctrl, step_fn=worker_step, sleep_sec=sleep_sec)
        except KeyboardInterrupt:
            pass
        except Exception as e:
            LOG.e(f"[{dev_id}] MP worker error: {e}")
        finally:
            LOG.i(f"[{dev_id}] MP worker exit")

    # ---------------- status helpers ----------------
    @staticmethod
    def _status_from_rec(r: _DevRec) -> dict:
        # guard สำหรับเวอร์ชันเก่า
        if not hasattr(r, "paused"):
            setattr(r, "paused", False)
        paused = bool(getattr(r, "paused", False))

        now = time.time()
        alive = bool(r.proc and r.proc.is_alive())
        state = "running" if alive and not paused else ("paused" if alive and paused else "idle")
        started_at = r.started_at if alive else 0.0
        uptime_sec = int(now - started_at) if alive else 0
        return {
            "id": r.dev.id,
            "name": r.dev.name,
            "device": r.dev.device,
            "target_level": r.dev.target_level,

            "state": state,            # running/paused/idle
            "alive": alive,
            "thread_alive": alive,     # เพื่อความเข้ากันได้
            "is_paused": paused,

            "can_start": not alive,
            "can_stop": alive,
            "can_pause": (alive and not paused),
            "can_resume": (alive and paused),

            "started_at": started_at,
            "uptime_sec": uptime_sec,
            "uptime_min": round(uptime_sec / 60.0, 2),

            "items_upgraded_done": r.items_done,
            "breaks_today": {},
        }

    # ---------------- CRUD ----------------
    def upsert_device(self, dev: Device) -> None:
        rec = self._rec.get(dev.id)
        if rec is None:
            rec = _DevRec(dev=dev)
            self._rec[dev.id] = rec
        else:
            rec.dev = dev
            if not hasattr(rec, "paused"):
                rec.paused = False
        try:
            self._cfg.upsert_device(dev)  # type: ignore[attr-defined]
        except Exception:
            pass

    def delete_device(self, device_id: str) -> bool:
        r = self._rec.get(device_id)
        if not r:
            return False
        if r.proc is not None and r.proc.is_alive():
            raise RuntimeError("device is running; stop it first")
        self._rec.pop(device_id, None)
        try:
            self._cfg.delete_device(device_id)  # type: ignore[attr-defined]
        except Exception:
            pass
        return True

    # ---------------- list / get ----------------
    def list_devices(self) -> List[dict]:
        if not self._rec:
            self._preload_from_cfg()
        return [self._status_from_rec(r) for r in self._rec.values()]

    class _Proxy:
        def __init__(self, parent: "MPDeviceManager", rec: _DevRec):
            self._parent = parent
            self._rec = rec
            self.id = rec.dev.id
            self.device = rec.dev.device
        def status(self) -> dict:
            return self._parent._status_from_rec(self._rec)

    def get(self, device_id: str) -> Optional["_Proxy"]:
        r = self._rec.get(device_id)
        return None if not r else MPDeviceManager._Proxy(self, r)

    def is_alive(self, device_id: str) -> bool:
        r = self._rec.get(device_id)
        return bool(r and r.proc and r.proc.is_alive())

    def dashboard(self) -> dict:
        total = len(self._rec)
        running = sum(1 for r in self._rec.values() if r.proc and r.proc.is_alive())
        return {
            "total_devices": total,
            "running": running,
            "stopped": total - running,
            "devices": self.list_devices(),
        }

    # ---------------- run control ----------------
    def start(self, device_id: str) -> bool:
        r = self._rec.get(device_id)
        if not r:
            raise KeyError("device not found")
        if r.proc is not None and r.proc.is_alive():
            raise RuntimeError("already running")

        r.stop_ev = mp.Event()
        r.pause_ev = mp.Event()
        r.paused = False

        dev_id = r.dev.id
        dev_serial = r.dev.device
        target = int(r.dev.target_level)

        p = mp.Process(
            target=MPDeviceManager._device_proc,
            args=(
                dev_id, dev_serial, target,
                r.stop_ev, r.pause_ev,
                self._weblog_queue,                         # << ส่งคิวให้โปรเซสลูก
                float(os.getenv("MP_SLEEP_SEC", "0.10")),
            ),
            daemon=True,
        )
        p.start()

        r.proc = p
        r.started_at = time.time()
        LOG.i(f"[{dev_id}] started mp process pid={p.pid}")
        return True

    def stop(self, device_id: str) -> bool:
        r = self._rec.get(device_id)
        if not r:
            raise KeyError("device not found")
        if not r.proc:
            raise RuntimeError("not running")
        if r.stop_ev:
            r.stop_ev.set()
        p = r.proc
        p.join(timeout=float(os.getenv("MP_STOP_JOIN_SEC", "1.5")))
        if p.is_alive():
            LOG.w(f"[{device_id}] process still alive → terminate()")
            p.terminate()
            p.join(timeout=0.5)
        r.proc = None
        r.started_at = 0.0
        r.paused = False
        return True

    def pause(self, device_id: str) -> bool:
        r = self._rec.get(device_id)
        if not r or not r.proc or not r.proc.is_alive():
            raise RuntimeError("not running")
        if not r.pause_ev:
            raise RuntimeError("pause event missing")
        r.pause_ev.set()
        r.paused = True
        LOG.i(f"[{device_id}] paused")
        return True

    def resume(self, device_id: str) -> bool:
        r = self._rec.get(device_id)
        if not r or not r.proc or not r.proc.is_alive():
            raise RuntimeError("not running")
        if not r.pause_ev:
            raise RuntimeError("pause event missing")
        r.pause_ev.clear()
        r.paused = False
        LOG.i(f"[{device_id}] resumed")
        return True

    # ---------------- config ----------------
    def set_target_level(self, device_id: str, tl: int, persist: bool = False) -> None:
        r = self._rec.get(device_id)
        if not r:
            raise KeyError("device not found")
        if tl < 0 or tl > 99:
            raise ValueError("invalid target level")
        r.dev = Device(id=r.dev.id, name=r.dev.name, device=r.dev.device, target_level=int(tl))
        if persist:
            try:
                self._cfg.upsert_device(r.dev)  # type: ignore[attr-defined]
            except Exception:
                pass

    def set_step_fn(self, step_fn: Callable[..., Any]) -> None:
        # โหมด MP จะไม่ใช้ step_fn จากภายนอก (worker_step นำเข้าในโปรเซสลูกแล้ว)
        LOG.w("[mp_manager] set_step_fn ignored in RUN_MODE=mp")
