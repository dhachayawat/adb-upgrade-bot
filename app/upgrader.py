# app/upgrader.py
# อัปเกรดแบบ “กันพลาด +6” + ฝัง thresholds จากไฟล์วิเคราะห์สี
import os
import time
from typing import Optional, Tuple

import cv2
import numpy as np

from app import config as C
from app import adb as ADB
from app import cv_utils as CV
from app import rtlog as LOG

# ===================== โหลด Thresholds จากไฟล์วิเคราะห์ (ถ้ามี) =====================
SUGGEST_PATH = os.getenv(
    "OVERLAY_SUGGEST_FILE",
    "/data/analysis_out/overlay_threshold_suggestion.json"
)

# ค่ามาตรฐาน (fallback) เดิม
DEFAULT_BLUE_MIN      = 0.03   # ต้องมีฟ้าขั้นต่ำ
DEFAULT_SUCC_SUM_MIN  = 0.08   # (blue+white) ขั้นต่ำ
DEFAULT_RED_MIN       = 0.06   # fail ขั้นต่ำ

# จะถูก override ด้วยค่าจากไฟล์ (ถ้ามี)
TH_BLUE_MIN     = DEFAULT_BLUE_MIN
TH_SUCC_SUM_MIN = DEFAULT_SUCC_SUM_MIN
TH_RED_MIN      = DEFAULT_RED_MIN

def _load_color_thresholds():
    """
    อ่านไฟล์ JSON ที่วิเคราะห์จากคลิปแล้วตั้งค่า threshold อัตโนมัติ
    เลือก p80 เป็นฐาน (บาลานซ์ระหว่างความไวกับความแม่น) ถ้าไม่มีใช้ default
    """
    global TH_BLUE_MIN, TH_SUCC_SUM_MIN, TH_RED_MIN
    try:
        import json
        if not os.path.isfile(SUGGEST_PATH):
            LOG.tee(f"[thresholds] ไม่มีไฟล์แนะนำ: {SUGGEST_PATH} → ใช้ค่า default")
            return
        with open(SUGGEST_PATH, "r", encoding="utf-8") as f:
            j = json.load(f)

        b80 = float(j.get("blue_quantiles", {}).get("p80", DEFAULT_BLUE_MIN))
        w80 = float(j.get("white_quantiles", {}).get("p80", 0.05))
        r80 = float(j.get("red_quantiles", {}).get("p80", DEFAULT_RED_MIN))

        # กติกาง่าย ๆ:
        # success: (blue+white) > succ_sum  และ blue > blue_min
        # fail:    red > red_min
        TH_BLUE_MIN     = max(0.01, min(0.20, b80))         # clamp กันหลุด
        TH_SUCC_SUM_MIN = max(0.03, min(0.35, b80 + w80))   # รวม b+w
        TH_RED_MIN      = max(0.03, min(0.35, r80))

        LOG.tee(f"[thresholds] ใช้ค่าจากไฟล์: "
                f"BLUE_MIN={TH_BLUE_MIN:.3f}, SUCC_SUM_MIN={TH_SUCC_SUM_MIN:.3f}, RED_MIN={TH_RED_MIN:.3f}")
    except Exception as e:
        LOG.tee(f"[thresholds] อ่านไฟล์แนะนำไม่สำเร็จ ({e}) → ใช้ค่า default")

# โหลดหนึ่งครั้งตอน import
_load_color_thresholds()

# ================= OCR ช่วยอ่าน [n] =================
def _prep_variants(gray: np.ndarray):
    """สร้างหลายเวอร์ชันภาพเพื่อ OCR แล้ว vote กัน"""
    out = []
    out.append(gray)
    out.append(cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 21, 5
    ))
    _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY+cv2.THRESH_OTSU)
    out.append(th)
    _, thi = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)
    out.append(thi)
    edges = cv2.Canny(gray, 60, 140)
    out.append(edges)
    return out

def _ocr_digits_only(img: np.ndarray) -> str:
    """OCR ตัวเลข/สัญลักษณ์พื้นฐาน ถ้ามี pytesseract ก็ใช้; ไม่มีให้คืนว่างไป"""
    try:
        import pytesseract
        cfg = "--psm 7 -c tessedit_char_whitelist=0123456789[]+"
        txt = pytesseract.image_to_string(img, config=cfg) or ""
        return txt.strip()
    except Exception:
        return ""

def _extract_bracket_number(s: str) -> Optional[int]:
    """ดึงตัวเลขรูปแบบ [n] หรือ +n (กัน OCR เพี้ยนเล็กน้อย)"""
    if not s:
        return None
    ss = s.replace(" ", "").replace("S", "5").replace("s", "5")
    import re
    m = re.search(r"\[(\d{1,2})\]", ss)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            pass
    m2 = re.search(r"\+(\d{1,2})", ss)
    if m2:
        try:
            return int(m2.group(1))
        except Exception:
            pass
    return None

