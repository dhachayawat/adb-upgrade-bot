# app/adb.py
import os
import random
import subprocess
import time
from typing import Tuple, List, Optional

from app import config as C

ADB_BIN = os.getenv("ADB_BIN", "adb")
DEVICE = C.DEVICE  # เช่น "host.docker.internal:5605"

# ---------------------------
# Internal helpers
# ---------------------------

def _run(cmd: list, timeout: int = 5) -> Tuple[int, bytes, bytes]:
    """
    รันคำสั่ง (list) แล้วคืน (returncode, stdout_bytes, stderr_bytes)
    """
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as e:
        return 124, b"", f"timeout: {e}".encode()


def _adb_cmd(args: list, timeout: int = 5) -> Tuple[int, bytes, bytes]:
    """
    รันคำสั่ง ADB ด้วย -s <DEVICE>
    """
    cmd = [ADB_BIN, "-s", DEVICE] + args
    return _run(cmd, timeout=timeout)


def _text(b: bytes) -> str:
    return b.decode(errors="ignore")


# ---------------------------
# Connection / Device utils
# ---------------------------

def devices() -> List[str]:
    """
    คืนรายการ device จาก `adb devices`
    """
    rc, out, err = _run([ADB_BIN, "devices"], timeout=5)
    if rc != 0:
        return []
    lines = _text(out).splitlines()
    devs = []
    for ln in lines[1:]:
        ln = ln.strip()
        if not ln:
            continue
        if "\t" in ln:
            serial, state = ln.split("\t", 1)
            if state.strip() == "device":
                devs.append(serial.strip())
    return devs


def ensure_connected(retry: int = 2, delay: float = 0.8) -> None:
    """
    ถ้า DEVICE เป็น host:port -> เรียก `adb connect` (เงียบหากเชื่อมต่ออยู่แล้ว)
    จากนั้นเช็คว่าโผล่ใน `adb devices` หรือไม่
    """
    if ":" in DEVICE:
        for i in range(max(1, retry)):
            rc, out, err = _run([ADB_BIN, "connect", DEVICE], timeout=5)
            # ไม่ต้องซีเรียสกับ rc เพราะถ้า connected แล้วมักข้อความ "already connected"
            time.sleep(delay)

    # ตรวจว่ามีจริง
    devs = devices()
    if DEVICE not in devs:
        # บางที adb devices แสดงเป็น <host:port> หรือ <ip:port> ตรงตัว
        # ถ้า BlueStacks รายงานเป็น 'host.docker.internal:5605' ก็ต้องตรงกัน
        # ถ้าไม่เจอ ให้ลอง shell ง่ายๆ เพื่อตรวจอีกที
        try:
            _ = shell("echo ok", timeout=3)
        except Exception as e:
            raise RuntimeError(f"ADB not connected to {DEVICE}: {e}")


def wait_for_device(timeout_sec: int = 10) -> bool:
    """
    รอจน DEVICE พร้อมใช้งานในกรอบเวลาที่กำหนด
    """
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            shell("echo ok", timeout=2)
            return True
        except Exception:
            time.sleep(0.5)
    return False


# ---------------------------
# Shell & input
# ---------------------------

def shell(cmd, timeout: int = 5) -> str:
    """
    เรียก 'adb -s <dev> shell <cmd>' แล้วคืน text decode
    """
    args = ["shell"] + (cmd if isinstance(cmd, list) else [cmd])
    rc, out, err = _adb_cmd(args, timeout=timeout)
    if rc != 0:
        raise RuntimeError(f"adb shell failed rc={rc} err={_text(err)[:200]}")
    return _text(out)


def tap(x: int, y: int, quiet: bool = False) -> None:
    """
    แตะหน้าจอพิกัด (x, y)
    """
    if not quiet:
        print(f"[ADB] input tap {int(x)} {int(y)}")
    rc, out, err = _adb_cmd(["shell", "input", "tap", str(int(x)), str(int(y))], timeout=3)
    if rc != 0:
        raise RuntimeError(f"adb tap failed: {_text(err)[:200]}")


def swipe(x1: int, y1: int, x2: int, y2: int, ms: int = 200, quiet: bool = False) -> None:
    """
    ปัดจาก (x1,y1) ไป (x2,y2) ใช้เวลา ms มิลลิวินาที
    """
    if not quiet:
        print(f"[ADB] input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(ms)}")
    rc, out, err = _adb_cmd([
        "shell", "input", "swipe",
        str(int(x1)), str(int(y1)), str(int(x2)), str(int(y2)), str(int(ms))
    ], timeout=4)
    if rc != 0:
        raise RuntimeError(f"adb swipe failed: {_text(err)[:200]}")


def keyevent(code: int, quiet: bool = False) -> None:
    """
    ส่ง keyevent เช่น BACK (4)
    """
    if not quiet:
        print(f"[ADB] input keyevent {code}")
    rc, out, err = _adb_cmd(["shell", "input", "keyevent", str(code)], timeout=3)
    if rc != 0:
        raise RuntimeError(f"adb keyevent failed: {_text(err)[:200]}")


def sleep_rand(a: float, b: float) -> float:
    """
    หน่วงเวลาแบบสุ่มระหว่าง a..b วินาที
    """
    t = random.uniform(a, b)
    time.sleep(t)
    return t


# ---------------------------
# Screencap (สำคัญสำหรับ CV)
# ---------------------------

def screencap(timeout: int = 5, save_tag: Optional[str] = None) -> bytes:
    """
    ดึงภาพหน้าจอเป็น PNG bytes (เร็วและเสถียรกว่า 'adb shell screencap -p' เฉยๆ บนบางระบบ)
    ใช้ `exec-out` เพื่อได้ byte stream ตรง
    """
    rc, out, err = _adb_cmd(["exec-out", "screencap", "-p"], timeout=timeout)
    if rc != 0 or not out:
        # fallback: บางอุปกรณ์อาจไม่รองรับ exec-out
        rc2, out2, err2 = _adb_cmd(["shell", "screencap", "-p"], timeout=timeout)
        if rc2 != 0 or not out2:
            raise RuntimeError(f"adb screencap failed: {_text(err or err2)[:200]}")
        out = out2

    # เซฟไฟล์ดีบักถ้าต้องการ (PNG ตรง)
    if save_tag and C.SAVE_SCREENCAP:
        try:
            os.makedirs(C.DEBUG_DIR, exist_ok=True)
            path = os.path.join(
                C.DEBUG_DIR,
                f"{C.SCREENCAP_PREFIX}_{save_tag}_{time.strftime('%Y%m%d-%H%M%S')}.png"
            )
            with open(path, "wb") as f:
                f.write(out)
        except Exception as _e:
            print(f"[ADB] screencap save warn: {_e}")

    return out


def screencap_bgr(timeout: int = 5, save_tag: Optional[str] = None):
    """
    คืนภาพเป็น numpy.ndarray (BGR) สำหรับ OpenCV
    """
    import numpy as np
    import cv2

    png = screencap(timeout=timeout, save_tag=save_tag)  # PNG bytes
    arr = np.frombuffer(png, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("cv2.imdecode returned None (invalid PNG from screencap)")
    return img


# ---------------------------
# One-time connect on import
# ---------------------------

try:
    ensure_connected()
    # optional: รอให้พร้อมสักแป๊บ
    wait_for_device(5)
except Exception as _e:
    # ไม่หยุดโปรแกรม แต่แจ้งเตือน
    print(f"[ADB] ensure_connected warn: {_e}")
