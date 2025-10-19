# app/core/adb_adapter.py
from __future__ import annotations
import subprocess
import shlex
import tempfile
import os
import cv2
import numpy as np

class ADBAdapter:
    """
    อะแดปเตอร์สั่ง ADB แบบง่าย ใช้ได้ทั้ง host:port และ usb:<serial>
    - screencap() -> ndarray (BGR)
    - tap(x,y), swipe(x,y,dy,ms)
    """
    def __init__(self, device: str) -> None:
        # device: 'host:port' หรือ 'usb:<serial>'
        if device.startswith("usb:"):
            self.serial = device.split(":", 1)[1]
        else:
            self.serial = device  # host:port
        self.adb = "adb"

    def _run(self, cmd: str, timeout: float = 5.0) -> subprocess.CompletedProcess:
        args = f'{self.adb} -s {shlex.quote(self.serial)} {cmd}'
        return subprocess.run(args, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)

    def tap(self, x: int, y: int) -> None:
        self._run(f"shell input tap {int(x)} {int(y)}")

    def swipe(self, x: int, y: int, dx: int = 0, dy: int = 0, ms: int = 250) -> None:
        x2, y2 = int(x + dx), int(y + dy)
        self._run(f"shell input swipe {int(x)} {int(y)} {x2} {y2} {int(ms)}")

    def screencap(self):
        """
        ดึงสกรีนช็อต แล้วคืนเป็นภาพ BGR (ndarray)
        ใช้รูปแบบ 'adb exec-out screencap -p' (PNG)
        """
        proc = self._run("exec-out screencap -p", timeout=8.0)
        if proc.returncode != 0 or not proc.stdout:
            # fallback: ดึงเป็นไฟล์ชั่วคราว (กรณีบางอุปกรณ์)
            tmp_remote = "/sdcard/__tmp_cap.png"
            self._run(f"shell screencap -p {tmp_remote}", timeout=8.0)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                local = tmp.name
            try:
                self._run(f"pull {tmp_remote} {local}", timeout=8.0)
                data = open(local, "rb").read()
            finally:
                try: os.remove(local)
                except: pass
            self._run(f"shell rm {tmp_remote}")
        else:
            data = proc.stdout
        img = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        return img
