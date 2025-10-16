# app/upgrader.py
# ลอจิกใหม่ (ตามสเปคผู้ใช้):
# 1) แตะไอเทม -> OCR ที่ slot_status เพื่ออ่านรูปแบบ [n] (เช่น [1]..[6]) *ก่อนใส่ลง*
# 2) ถ้าไม่เจอเลข หรือเลข <=4 -> ทำ "ใส่ลง" แล้วเข้าสู่โหมดอัปเกรด
#    ถ้าเลข >=5 -> ข้ามไอเทม
# 3) โหมดอัปเกรด: นับจำนวน "สำเร็จ" ให้ครบจนถึง TARGET_LEVEL (ปกติ +5)
#    - หลังแต่ละคลิก "อัปเกรด" ตรวจผลจาก overlay_abs โดยลำดับ:
#        3.1 OCR คำ "สำเร็จ"/"ล้มเหลว" (ไทย) ใน ROI
#        3.2 เทมเพลต success.png / fail.png
#        3.3 ฮิวริสติกโทนสี: ฟ้า/ขาว => success, ส้ม/แดง => fail
#    - ถ้า fail และระดับปัจจุบัน (base + successes) >= 4 -> ถือว่าแตก -> ข้ามไอเทม
# 4) ก่อนตรวจช่อง/สถานะหลัง "ใส่ลง" ให้หน่วง 0.5 วินาที

import os
import re
import time
import numpy as np
import cv2

try:
    import pytesseract
    HAVE_TESS = True
except Exception:
    HAVE_TESS = False

from app import config as C
from app import adb as ADB
from app import cv_utils as CV
from app import rtlog as LOG

# ======== REGEX ========
BRACKET_NUM_RE = re.compile(r"\[\s*(\d{1,2})\s*\]")  # จับเลขในวงเล็บเหลี่ยม [n]

# ======== I/O จอ ========
def _grab():
    """ดึงภาพหน้าจอจาก ADB เป็น BGR ndarray"""
    return CV.screencap_bgr()

