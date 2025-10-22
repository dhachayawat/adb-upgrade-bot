# app/core/adb_adapter.py
from __future__ import annotations

import os
import re
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


# ดีเลย์สุ่มเล็กน้อยหลังคลิก/สไลด์
_CLICK_DELAY_MIN = _envf("CLICK_DELAY_MIN", 0.15)
_CLICK_DELAY_MAX = _envf("CLICK_DELAY_MAX", 0.30)

# retry หลุดสาย
_RETRY_MAX = _envi("ADB_RETRY_MAX", 3)
_RETRY_SLEEP = _envf("ADB_RETRY_SLEEP", 0.35)

# โหมดการ map พิกัด:
#   "none"    → ไม่ map ใด ๆ (เหมือน app/adb.py เดิม)  ← ค่าเริ่มต้น
#   "contain" → inverse contain จาก UI(_UI_Wx_UI_H) → frame → device
_MAP_MODE = os.getenv("ADB_MAP_MODE", "none").strip().lower()

# ขนาด space UI คงที่ (ใช้เฉพาะโหมด contain)
_UI_W = _envi("UI_FIXED_WIDTH", 1280)
_UI_H = _envi("UI_FIXED_HEIGHT", 720)

# เปิด debug mapping
_DEBUG_TAP = os.getenv("ADB_DEBUG_TAP", "0") == "1"

# ไบนารี่ adb (เผื่ออยากชี้ adb เฉพาะทาง)
_ADB_BIN = os.getenv("ADB_BIN", "adb")


