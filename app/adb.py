# app/adb.py
import os
import random
import subprocess
import time
import numpy as np
import cv2

from app import config as C

ADB_BIN = os.getenv("ADB_BIN", "adb")
DEVICE = C.DEVICE  # เช่น "host.docker.internal:5605"

def _adb_cmd(args, timeout=5):
    cmd = [ADB_BIN, "-s", DEVICE] + args
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr

def ensure_connected():
    if ":" in DEVICE:
        # adb connect ถ้าเป็น host:port
        try:
            subprocess.run([ADB_BIN, "connect", DEVICE], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
        except Exception as e:
            print(f"[ADB] connect warn: {e}")

def shell(cmd, timeout=5):
    rc, out, err = _adb_cmd(["shell"] + (cmd if isinstance(cmd, list) else [cmd]), timeout=timeout)
    if rc != 0:
        raise RuntimeError(f"adb shell failed rc={rc} err={err.decode(errors='ignore')[:200]}")
    return out.decode(errors="ignore")

def tap(x, y, quiet=False):
    if not quiet:
        print(f"[ADB] input tap {int(x)} {int(y)}")
    rc, out, err = _adb_cmd(["shell", "input", "tap", str(int(x)), str(int(y))], timeout=4)
    if rc != 0:
        raise RuntimeError(f"adb tap failed: {err.decode(errors='ignore')[:200]}")

def swipe(x1, y1, x2, y2, ms=200, quiet=False):
    if not quiet:
        print(f"[ADB] input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(ms)}")
    rc, out, err = _adb_cmd([
        "shell", "input", "swipe",
        str(int(x1)), str(int(y1)), str(int(x2)), str(int(y2)), str(int(ms))
    ], timeout=6)
    if rc != 0:
        raise RuntimeError(f"adb swipe failed: {err.decode(errors='ignore')[:200]}")

def sleep_rand(a, b):
    t = random.uniform(a, b)
    time.sleep(t)
    return t

def keyevent(code, quiet=False):
    if not quiet:
        print(f"[ADB] input keyevent {code}")
    rc, out, err = _adb_cmd(["shell", "input", "keyevent", str(code)], timeout=3)
    if rc != 0:
        raise RuntimeError(f"adb keyevent failed: {err.decode(errors='ignore')[:200]}")

def screencap(save_tag=None):
    """
    คืน PNG bytes จาก 'screencap -p'
    """
    rc, out, err = _adb_cmd(["exec-out", "screencap", "-p"], timeout=8)
    if rc != 0 or not out:
        # fallback: ผ่าน shell (บางอุปกรณ์)
        rc2, out2, err2 = _adb_cmd(["shell", "screencap", "-p"], timeout=8)
        if rc2 != 0 or not out2:
            raise RuntimeError(f"adb screencap failed: {err.decode(errors='ignore')[:200]}")
        data = out2
    else:
        data = out

    if save_tag and getattr(C, "SAVE_SCREENCAP", False):
        os.makedirs(C.DEBUG_DIR, exist_ok=True)
        p = os.path.join(C.DEBUG_DIR, f"{C.SCREENCAP_PREFIX}_{save_tag}_{time.strftime('%Y%m%d-%H%M%S')}.png")
        try:
            with open(p, "wb") as f:
                f.write(data)
        except Exception:
            pass
    return data

def screencap_bgr(save_tag=None):
    """
    คืนภาพ BGR (ndarray) โดยถอดจาก PNG bytes
    """
    data = screencap(save_tag=save_tag)
    arr = np.frombuffer(data, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("cv2.imdecode failed")
    return img

# เรียกครั้งแรกเพื่อเชื่อมต่อ
try:
    ensure_connected()
except Exception as _e:
    print(f"[ADB] ensure_connected warn: {_e}")
