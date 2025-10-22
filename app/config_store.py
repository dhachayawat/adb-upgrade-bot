# app/config_store.py
from __future__ import annotations
import os
import json
import tempfile
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Tuple


# ============ Utilities ============

def _safe_read_json(path: str | Path) -> Dict[str, Any]:
    """
    อ่านไฟล์ JSON แบบปลอดภัย: ถ้าไฟล์ไม่มี/พัง → คืน {} แทน
    """
    p = Path(path)
    if not p.exists():
        return {}
    try:
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _atomic_write(path: str | Path, data: Dict[str, Any]) -> None:
    """
    เขียนไฟล์แบบ atomic โดยสร้าง temp ในไดเรกทอรีเดียวกับปลายทาง
    เพื่อเลี่ยง OSError: Invalid cross-device link (Errno 18)
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=str(p.parent), delete=False
    ) as tf:
        json.dump(data, tf, ensure_ascii=False, indent=2)
        tf.write("\n")
        tf.flush()
        os.fsync(tf.fileno())
        tmp_name = tf.name

    os.replace(tmp_name, p)


# ============ Normalizers / Helpers ============

def _as_int_tuple_2(v) -> Tuple[int, int] | None:
    if isinstance(v, (list, tuple)) and len(v) == 2:
        try:
            return int(v[0]), int(v[1])
        except Exception:
            return None
    return None

def _as_int_tuple_4(v) -> Tuple[int, int, int, int] | None:
    if isinstance(v, (list, tuple)) and len(v) == 4:
        try:
            return int(v[0]), int(v[1]), int(v[2]), int(v[3])
        except Exception:
            return None
    if isinstance(v, dict):
        try:
            return int(v.get("x1", 0)), int(v.get("y1", 0)), int(v.get("w", 0)), int(v.get("h", 0))
        except Exception:
            return None
    return None

def _rect_dict(x1:int, y1:int, w:int, h:int) -> Dict[str, int]:
    return {"x1": int(x1), "y1": int(y1), "w": int(w), "h": int(h)}

def _normalize_items(data: Dict[str, Any]) -> None:
    items = data.get("items")
    if isinstance(items, list):
        fixed = []
        for p in items:
            t = _as_int_tuple_2(p)
            if t:
                fixed.append([t[0], t[1]])
        data["items"] = fixed

def _normalize_point_field(data: Dict[str, Any], key: str) -> None:
    t = _as_int_tuple_2(data.get(key))
    if t:
        data[key] = [t[0], t[1]]

def _normalize_rect_field(data: Dict[str, Any], key: str) -> None:
    r = _as_int_tuple_4(data.get(key))
    if r:
        x1, y1, w, h = r
        data[key] = _rect_dict(x1, y1, w, h)

def _center_size_to_rect(center: Tuple[int,int] | None, size: Tuple[int,int] | None) -> Tuple[int,int,int,int] | None:
    if not center or not size:
        return None
    cx, cy = center
    w, h = size
    x1 = max(0, cx - w // 2)
    y1 = max(0, cy - h // 2)
    return x1, y1, w, h

def _normalize_slot_status_roi(data: Dict[str, Any]) -> None:
    """
    รองรับทั้งรูปแบบใหม่:
        "slot_status_roi": { "x1":..., "y1":..., "w":..., "h":... }
    และรูปแบบเก่า (ถ้ามี):
        "slot_status": [cx, cy]
        "slot_status_roi": [w, h]  หรือ "slot_roi": [w, h]
    จะเขียนกลับเป็น dict 4 คีย์เสมอ
    """
    # ถ้าเป็น dict 4 คีย์อยู่แล้ว → บังคับเป็นรูป dict ที่แน่นอน
    r4 = _as_int_tuple_4(data.get("slot_status_roi"))
    if r4:
        x1, y1, w, h = r4
        data["slot_status_roi"] = _rect_dict(x1, y1, w, h)
        return

    # legacy: center + size
    center = _as_int_tuple_2(data.get("slot_status"))
    size = _as_int_tuple_2(data.get("slot_status_roi")) or _as_int_tuple_2(data.get("slot_roi"))
    rect = _center_size_to_rect(center, size)
    if rect:
        x1, y1, w, h = rect
        data["slot_status_roi"] = _rect_dict(x1, y1, w, h)
    else:
        # ใส่ค่าเริ่มต้นว่าง ๆ ป้องกัน KeyError ฝั่งผู้อ่าน
        data["slot_status_roi"] = _rect_dict(0, 0, 0, 0)

def _normalize_defaults(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    จัดรูป defaults ให้เป็นชนิดข้อมูลที่คาดหวัง:
      - items: [[x,y], ...]
      - slot_center, upgrade_btn: [x,y]
      - overlay_abs, insert_roi, slot_status_roi: {x1,y1,w,h}
      - swipe: {x,y,dy,ms} (int)
    """
    out = dict(data or {})

    # จุดกดเดี่ยว
    _normalize_point_field(out, "slot_center")
    _normalize_point_field(out, "upgrade_btn")

    # รายการไอเทม
    _normalize_items(out)

    # rect มาตรฐาน
    _normalize_rect_field(out, "overlay_abs")
    _normalize_rect_field(out, "insert_roi")

    # slot_status_roi: รองรับทั้งรูปแบบใหม่และเก่า → บังคับเป็น rect dict
    _normalize_slot_status_roi(out)

    # swipe
    swipe = out.get("swipe") or {}
    try:
        out["swipe"] = {
            "x": int(swipe.get("x", 0)),
            "y": int(swipe.get("y", 0)),
            "dy": int(swipe.get("dy", 0)),
            "ms": int(swipe.get("ms", 300)),
        }
    except Exception:
        out["swipe"] = {"x": 0, "y": 0, "dy": 0, "ms": 300}

    # device string
    if "device" in out:
        out["device"] = str(out["device"]).strip()

    # threshold เฉพาะบางฟีเจอร์ (ถ้ามี)
    if "conf_insert_thr" in out:
        try:
            out["conf_insert_thr"] = float(out["conf_insert_thr"])
        except Exception:
            pass

    return out


