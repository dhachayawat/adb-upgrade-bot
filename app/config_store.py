# app/config_store.py
from __future__ import annotations
import os
import json
import tempfile
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import List, Dict, Any


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
        return _safe_read_json(self.defaults_file)

    def save_defaults(self, data: Dict[str, Any]) -> None:
        # สามารถใส่ validation เบื้องต้นได้ตามต้องการ
        _atomic_write(self.defaults_file, data)

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