def _roi_centered(img, cx, cy, w, h):
    """crop ROI โดยศูนย์กลาง (cx,cy) และขนาด (w,h)"""
    x1 = max(0, cx - w//2); y1 = max(0, cy - h//2)
    x2 = min(img.shape[1], x1 + w); y2 = min(img.shape[0], y1 + h)
    return img[y1:y2, x1:x2], (x1, y1)

def _roi_rect(img, r):
    """crop ROI โดย dict {x1,y1,w,h}"""
    return img[r["y1"]:r["y1"]+r["h"], r["x1"]:r["x1"]+r["w"]], (r["x1"], r["y1"])

# ======== ช่วย OCR ========
def _binarize(img_bgr):
    g = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    g = cv2.GaussianBlur(g, (3,3), 0)
    thr = cv2.adaptiveThreshold(
        g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV, 31, 9
    )
    return thr

def _ocr_text(gray, psm=7):
    if not HAVE_TESS:
        return ""
    cfg = f"--psm {psm}"
    txt = pytesseract.image_to_string(gray, lang="eng", config=cfg)
    return (txt or "").strip().replace("\n", " ")

# ======== อ่านระดับ [n] จาก slot_status (ก่อนใส่ลง) ========
def read_bracket_level_from_status():
    """
    อ่านเลขจาก badge/ป้ายสถานะรูปแบบ [n] (1..6) บริเวณ C.SLOT_STATUS_X/Y ขนาด C.SLOT_STATUS_ROI_W/H
    ใช้ก่อน "ใส่ลง"
    คืน int | None
    """
    img = _grab()
    w, h = C.SLOT_STATUS_ROI_W, C.SLOT_STATUS_ROI_H
    roi, (ox,oy) = _roi_centered(img, C.SLOT_STATUS_X, C.SLOT_STATUS_Y, w, h)
    thr = _binarize(roi)
    text = _ocr_text(thr, psm=7)

    lvl = None
    m = BRACKET_NUM_RE.search(text)
    if m:
        try:
            lvl = int(m.group(1))
        except:
            lvl = None

    LOG.tee(f"[OCR ก่อนใส่ลง] text='{text}' → ระดับในวงเล็บ = {lvl} (roi {ox},{oy},{w}x{h})")

    # debug
    try:
        os.makedirs(C.DEBUG_DIR, exist_ok=True)
        cv2.imwrite(os.path.join(C.DEBUG_DIR, "preinsert_status_roi.png"), roi)
        cv2.imwrite(os.path.join(C.DEBUG_DIR, "preinsert_status_thr.png"), thr)
    except Exception:
        pass

    return lvl

# ======== ตรวจ "ช่องว่าง" (หลังใส่ลง) ========
def _empty_by_template(crop_bgr):
    tpl = CV.read_tpl("slot_empty.png")
    if tpl is None:
        return (False, 0.0)
    pt, sc = CV.match_center_multiscale(
        crop_bgr, tpl, thr=C.CONF_SLOT_EMPTY_THR, scales=(0.9, 1.0, 1.1)
    )
    return (pt is not None and sc >= C.CONF_SLOT_EMPTY_THR, float(sc))

def _empty_by_variance(crop_bgr):
    g = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    v = float(np.var(g))
    edges = cv2.Canny(g, 40, 120)
    edge_ratio = edges.mean()/255.0
    return (v < 400.0 and edge_ratio < 0.03)

def is_slot_empty():
    """
    ใช้ SLOT_CENTER_X/Y และ SLOT_ROI_W/H ตรวจว่า slot ปัจจุบันว่างหรือไม่
    - ถ้ามีเทมเพลต slot_empty.png: ใช้เทมเพลตก่อน
    - ถ้าไม่มี: ใช้ฮิวริสติก variance
    """
    img = _grab()
    w, h = C.SLOT_ROI_W, C.SLOT_ROI_H
    crop, _ = _roi_centered(img, C.SLOT_CENTER_X, C.SLOT_CENTER_Y, w, h)

    if CV.read_tpl("slot_empty.png") is not None:
        emp, sc = _empty_by_template(crop)
        LOG.tee(f"[ตรวจช่อง] ว่าง={emp} (ด้วยเทมเพลต score={sc:.3f})")
        return emp

    emp = _empty_by_variance(crop)
    LOG.tee(f"[ตรวจช่อง] ว่าง={emp} (ฮิวริสติก)")
    return emp

# ======== วิเคราะห์สีสำหรับ overlay animation ========
def _color_scores_for_overlay(roi_bgr):
    """
    คืน (success_score, fail_score) เป็นสัดส่วนพื้นที่ (0..1) ของสีที่เข้าข่าย
    - success: โทนฟ้า/น้ำเงิน + ขาวสว่าง
    - fail:    โทนส้ม/แดง
    """
    hsv = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2HSV)

    # success: ฟ้า/น้ำเงิน
    s_h1 = getattr(C, "SUCCESS_H_MIN", 90)
    s_h2 = getattr(C, "SUCCESS_H_MAX", 140)
    s_s  = getattr(C, "SUCCESS_S_MIN", 60)
    s_v  = getattr(C, "SUCCESS_V_MIN", 80)
    success_blue = cv2.inRange(hsv, (s_h1, s_s, s_v), (s_h2, 255, 255))

    # success: ขาวสว่าง
    w_s_max = getattr(C, "SUCCESS_WHITE_S_MAX", 40)
    w_v_min = getattr(C, "SUCCESS_WHITE_V_MIN", 200)
    success_white = cv2.inRange(hsv, (0, 0, w_v_min), (180, w_s_max, 255))

    success_mask = cv2.bitwise_or(success_blue, success_white)

    # fail: ส้ม
    f_or_h1 = getattr(C, "FAIL_ORANGE_H_MIN", 10)
    f_or_h2 = getattr(C, "FAIL_ORANGE_H_MAX", 25)
    f_s     = getattr(C, "FAIL_S_MIN", 80)
    f_v     = getattr(C, "FAIL_V_MIN", 80)
    fail_orange = cv2.inRange(hsv, (f_or_h1, f_s, f_v), (f_or_h2, 255, 255))

    # fail: แดง (สองช่วง hue)
    f_r1 = cv2.inRange(hsv, (0,   f_s, f_v), (5,   255, 255))
    f_r2 = cv2.inRange(hsv, (170, f_s, f_v), (180, 255, 255))
    fail_red = cv2.bitwise_or(f_r1, f_r2)

    fail_mask = cv2.bitwise_or(fail_orange, fail_red)

    area = roi_bgr.shape[0] * roi_bgr.shape[1]
    if area <= 0:
        return 0.0, 0.0

    succ_ratio = float(cv2.countNonZero(success_mask)) / float(area)
    fail_ratio = float(cv2.countNonZero(fail_mask)) / float(area)

    return succ_ratio, fail_ratio