# ============ Models ============

@dataclass
class Device:
    id: str
    name: str
    device: str           # host:port หรือ usb:<serial> หรือ serial
    target_level: int = 5

    @staticmethod
    def from_any(x: Dict[str, Any]) -> "Device":
        return Device(
            id=str(x.get("id", "")).strip(),
            name=str(x.get("name", "")).strip(),
            device=str(x.get("device", "")).strip(),
            target_level=int(x.get("target_level", 5)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============ Store ============

class ConfigStore:
    """
    บริหารจัดการไฟล์คอนฟิก:
      - defaults (ตำแหน่ง/ROI ฯลฯ): CONFIG_DEFAULTS_FILE (default /app/data/config/config.json)
      - devices  (รายการอุปกรณ์):     DEVICES_FILE         (default /app/data/config/device.json)
    โครงสร้างไฟล์ devices:
      { "devices": [ {id, name, device, target_level}, ... ] }
    """
    def __init__(self) -> None:
        self.defaults_file = Path(os.getenv("CONFIG_DEFAULTS_FILE", "/app/data/config/config.json"))
        self.devices_file  = Path(os.getenv("DEVICES_FILE", "/app/data/config/device.json"))

    # ----- defaults (shared config for resolution) -----

    def load_defaults(self) -> Dict[str, Any]:
        """
        โหลด defaults แล้ว normalize ให้เป็นสคีมาตามที่โค้ดคาดหวัง:
          - "slot_status_roi" บังคับให้อยู่ในรูป {x1,y1,w,h} (รองรับ config เก่าที่เป็น center+size)
          - "insert_roi" / "overlay_abs" เป็น {x1,y1,w,h}
          - "slot_center" / "upgrade_btn" เป็น [x,y]
          - "items" เป็น [[x,y], ...] ที่เป็น int
        """
        raw = _safe_read_json(self.defaults_file)
        return _normalize_defaults(raw)

    def save_defaults(self, data: Dict[str, Any]) -> None:
        """
        เขียน defaults โดย normalize ก่อนเสมอ เพื่อให้ไฟล์ออกมาเป็นฟอร์แมตเดียวกัน
        """
        clean = _normalize_defaults(data)
        _atomic_write(self.defaults_file, clean)

    def clear_defaults(self) -> None:
        _atomic_write(self.defaults_file, {})

    # ----- devices list -----

    def load_devices(self) -> List[Device]:
        data = _safe_read_json(self.devices_file)
        arr = data.get("devices", [])
        out: List[Device] = []
        for x in arr:
            try:
                out.append(Device.from_any(x))
            except Exception:
                # ข้ามรายการที่พัง
                continue
        return out

    def save_devices(self, devices: List[Device | Dict[str, Any]]) -> None:
        payload = {
            "devices": [
                (d.to_dict() if isinstance(d, Device) else Device.from_any(d).to_dict())
                for d in devices
            ]
        }
        _atomic_write(self.devices_file, payload)

    def upsert_device(self, dev: Device) -> None:
        devices = self.load_devices()
        for i, d in enumerate(devices):
            if d.id == dev.id:
                devices[i] = dev
                break
        else:
            devices.append(dev)
        self.save_devices(devices)

    def delete_device(self, device_id: str) -> bool:
        devices = self.load_devices()
        new_list = [d for d in devices if d.id != device_id]
        if len(new_list) == len(devices):
            return False  # ไม่มีรายการนี้อยู่
        self.save_devices(new_list)
        return True
