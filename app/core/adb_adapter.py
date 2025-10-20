# app/core/adb_adapter.py
from __future__ import annotations
import os
import time
import random
import subprocess
from typing import Optional, Tuple

import cv2
import numpy as np


class ADBError(RuntimeError):
    """ข้อผิดพลาดจากคำสั่ง ADB"""


def _envf(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def _envi(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


# ดีเลย์สุ่มเล็กน้อยหลังคลิก/สไลด์ เพื่อไม่ให้สั่งถี่เกินไป
_CLICK_DELAY_MIN = _envf("CLICK_DELAY_MIN", 0.15)
_CLICK_DELAY_MAX = _envf("CLICK_DELAY_MAX", 0.30)

# นโยบาย retry เมื่อเจอการเชื่อมต่อหลุด (error: closed / offline)
_RETRY_MAX = _envi("ADB_RETRY_MAX", 3)
_RETRY_SLEEP = _envf("ADB_RETRY_SLEEP", 0.35)


def _retryable(fn):
    """
    เดโคเรเตอร์สำหรับเมธอดที่คุยกับอุปกรณ์:
    - ensure_connected() ทุกครั้ง
    - หากเจอ error ที่สื่อว่าการเชื่อมต่อหลุด/offline → ลอง reconnect แล้ว retry ตามจำนวนรอบที่กำหนด
    """
    def wrap(self: "ADBAdapter", *a, **kw):
        last_e: Optional[Exception] = None
        for attempt in range(1, _RETRY_MAX + 1):
            try:
                # เช็ค/เชื่อมต่อก่อนทุกครั้ง
                self.ensure_connected()
                return fn(self, *a, **kw)
            except ADBError as e:
                last_e = e
                emsg = str(e).lower()
                # เฉพาะ error เชิงการเชื่อมต่อ ค่อยลอง reconnect + retry
                if any(k in emsg for k in [
                    "error: closed", "device offline",
                    "no devices/emulators found", "unable to connect",
                    "cannot connect"
                ]):
                    # พยายาม reconnect เฉพาะกรณี host:port
                    if self.hostport:
                        try:
                            self._run(["adb", "disconnect", self.hostport], timeout=5.0)
                        except Exception:
                            pass
                        self._run(["adb", "connect", self.hostport], timeout=7.0)
                    time.sleep(_RETRY_SLEEP)
                    continue
                # ถ้าเป็น error แบบอื่น ไม่ต้อง retry
                break
            except Exception as e:
                last_e = e
                time.sleep(_RETRY_SLEEP)
                continue
        # หมดสิทธิ์แล้ว ยังพัง → โยน error สุดท้าย
        if last_e is not None:
            raise last_e
        # กรณีไม่เข้าเงื่อนไขใด ๆ (ไม่ควรเกิด)
        return None
    return wrap


class ADBAdapter:
    """
    ตัวห่อคำสั่ง ADB สำหรับอุปกรณ์ 1 เครื่อง

    device_str:
      - "host:port"        (เช่น host.docker.internal:5595)
      - "usb:<serial>"     (เช่น usb:R3CN30XXXX)
      - "<serial>"         (เช่น emulator-5554 หรือหมายเลข serial ตรง ๆ)
    """
    def __init__(self, device_str: str, connect_retries: int = 5, retry_delay: float = 0.6):
        self.device_str = (device_str or "").strip()
        self.connect_retries = connect_retries
        self.retry_delay = retry_delay

        # แยกชนิด serial/hostport
        if self.device_str.startswith("usb:"):
            self.serial = self.device_str.split(":", 1)[1]
            self.hostport: Optional[str] = None
        elif ":" in self.device_str and self.device_str.count(":") == 1:
            # host:port
            self.serial = self.device_str
            self.hostport = self.device_str
        else:
            # serial ตรง ๆ
            self.serial = self.device_str
            self.hostport = None

    # ---------- helpers ใหม่ ----------
    def reconnect(self) -> None:
        """พยายาม reconnect แบบแรงกว่าปกติ"""
        if self.hostport:
            # disconnect → connect → รอให้ get-state เป็น device
            self._run(["adb", "disconnect", self.hostport], timeout=5.0)
            self._run(["adb", "connect", self.hostport], timeout=7.0)
            t0 = time.time()
            while time.time() - t0 < 4.0:
                if self.is_online():
                    return
                time.sleep(0.3)
        else:
            # usb/serial: แค่รอให้กลับมา online
            t0 = time.time()
            while time.time() - t0 < 4.0:
                if self.is_online():
                    return
                time.sleep(0.3)
        # ถ้ายังไม่ขึ้น ปล่อยให้ ensure_connected() จัดการ error เอง

    def wake_and_unlock(self) -> None:
        """ปลุกจอ + ปลดล็อกแบบง่าย (บาง emulator ต้องปลุกก่อน input ถึงติด)"""
        try:
            self.ensure_connected()
            # ปลุกจอ
            self._adb("shell", "input", "keyevent", "224", timeout=5.0)  # POWER
            time.sleep(0.2)
            # ลองสไลด์ขึ้นเล็กน้อยเพื่อปลดล็อก (ถ้ามี lockscreen)
            self._adb("shell", "input", "swipe", "400", "1000", "400", "400", "200", timeout=5.0)
            time.sleep(0.2)
        except Exception:
            # ไม่ต้อง fail งานหลัก ถ้าปลุกไม่สำเร็จ
            pass

    # ---------------- low-level ----------------
    def _run(self, args: list[str], timeout: Optional[float] = 15.0) -> Tuple[int, bytes, bytes]:
        """
        รันคำสั่ง subprocess แบบดิบ ๆ
        """
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
        return proc.returncode, out or b"", err or b""

    def _adb(self, *more: str, timeout: Optional[float] = 15.0) -> Tuple[int, bytes, bytes]:
        """
        เรียก adb พร้อมชี้ serial เครื่องปลายทางเสมอ
        """
        base = ["adb"]
        if self.serial:
            base += ["-s", self.serial]
        return self._run(base + list(more), timeout=timeout)

    def _sleep_click_delay(self):
        """ดีเลย์สุ่มหลังคำสั่งที่เป็น gesture"""
        d = random.uniform(_CLICK_DELAY_MIN, _CLICK_DELAY_MAX)
        if d > 0:
            time.sleep(d)

    # ---------------- connectivity ----------------
    def ensure_connected(self) -> None:
        if self.is_online():
            return

        if self.hostport:
            last_err = b""
            for _ in range(self.connect_retries):
                # เคลียร์ช่องค้าง
                self._run(["adb", "disconnect", self.hostport], timeout=5.0)

                # ต่อใหม่
                self._run(["adb", "connect", self.hostport], timeout=7.0)
                # รอจนเป็น device จริง
                self._adb("wait-for-device", timeout=10.0)
                if self.is_online():
                    # อุ่น shell หนึ่งคำสั่ง กัน tap หล่น closed
                    rc, _, err = self._adb("shell", "true", timeout=3.0)
                    if rc == 0:
                        return
                    last_err = err or b""
                time.sleep(self.retry_delay)
            raise ADBError(f"adb connect {self.hostport} failed; device not online {last_err.decode('utf-8','ignore')}")
        else:
            self._adb("wait-for-device", timeout=10.0)
            if not self.is_online():
                raise ADBError(f"adb device {self.serial} is not online")


    def is_online(self) -> bool:
        rc, out, _ = self._adb("get-state", timeout=5.0)
        return (rc == 0) and (out.strip() == b"device")

    # ---------------- features ----------------
    @_retryable
    def screencap(self) -> np.ndarray:
        """
        ดึงภาพหน้าจอ (PNG) แล้วคืนเป็น BGR ndarray (OpenCV)
        """
        # ensure_connected() ถูกเรียกในเดโคเรเตอร์แล้ว
        rc, data, err = self._adb("exec-out", "screencap", "-p", timeout=10.0)
        if rc != 0 or not data:
            raise ADBError(f"screencap failed (rc={rc}) {err.decode('utf-8','ignore')}")
        img = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise ADBError("screencap decode returned None (empty buffer)")
        return img

    @_retryable
    def tap(self, x:int, y:int)->None:
        self.wake_and_unlock()
        try: _ = self.screencap(); time.sleep(0.10)
        except: pass
        try: self._adb("shell","true",timeout=3.0)
        except: pass

        xs, ys = str(int(x)), str(int(y))
        attempts = int(os.getenv("ADB_TAP_LOCAL_RETRY","5"))
        backoff  = float(os.getenv("ADB_TAP_BACKOFF","0.6"))
        last_err = b""
        for _ in range(attempts):
            rc,_,err = self._adb("shell","sh","-lc",f"input tap {xs} {ys}",timeout=6.0)
            if rc==0: self._sleep_click_delay(); return
            rc2,_,err2 = self._adb("shell","input","tap",xs,ys,timeout=6.0)
            if rc2==0: self._sleep_click_delay(); return
            rc3,_,err3 = self._adb("shell","input","swipe",xs,ys,xs,ys,"120",timeout=6.0)
            if rc3==0: self._sleep_click_delay(); return
            last_err = err3 or err2 or err
            if any(k in (last_err or b"").lower() for k in [b"closed",b"offline",b"no devices",b"unable to connect"]):
                self.reconnect()
            time.sleep(backoff)
        raise ADBError(f"tap failed @({x},{y}) {(last_err or b'').decode('utf-8','ignore')}")



    @_retryable
    def swipe(self, x: int, y: int, *, dx: int = 0, dy: int = 0, ms: int = 300) -> None:
        """
        ปัดนิ้วจาก (x,y) → (x+dx, y+dy) ใช้เวลา ms มิลลิวินาที
        - ปลุกจอก่อน
        - stabilize: screencap + adb shell true
        - ถ้า stderr มี closed/offline → reconnect แล้วให้เดโคเรเตอร์ retry
        """
        self.wake_and_unlock()

        # --- stabilize ก่อน input ---
        try:
            _ = self.screencap()
            time.sleep(0.10)
        except Exception:
            pass
        try:
            self._adb("shell", "true", timeout=3.0)
        except Exception:
            pass

        x1, y1 = int(x), int(y)
        x2, y2 = x1 + int(dx), y1 + int(dy)
        dur = max(1, int(ms))
        rc, _, err = self._adb(
            "shell", "input", "swipe",
            str(x1), str(y1), str(x2), str(y2), str(dur),
            timeout=8.0
        )
        if rc != 0:
            el = err.lower()
            if b"closed" in el or b"offline" in el or b"no devices" in el or b"unable to connect" in el:
                self.reconnect()
            raise ADBError(f"swipe failed @{(x1,y1)}->{(x2,y2)} dur={dur}ms {err.decode('utf-8','ignore')}")
        self._sleep_click_delay()



    @_retryable
    def keyevent(self, code: int | str) -> None:
        """
        ส่งคีย์อีเวนต์ เช่น BACK=4, ENTER=66, POWER=26
        """
        rc, _, err = self._adb("shell", "input", "keyevent", str(code), timeout=5.0)
        if rc != 0:
            raise ADBError(f"keyevent {code} failed {err.decode('utf-8','ignore')}")
        self._sleep_click_delay()