# ======== ตรวจผล overlay_abs ========
def detect_overlay_result():
    """
    คืน 'success' / 'fail' / None
    ลำดับตรวจ:
      1) OCR หา 'สำเร็จ' หรือ 'ล้มเหลว'
      2) เทมเพลต success.png / fail.png
      3) วิเคราะห์โทนสี (success: ฟ้า/ขาว, fail: ส้ม/แดง)
    """
    img = _grab()
    roi, _ = _roi_rect(img, {
        "x1": C.OVERLAY_X1, "y1": C.OVERLAY_Y1,
        "w":  C.OVERLAY_W,  "h":  C.OVERLAY_H
    })
    thr = _binarize(roi)

    # (1) OCR ไทย
    txt = _ocr_text(thr, psm=7)
    if "สำเร็จ" in txt:
        LOG.tee("[overlay OCR] พบคำว่า 'สำเร็จ'")
        return "success"
    if "ล้มเหลว" in txt:
        LOG.tee("[overlay OCR] พบคำว่า 'ล้มเหลว'")
        return "fail"

    # (2) เทมเพลต
    pt, sc = CV.match_center_multiscale(
        roi, CV.read_tpl("success.png"),
        thr=C.CONF_SUCCESS_THR, scales=(0.95, 1.00, 1.05)
    )
    if pt:
        LOG.tee("[overlay TM] จับ 'สำเร็จ' ด้วยเทมเพลต")
        return "success"

    pt, sc = CV.match_center_multiscale(
        roi, CV.read_tpl("fail.png"),
        thr=C.CONF_FAIL_THR, scales=(0.95, 1.00, 1.05)
    )
    if pt:
        LOG.tee("[overlay TM] จับ 'ล้มเหลว' ด้วยเทมเพลต")
        return "fail"

    # (3) โทนสี
    succ_ratio, fail_ratio = _color_scores_for_overlay(roi)
    succ_min = float(getattr(C, "SUCCESS_COLOR_MIN", 0.06))
    fail_min = float(getattr(C, "FAIL_COLOR_MIN", 0.06))

    LOG.tee(f"[overlay สี] success≈{succ_ratio:.3f}, fail≈{fail_ratio:.3f} (เกณฑ์ {succ_min:.2f}/{fail_min:.2f})")

    succ_hit = succ_ratio >= succ_min
    fail_hit = fail_ratio >= fail_min

    if succ_hit and not fail_hit:
        LOG.tee("[overlay สี] ตัดสิน: สำเร็จ (ฟ้า/ขาวเด่น)")
        return "success"
    if fail_hit and not succ_hit:
        LOG.tee("[overlay สี] ตัดสิน: ล้มเหลว (ส้ม/แดงเด่น)")
        return "fail"

    LOG.tee("[overlay] ยังไม่ชัดเจน (OCR/เทมเพลต/สีไม่เด่น)")
    return None