def _retryable(fn):
    def wrap(self: "ADBAdapter", *a, **kw):
        last_e: Optional[Exception] = None
        for _attempt in range(1, _RETRY_MAX + 1):
            try:
                self.ensure_connected()
                return fn(self, *a, **kw)
            except ADBError as e:
                last_e = e
                emsg = str(e).lower()
                if any(k in emsg for k in [
                    "error: closed", "device offline",
                    "no devices/emulators found", "unable to connect",
                    "cannot connect"
                ]):
                    if self.hostport:
                        try:
                            self._run([_ADB_BIN, "disconnect", self.hostport], timeout=5.0)
                        except Exception:
                            pass
                        self._run([_ADB_BIN, "connect", self.hostport], timeout=7.0)
                    time.sleep(_RETRY_SLEEP)
                    continue
                break
            except Exception as e:
                last_e = e
                time.sleep(_RETRY_SLEEP)
                continue
        if last_e is not None:
            raise last_e
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

        if self.device_str.startswith("usb:"):
            self.serial = self.device_str.split(":", 1)[1]
            self.hostport: Optional[str] = None
        elif ":" in self.device_str and self.device_str.count(":") == 1:
            self.serial = self.device_str
            self.hostport = self.device_str
        else:
            self.serial = self.device_str
            self.hostport = None

        # ขนาดเฟรมล่าสุด (จาก screencap) เพื่อใช้ map (เฉพาะโหมด contain)
        self._last_frame_w: Optional[int] = None
        self._last_frame_h: Optional[int] = None

    # ---------- helpers ----------
    def reconnect(self) -> None:
        if self.hostport:
            self._run([_ADB_BIN, "disconnect", self.hostport], timeout=5.0)
            self._run([_ADB_BIN, "connect", self.hostport], timeout=7.0)
            t0 = time.time()
            while time.time() - t0 < 4.0:
                if self.is_online():
                    return
                time.sleep(0.3)
        else:
            t0 = time.time()
            while time.time() - t0 < 4.0:
                if self.is_online():
                    return
                time.sleep(0.3)

    def wake_and_unlock(self) -> None:
        try:
            self.ensure_connected()
            self._adb("shell", "input", "keyevent", "224", timeout=5.0)  # WAKE
            time.sleep(0.2)
            self._adb("shell", "input", "swipe", "400", "1000", "400", "400", "200", timeout=5.0)
            time.sleep(0.2)
        except Exception:
            pass

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
        base = [_ADB_BIN]
        if getattr(self, "serial", None):
            base += ["-s", self.serial]
        return self._run(base + list(more), timeout=timeout)

    def _sleep_click_delay(self):
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
                self._run([_ADB_BIN, "disconnect", self.hostport], timeout=5.0)
                self._run([_ADB_BIN, "connect", self.hostport], timeout=7.0)
                self._adb("wait-for-device", timeout=10.0)
                if self.is_online():
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

    # ---------- device metrics (ใช้บางโหมด) ----------
    def _get_device_metrics(self) -> tuple[int, int, int]:
        dev_w, dev_h, rotation = 1080, 1920, 0
        rc, out, _ = self._adb("shell", "wm", "size", timeout=3.0)
        if rc == 0 and out:
            m = re.search(rb"Physical size:\s*(\d+)x(\d+)", out)
            if m:
                dev_w, dev_h = int(m.group(1)), int(m.group(2))
        rc2, out2, _ = self._adb("shell", "dumpsys", "display", timeout=4.0)
        if rc2 == 0 and out2:
            m2 = re.search(rb"mCurrentRotation\s*=\s*(\d+)", out2)
            if m2:
                rotation = int(m2.group(1))
        return dev_w, dev_h, rotation

    # ---------- mapping (เฉพาะโหมด contain) ----------
    @staticmethod
    def _invert_contain_mapping(
        x_ui: float, y_ui: float, ui_w: int, ui_h: int, frame_w: int, frame_h: int
    ) -> tuple[int, int]:
        s = min(ui_w / float(frame_w), ui_h / float(frame_h))
        off_x = (ui_w - s * frame_w) / 2.0
        off_y = (ui_h - s * frame_h) / 2.0
        xf = (float(x_ui) - off_x) / s
        yf = (float(y_ui) - off_y) / s
        xf = max(0.0, min(xf, frame_w - 1.0))
        yf = max(0.0, min(yf, frame_h - 1.0))
        return int(xf), int(yf)

    def _map_ui_point_to_device(self, x_ui: int, y_ui: int, dev_w: int, dev_h: int, rotation: int) -> tuple[int, int]:
        # ถ้าโหมด none → ยิงพิกัดตรง ๆ
        if _MAP_MODE == "none":
            if _DEBUG_TAP:
                print(f"[ADB MAP] mode=none ui({x_ui},{y_ui}) -> dev({x_ui},{y_ui})", flush=True)
            return int(x_ui), int(y_ui)

        # โหมด contain
        fw = self._last_frame_w or dev_w
        fh = self._last_frame_h or dev_h
        xf, yf = self._invert_contain_mapping(x_ui, y_ui, _UI_W, _UI_H, fw, fh)
        xd, yd = xf, yf  # frame->device ส่วนใหญ่ 1:1
        xd = max(0, min(int(xd), dev_w - 1))
        yd = max(0, min(int(yd), dev_h - 1))
        if _DEBUG_TAP:
            print(f"[ADB MAP] mode=contain ui=({_UI_W}x{_UI_H}) -> frame=({fw}x{fh}) -> dev=({dev_w}x{dev_h}) rot={rotation} "
                  f"ui({x_ui},{y_ui}) -> frame({xf},{yf}) -> dev({xd},{yd})", flush=True)
        return xd, yd

    # ---------------- features ----------------
    @_retryable
    def screencap(self) -> np.ndarray:
        # เหมือน adb.py: exec-out ก่อน, มี fallback shell
        rc, data, err = self._adb("exec-out", "screencap", "-p", timeout=10.0)
        if rc != 0 or not data:
            rc2, out2, err2 = self._adb("shell", "screencap", "-p", timeout=10.0)
            if rc2 != 0 or not out2:
                raise ADBError(f"screencap failed (rc={rc}) {(err or err2 or b'').decode('utf-8','ignore')}")
            data = out2
        img = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise ADBError("screencap decode returned None (empty buffer)")
        h, w = img.shape[:2]
        self._last_frame_w = int(w)
        self._last_frame_h = int(h)
        return img

    @_retryable
    def tap(self, x: int, y: int) -> None:
        """
        ค่าเริ่มต้น: พฤติกรรมเหมือน app/adb.py → ยิงพิกัดตรง ๆ
        หากต้องการ map แบบ contain: ตั้ง ADB_MAP_MODE=contain
        """
        self.wake_and_unlock()
        try:
            _ = self.screencap()
            time.sleep(0.10)
        except Exception:
            pass
        try:
            self._adb("shell", "true", timeout=3.0)
        except Exception:
            pass

        dev_w, dev_h, rotation = self._get_device_metrics()
        tx, ty = self._map_ui_point_to_device(x, y, dev_w, dev_h, rotation)
        xs, ys = str(int(tx)), str(int(ty))

        attempts = int(os.getenv("ADB_TAP_LOCAL_RETRY", "3"))
        backoff = float(os.getenv("ADB_TAP_BACKOFF", "0.4"))
        last_err = b""

        # ใช้รูปแบบเดียวกับ adb.py เป็นอันดับแรก
        tap_cmds = [
            ["shell", "input", "tap", xs, ys],
            ["shell", "sh", "-lc", f"input tap {xs} {ys}"],
            ["shell", "input", "touchscreen", "tap", xs, ys],
            ["shell", "input", "swipe", xs, ys, xs, ys, "160"],
        ]

        for _ in range(attempts):
            for cmd in tap_cmds:
                rc, _, err = self._adb(*cmd, timeout=6.0)
                if rc == 0:
                    self._sleep_click_delay()
                    return
                last_err = err or last_err
            if any(k in (last_err or b"").lower() for k in [b"closed", b"offline", b"no devices", b"unable to connect"]):
                self.reconnect()
            time.sleep(backoff)

        raise ADBError(
            f"tap failed mode={_MAP_MODE} @ui({x},{y}) -> dev({xs},{ys}) dev={dev_w}x{dev_h} frame={self._last_frame_w}x{self._last_frame_h} "
            f"{(last_err or b'').decode('utf-8','ignore')}"
        )

    @_retryable
    def swipe(self, x: int, y: int, *, dx: int = 0, dy: int = 0, ms: int = 300) -> None:
        """
        ค่าเริ่มต้น: ยิงตรง (เหมือน adb.py) | โหมด contain จะ map ก่อน
        """
        self.wake_and_unlock()
        try:
            _ = self.screencap()
            time.sleep(0.10)
        except Exception:
            pass
        try:
            self._adb("shell", "true", timeout=3.0)
        except Exception:
            pass

        dev_w, dev_h, rotation = self._get_device_metrics()
        x2, y2 = x + dx, y + dy

        sx, sy = self._map_ui_point_to_device(x,  y,  dev_w, dev_h, rotation)
        ex, ey = self._map_ui_point_to_device(x2, y2, dev_w, dev_h, rotation)

        durs = str(max(1, int(ms)))
        x1s, y1s, x2s, y2s = map(str, (sx, sy, ex, ey))

        attempts = int(os.getenv("ADB_SWIPE_LOCAL_RETRY", "3"))
        backoff = float(os.getenv("ADB_SWIPE_BACKOFF", "0.4"))
        last_err = b""

        cmds = [
            ["shell", "input", "swipe", x1s, y1s, x2s, y2s, durs],
            ["shell", "sh", "-lc", f"input swipe {x1s} {y1s} {x2s} {y2s} {durs}"],
            ["shell", "input", "touchscreen", "swipe", x1s, y1s, x2s, y2s, durs],
        ]

        for _ in range(attempts):
            for c in cmds:
                rc, _, err = self._adb(*c, timeout=8.0)
                if rc == 0:
                    self._sleep_click_delay()
                    return
                last_err = err or last_err
            if any(k in (last_err or b"").lower() for k in [b"closed", b"offline", b"no devices", b"unable to connect"]):
                self.reconnect()
            time.sleep(backoff)

        raise ADBError(
            f"swipe failed mode={_MAP_MODE} @ui({x},{y})->ui({x2},{y2}) dev({sx},{sy})->({ex},{ey}) dev={dev_w}x{dev_h} "
            f"frame={self._last_frame_w}x{self._last_frame_h} {(last_err or b'').decode('utf-8','ignore')}"
        )

    @_retryable
    def keyevent(self, code: int | str) -> None:
        rc, _, err = self._adb("shell", "input", "keyevent", str(code), timeout=5.0)
        if rc != 0:
            raise ADBError(f"keyevent {code} failed {err.decode('utf-8','ignore')}")
        self._sleep_click_delay()