def _roi_slot_status_bgr(frame: np.ndarray) -> np.ndarray:
    """คืน BGR ROI ของ 'slot_status' ตาม config"""
    sx, sy = C.SLOT_STATUS_X, C.SLOT_STATUS_Y
    w, h   = C.SLOT_STATUS_ROI_W, C.SLOT_STATUS_ROI_H
    x1 = max(0, sx - w//2)
    y1 = max(0, sy - h//2)
    x2 = min(frame.shape[1], x1 + w)
    y2 = min(frame.shape[0], y1 + h)
    return frame[y1:y2, x1:x2].copy()

def _vote_level_from_roi(roi_bgr: np.ndarray) -> Tuple[Optional[int], str]:
    """OCR หลายเวอร์ชัน แล้ว vote ระดับ [n]"""
    if roi_bgr.size == 0:
        return None, "empty_roi"
    roi = cv2.resize(roi_bgr, None, fx=1.8, fy=1.8, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    variants = _prep_variants(gray)

    votes, texts = {}, []
    for v in variants:
        txt = _ocr_digits_only(v)
        texts.append(txt)
        n = _extract_bracket_number(txt)
        if n is not None:
            votes[n] = votes.get(n, 0) + 1

    if votes:
        best = sorted(votes.items(), key=lambda kv: (-kv[1], -kv[0]))[0][0]
        return best, f"vote={votes} texts={texts!r}"

    import re
    joined = "".join(texts)
    m = re.search(r"(\d{1,2})", joined)
    if m:
        try:
            return int(m.group(1)), f"heuristic={m.group(1)} texts={texts!r}"
        except Exception:
            pass
    return None, f"no_digits texts={texts!r}"

def read_bracket_level_from_status(retry: int = 2, wait: float = 0.10) -> Optional[int]:
    """จับภาพหลายครั้ง + vote เพื่ออ่านระดับ [n] ที่ slot_status"""
    levels, debugs = [], []
    for i in range(max(1, retry)):
        img = CV.screencap_bgr()
        roi = _roi_slot_status_bgr(img)
        lv, dbg = _vote_level_from_roi(roi)
        levels.append(lv)
        debugs.append(dbg)
        if i < retry - 1:
            time.sleep(wait)
    cnt = {}
    for lv in levels:
        if lv is None: continue
        cnt[lv] = cnt.get(lv, 0) + 1
    if cnt:
        best = sorted(cnt.items(), key=lambda kv: (-kv[1], -kv[0]))[0][0]
        LOG.tee(f"[OCR slot_status] levels={levels} -> {best} ({debugs[-1] if debugs else ''})")
        return best
    LOG.tee(f"[OCR slot_status] ไม่พบเลข (levels={levels})")
    return None

# =============== ตรวจผลสำเร็จ/ล้มเหลว จาก overlay ===============
def _roi_overlay(frame: np.ndarray) -> np.ndarray:
    x1, y1, w, h = C.OVERLAY_X1, C.OVERLAY_Y1, C.OVERLAY_W, C.OVERLAY_H
    x2 = min(frame.shape[1], x1 + w)
    y2 = min(frame.shape[0], y1 + h)
    return frame[y1:y2, x1:x2].copy()

def _color_ratios(roi_bgr: np.ndarray):
    hsv = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2HSV)
    mask_blue  = cv2.inRange(hsv, (90, 40, 80), (130,255,255))
    mask_white = cv2.inRange(hsv, (0,  0,200), (179, 40,255))
    mask_r1    = cv2.inRange(hsv, (0, 80, 80), (10, 255,255))
    mask_r2    = cv2.inRange(hsv, (160,80,80), (179,255,255))
    blue_ratio  = float((mask_blue  > 0).mean())
    white_ratio = float((mask_white > 0).mean())
    red_ratio   = float(((mask_r1>0).mean() + (mask_r2>0).mean()) / 2.0)
    return blue_ratio, white_ratio, red_ratio

def _detect_success_fail_by_color(roi_bgr: np.ndarray) -> Optional[str]:
    """
    ใช้ thresholds ที่โหลดมาจากไฟล์ (หรือ default)
    success: (blue+white) > TH_SUCC_SUM_MIN และ blue > TH_BLUE_MIN
    fail:    red > TH_RED_MIN
    """
    if roi_bgr.size == 0:
        return None
    b, w, r = _color_ratios(roi_bgr)
    # LOG.tee(f"[overlay color] b={b:.3f} w={w:.3f} r={r:.3f}")  # เปิดถ้าอยากดูละเอียด
    if (b + w) > TH_SUCC_SUM_MIN and b > TH_BLUE_MIN:
        return "success"
    if r > TH_RED_MIN:
        return "fail"
    return None

def _detect_success_fail_by_tpl(roi_bgr: np.ndarray) -> Optional[str]:
    """ถ้ามี template success.png / fail.png ให้ลองจับด้วย template"""
    try:
        tpl_succ = CV.read_tpl("success.png")
        tpl_fail = CV.read_tpl("fail.png")
        got = []
        if tpl_succ is not None:
            pt, sc = CV.match_center_multiscale(roi_bgr, tpl_succ, thr=C.CONF_SUCCESS_THR, save_debug_tag=None)
            if pt is not None:
                got.append(("success", sc))
        if tpl_fail is not None:
            pt, sc = CV.match_center_multiscale(roi_bgr, tpl_fail, thr=C.CONF_FAIL_THR, save_debug_tag=None)
            if pt is not None:
                got.append(("fail", sc))
        if not got:
            return None
        got.sort(key=lambda x: -x[1])
        return got[0][0]
    except Exception:
        return None

def detect_overlay_success_fail(confirm_twice: bool = True) -> Optional[str]:
    """
    อ่านผลสำเร็จ/ล้มเหลวจาก overlay:
    - ลอง template ก่อน (ถ้ามี) → ไม่ได้ค่อยใช้สี (thresholds ที่โหลด)
    - ถ้า confirm_twice=True จะจับ 2 เฟรม ห่าง ~120ms ต้องตรงกัน
    """
    def snap_once():
        img = CV.screencap_bgr()
        roi = _roi_overlay(img)
        by_tpl = _detect_success_fail_by_tpl(roi)
        if by_tpl:
            return by_tpl
        by_color = _detect_success_fail_by_color(roi)
        return by_color

    r1 = snap_once()
    if not confirm_twice:
        return r1
    time.sleep(0.12)
    r2 = snap_once()
    if r1 and r1 == r2:
        return r1
    return None  # ไม่แน่ใจ

# =============== ลูปอัปเกรดแบบนับ “สำเร็จ” ถึงเป้า ===============
def _click_upgrade():
    ADB.tap(C.UPGRADE_BTN_X, C.UPGRADE_BTN_Y)

def upgrade_count_successes(start_level: Optional[int], target_level: int = 5) -> Tuple[bool, Optional[int], str]:
    """
    อัปเกรดโดยนับจำนวน “สำเร็จ” ให้ถึง target_level (เช่น 5)
    - ก่อนคลิกทุกครั้งจะ re-OCR slot_status: ถ้า >= target → หยุด (กันทะลุ)
    - หลังคลิกจะรอให้แอนิเมชันทำงาน แล้วตรวจ overlay แบบ double-check
      จากนั้น re-OCR slot_status อีกครั้งเพื่อรับรองสถานะจริง
    - unknown หลายเฟรม → รอเพิ่ม/ตรวจซ้ำ แบบระมัดระวัง
    """
    t0 = time.time()
    max_clicks = max(1, C.MAX_UPGRADE_CLICKS_PER_ITEM)
    end_level = start_level
    unknown_streak = 0

    now = read_bracket_level_from_status(retry=2)
    if now is not None and now >= target_level:
        return True, now, "ถึงเป้าตั้งแต่ต้น"

    for click_idx in range(1, max_clicks + 1):
        # กันทะลุ: เช็คก่อนคลิก
        now = read_bracket_level_from_status(retry=2)
        if now is not None and now >= target_level:
            return True, now, "ถึงเป้าก่อนคลิกถัดไป"

        LOG.tee(f"[อัปเกรด] คลิกครั้งที่ {click_idx}")
        _click_upgrade()
        ADB.sleep_rand(C.CLICK_DELAY_MIN, C.CLICK_DELAY_MAX)

        time.sleep(max(0.2, C.POST_UPGRADE_WAIT_SEC))

        rs = detect_overlay_success_fail(confirm_twice=True)
        LOG.tee(f"[อัปเดตผล] overlay = {rs}")

        lvl = read_bracket_level_from_status(retry=3)
        if lvl is not None:
            end_level = lvl

        if rs == "success":
            unknown_streak = 0
            if end_level is not None and end_level >= target_level:
                return True, end_level, "สำเร็จถึงเป้า"
        elif rs == "fail":
            unknown_streak = 0
            if end_level is not None and end_level >= 4:
                return False, end_level, "แตก/หาย"
        else:
            unknown_streak += 1
            LOG.tee(f"[อัปเดตผล] ไม่แน่ใจ (unknown_streak={unknown_streak}) → จับภาพซ้ำแบบระวัง")
            if unknown_streak >= 2:
                time.sleep(0.25)
                lvl2 = read_bracket_level_from_status(retry=3)
                if lvl2 is not None and lvl2 >= target_level:
                    return True, lvl2, "ไม่แน่ใจแต่ตรวจซ้ำ พบถึงเป้า"

        if (time.time() - t0) > C.MAX_ITEM_TIME_SEC:
            return False, end_level, "หมดเวลาต่อชิ้น"
        if click_idx >= max_clicks:
            return False, end_level, "ครบจำนวนคลิกสูงสุด"

        ADB.sleep_rand(C.UPGRADE_COOLDOWN_MIN, C.UPGRADE_COOLDOWN_MAX)

    return False, end_level, "จบลูปโดยเงื่อนไขสำรอง"

# ================= API ระดับสูง (compat เดิม) =================
def is_plus5(geo=None) -> bool:
    lvl = read_bracket_level_from_status(retry=3)
    return bool(lvl is not None and lvl >= 5)

def upgrade_until_plus5_or_break():
    start = read_bracket_level_from_status(retry=3)
    done, end_lvl, reason = upgrade_count_successes(start, 5)
    return done