# ======== วงรอบอัปเกรด: นับ 'สำเร็จ' ให้ถึงเป้า ========
def upgrade_count_successes(start_level: int | None, target_level: int = None):
    """
    เริ่มหลังจาก 'ใส่ลง' แล้ว
    - start_level: ระดับที่อ่านได้จาก [n] ก่อนใส่ลง (None -> ถือเป็น 0)
    - target_level: เป้าหมาย (ดีฟอลต์ C.TARGET_LEVEL = 5)
    วน: ตรวจช่องยังไม่ว่าง -> กดอัปเกรด -> ตรวจ overlay -> นับ "สำเร็จ"
         ถ้า fail และ (base+successes) >= 4 -> แตก -> ข้าม
    คืน (done:bool, end_level:int, reason:str)
    """
    if target_level is None:
        target_level = getattr(C, "TARGET_LEVEL", 5)

    base = start_level if (start_level is not None and start_level >= 0) else 0
    need = max(0, target_level - base)
    LOG.tee(f"[อัปเกรด] เริ่มที่ระดับ {base} ต้องการ 'สำเร็จ' เพิ่มอีก {need} ครั้ง เพื่อถึง +{target_level}")

    successes = 0
    start_t = time.time()

    while successes < need:
        if time.time() - start_t > C.MAX_ITEM_TIME_SEC:
            LOG.tee("[อัปเกรด] เกินเวลาไอเทมนี้ → ข้าม")
            return (False, base+successes, "หมดเวลา")

        # กันเคสแตกเงียบ ๆ ก่อนคลิก
        if is_slot_empty():
            LOG.tee("[อัปเกรด] ช่องว่าง (คาดว่าไอเทมสูญหาย) → ข้าม")
            return (False, base+successes, "แตก")

        # คลิกอัปเกรด
        ADB.tap(C.UPGRADE_BTN_X, C.UPGRADE_BTN_Y)
        LOG.tee(f"[อัปเกรด] กดอัปเกรด (คืบหน้า: +{base+successes} → +{base+successes+1})")
        time.sleep(C.POST_UPGRADE_WAIT_SEC)

        # อ่านผล overlay
        res = detect_overlay_result()
        if res == "success":
            successes += 1
            LOG.tee(f"[อัปเกรด] ผล: สำเร็จ (+1) → ตอนนี้เป็น +{base+successes}")
            continue
        elif res == "fail":
            cur = base + successes
            if cur >= 4:
                LOG.tee("[อัปเกรด] ผล: ล้มเหลวที่ระดับ ≥4 → ไอเทมแตก → ข้าม")
                return (False, cur, "แตก")
            else:
                LOG.tee("[อัปเกรด] ผล: ล้มเหลวที่ระดับ <4 → เกมไม่แตก → ลองอัปต่อ")
                continue
        else:
            # ยังไม่ชัดเจน → รอเพิ่มแล้วลองตรวจซ้ำ (เผื่ออนิเมชัน)
            t0 = time.time()
            got = None
            while time.time() - t0 < 1.5:
                time.sleep(0.3)
                chk = detect_overlay_result()
                if chk in ("success", "fail"):
                    got = chk
                    break
            if got == "success":
                successes += 1
                LOG.tee(f"[อัปเกรด] (ดีเลย์) ผล: สำเร็จ → ขึ้นเป็น +{base+successes}")
            elif got == "fail":
                cur = base + successes
                if cur >= 4:
                    LOG.tee("[อัปเกรด] (ดีเลย์) ผล: ล้มเหลวที่ระดับ ≥4 → แตก → ข้าม")
                    return (False, cur, "แตก")
                else:
                    LOG.tee("[อัปเกรด] (ดีเลย์) ผล: ล้มเหลวระดับ <4 → ลองต่อ")
            else:
                LOG.tee("[อัปเกรด] (ดีเลย์) ยังไม่ชัดเจน → ลองวนต่อ")

        # กันค้างจำนวนคลิก
        if successes + base >= target_level:
            break
        if successes + base < target_level and getattr(C, "MAX_UPGRADE_CLICKS_PER_ITEM", 60) <= (base + successes):
            LOG.tee("[อัปเกรด] จำนวนความพยายามเกินขีดจำกัด → ข้าม")
            return (False, base+successes, "เกินจำนวนคลิก")

    LOG.tee(f"[อัปเกรด] บรรลุเป้าหมาย +{target_level}")
    return (True, base+successes, "ครบเป้า")
