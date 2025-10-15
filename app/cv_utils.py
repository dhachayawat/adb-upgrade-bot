import os
import time
import cv2
import numpy as np

from app import config as C
from app import adb as ADB  # ใช้เป็นโมดูล แล้วเช็คว่ามีเมธอดอะไรให้เรียกได้บ้าง

_tpl_cache = {}

# ========== Screencap adapter ==========
def screencap_bgr(save_tag: str | None = None):
    """
    ดึงภาพจาก ADB แล้วคืนเป็นภาพ BGR (numpy.ndarray).
    รองรับได้ทั้ง:
      - ADB.screencap_bgr()  -> ndarray (BGR)
      - ADB.screencap()      -> bytes (PNG) หรือ ndarray (BGR)
    """
    img = None

    # 1) ถ้ามีฟังก์ชันชื่อ screencap_bgr ใช้อันนี้ก่อน
    if hasattr(ADB, "screencap_bgr"):
        img = ADB.screencap_bgr(save_tag=save_tag)

    # 2) fallback: มีแค่ screencap
    elif hasattr(ADB, "screencap"):
        data = ADB.screencap(save_tag=save_tag)
        if isinstance(data, bytes):
            # data เป็น PNG bytes -> แปลงเป็น BGR
            arr = np.frombuffer(data, np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        else:
            # สมมติว่าเป็น ndarray (BGR) อยู่แล้ว
            img = data

    if img is None:
        raise RuntimeError("screencap_bgr(): cannot obtain frame from ADB")

    # บันทึกลงไฟล์เพื่อดีบัก ถ้าตั้งค่าไว้
    if save_tag and C.SAVE_SCREENCAP:
        os.makedirs(C.DEBUG_DIR, exist_ok=True)
        path = os.path.join(
            C.DEBUG_DIR,
            f"{C.SCREENCAP_PREFIX}_{save_tag}_{time.strftime('%Y%m%d-%H%M%S')}.png",
        )
        cv2.imwrite(path, img)

    return img


# ========== Template cache / helpers ==========
def read_tpl(name: str):
    """
    อ่านเทมเพลตจากโฟลเดอร์ templates (cache ไว้ในหน่วยความจำ)
    รองรับ RGBA (ถ้ามี alpha)
    """
    if name in _tpl_cache:
        return _tpl_cache[name]
    p = os.path.join(C.TEMPLATES_DIR, name)
    img = cv2.imread(p, cv2.IMREAD_UNCHANGED)  # รองรับ alpha
    _tpl_cache[name] = img
    return img


def _split_rgba_to_bgr_mask(tpl_rgba):
    """
    รับภาพเทมเพลต (อาจเป็น RGBA) คืน (BGR, mask) สำหรับ matchTemplate
    mask จะมาจาก alpha channel (threshold > 10)
    """
    if tpl_rgba is None:
        return None, None
    if tpl_rgba.ndim == 3 and tpl_rgba.shape[2] == 4:
        bgr = cv2.cvtColor(tpl_rgba, cv2.COLOR_BGRA2BGR)
        alpha = tpl_rgba[:, :, 3]
        mask = cv2.threshold(alpha, 10, 255, cv2.THRESH_BINARY)[1]
        return bgr, mask
    return tpl_rgba, None


def _prep_gray_v(img_bgr):
    """
    แปลงเป็น HSV แล้วใช้เฉพาะ channel V + blur เล็กน้อย
    ช่วยให้ทนต่อความต่างเฉดสีมากขึ้น
    """
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    v = hsv[:, :, 2]
    v = cv2.GaussianBlur(v, (3, 3), 0)
    return v


def match_center_multiscale(
    roi_bgr,
    tpl_rgba,
    thr=0.75,
    scales=(0.90, 0.95, 1.00, 1.05, 1.10),
    save_debug_tag=None,
):
    """
    แมตช์เทมเพลตหลายสเกล คืน ((cx,cy), score) ถ้าเกิน threshold
    ถ้าต่ำกว่า thr จะคืน (None, best_score)
    """
    if tpl_rgba is None or roi_bgr is None:
        return (None, 0.0)

    tpl_bgr, tpl_mask = _split_rgba_to_bgr_mask(tpl_rgba)
    roi_v = _prep_gray_v(roi_bgr)

    best_pt, best_score, dbg_mat = None, -1.0, None

    for s in scales:
        t = cv2.resize(tpl_bgr, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        tm = None
        if tpl_mask is not None:
            tm = cv2.resize(
                tpl_mask, (t.shape[1], t.shape[0]), interpolation=cv2.INTER_NEAREST
            )
        tv = _prep_gray_v(t)

        # บาง OpenCV ไม่รองรับ mask -> ลองไม่มี mask เป็น fallback
        try:
            res = cv2.matchTemplate(roi_v, tv, cv2.TM_CCOEFF_NORMED, mask=tm)
        except Exception:
            res = cv2.matchTemplate(roi_v, tv, cv2.TM_CCOEFF_NORMED)

        minVal, maxVal, minLoc, maxLoc = cv2.minMaxLoc(res)
        if maxVal > best_score:
            cx = maxLoc[0] + t.shape[1] // 2
            cy = maxLoc[1] + t.shape[0] // 2
            best_pt, best_score, dbg_mat = (cx, cy), float(maxVal), res

    if save_debug_tag and dbg_mat is not None:
        os.makedirs(C.DEBUG_DIR, exist_ok=True)
        heat = (np.clip(dbg_mat, 0, 1) * 255).astype("uint8")
        heat = cv2.applyColorMap(heat, cv2.COLORMAP_JET)
        cv2.imwrite(
            os.path.join(C.DEBUG_DIR, f"{save_debug_tag}_heat_{int(time.time())}.png"),
            heat,
        )

    if best_score >= thr:
        return (best_pt, best_score)
    return (None, best_score)
