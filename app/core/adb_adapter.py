# app/core/adb_adapter.py
from __future__ import annotations
import subprocess, shlex, time
from typing import Optional, Tuple
import cv2
import numpy as np

class ADBError(RuntimeError):
    pass

class ADBAdapter:
    """
    device_str:
      - "host:port"   (เช่น host.docker.internal:5595)
      - "usb:<serial>"(เช่น usb:emulator-5554 หรือ usb:R3CN30XXXX)
      - "<serial>"    (เช่น emulator-5554)  ← ก็ได้
    """
    def __init__(self, device_str: str, connect_retries: int = 5, retry_delay: float = 0.6):
        self.device_str = (device_str or "").strip()
        self.connect_retries = connect_retries
        self.retry_delay = retry_delay

        if self.device_str.startswith("usb:"):
            self.serial = self.device_str.split(":", 1)[1]
            self.hostport: Optional[str] = None
        elif ":" in self.device_str and self.device_str.count(":") == 1:
            # host:port
            self.serial = self.device_str
            self.hostport = self.device_str
        else:
            # raw serial
            self.serial = self.device_str
            self.hostport = None

    # ---------------- low-level ----------------
    def _run(self, args: list[str], timeout: Optional[float] = 15.0) -> Tuple[int, bytes, bytes]:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
        return proc.returncode, out or b"", err or b""

    def _adb(self, *more: str, timeout: Optional[float] = 15.0) -> Tuple[int, bytes, bytes]:
        base = ["adb"]
        if self.serial:
            base += ["-s", self.serial]
        return self._run(base + list(more), timeout=timeout)

    # ---------------- connectivity ----------------
    def ensure_connected(self) -> None:
        """
        - ถ้าเป็น host:port → พยายาม adb connect จนกว่าจะ get-state == device
        - ถ้าเป็น usb/serial → เช็ค get-state
        """
        # fast path: already device?
        if self.is_online():
            return

        # host:port → try connect a few times
        if self.hostport:
            for _ in range(self.connect_retries):
                rc, _, _ = self._run(["adb", "connect", self.hostport], timeout=7.0)
                time.sleep(self.retry_delay)
                if self.is_online():
                    return
            raise ADBError(f"adb connect {self.hostport} failed; device not online")
        else:
            # usb/serial: just recheck
            if not self.is_online():
                raise ADBError(f"adb device {self.serial} is not online")

    def is_online(self) -> bool:
        rc, out, _ = self._adb("get-state", timeout=5.0)
        if rc != 0: 
            return False
        return out.strip() == b"device"

    # ---------------- features ----------------
    def screencap(self) -> np.ndarray:
        """
        คืน BGR image (numpy) หรือ raise ADBError ถ้าไม่ได้ไบต์ภาพ
        """
        self.ensure_connected()
        rc, data, err = self._adb("exec-out", "screencap", "-p", timeout=10.0)
        if rc != 0 or not data:
            raise ADBError(f"screencap failed (rc={rc}) {err.decode('utf-8', 'ignore')}")
        img = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise ADBError("screencap decode returned None (empty buffer)")
        return img
