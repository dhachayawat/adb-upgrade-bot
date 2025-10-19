# app/upgrader.py
# อัปเกรดแบบ “กันพลาด +6” + ฝัง thresholds จากไฟล์วิเคราะห์สี + โพลล์ overlay เร็วภายใน 1.5s
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

        TH_BLUE_MIN     = max(0.01, min(0.20, b80))         # clamp
        TH_SUCC_SUM_MIN = max(0.03, min(0.35, b80 + w80))   # b+w
        TH_RED_MIN      = max(0.03, min(0.35, r80))

        LOG.tee(f"[thresholds] ใช้ค่าจากไฟล์: "
                f"BLUE_MIN={TH_BLUE_MIN:.3f}, SUCC_SUM_MIN={TH_SUCC_SUM_MIN:.3f}, RED_MIN={TH_RED_MIN:.3f}")
    except Exception as e:
        LOG.tee(f"[thresholds] อ่านไฟล์แนะนำไม่สำเร็จ ({e}) → ใช้ค่า default")

# โหลดหนึ่งครั้งตอน import
_load_color_thresholds()

# ================= OCR ช่วยอ่าน [n] =================
def _prep_variants(gray: np.ndarray):
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
    try:
        import pytesseract
        cfg = "--psm 7 -c tessedit_char_whitelist=0123456789[]+"
        txt = pytesseract.image_to_string(img, config=cfg) or ""
        return txt.strip()
    except Exception:
        return ""

def _extract_bracket_number(s: str) -> Optional[int]:
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
    sx, sy = C.SLOT_STATUS_X, C.SLOT_STATUS_Y
    w, h   = C.SLOT_STATUS_ROI_W, C.SLOT_STATUS_ROI_H
    x1 = max(0, sx - w//2)
    y1 = max(0, sy - h//2)
    x2 = min(frame.shape[1], x1 + w)
    y2 = min(frame.shape[0], y1 + h)
    return frame[y1:y2, x1:x2].copy()

def _vote_level_from_roi(roi_bgr: np.ndarray) -> Tuple[Optional[int], str]:
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
    levels, debugs = [], []
    for i in range(max(1, retry)):
        img = CV.screencap_bgr()
        roi = _roi_slot_status_bgr(img)
        lv, dbg = _vote_level_from_roi(roi)
        levels.append(lv); debugs.append(dbg)
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
    success: (blue+white) > TH_SUCC_SUM_MIN และ blue > TH_BLUE_MIN
    fail:    red > TH_RED_MIN
    """
    if roi_bgr.size == 0:
        return None
    b, w, r = _color_ratios(roi_bgr)
    if (b + w) > TH_SUCC_SUM_MIN and b > TH_BLUE_MIN:
        return "success"
    if r > TH_RED_MIN:
        return "fail"
    return None

def _detect_success_fail_by_tpl(roi_bgr: np.ndarray) -> Optional[str]:
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

def _read_overlay_once() -> Optional[str]:
    """
    อ่าน overlay หนึ่งครั้ง (เร็วที่สุด):
    - พยายามจับด้วย template ก่อน
    - ไม่เจอใช้สีตาม thresholds
    """
    img = CV.screencap_bgr()
    roi = _roi_overlay(img)
    by_tpl = _detect_success_fail_by_tpl(roi)
    if by_tpl:
        return by_tpl
    return _detect_success_fail_by_color(roi)

# ---- โพลล์ overlay ภายในกรอบเวลา (เริ่มอ่านเร็วหลังคลิก) ----
OVERLAY_POLL_WINDOW_SEC   = float(os.getenv("OVERLAY_POLL_WINDOW_SEC", "1.5"))
OVERLAY_POLL_MIN_ATTEMPTS = int(os.getenv("OVERLAY_POLL_MIN_ATTEMPTS", "3"))
OVERLAY_POLL_MAX_ATTEMPTS = int(os.getenv("OVERLAY_POLL_MAX_ATTEMPTS", "8"))
OVERLAY_POLL_INTERVAL_MS  = int(os.getenv("OVERLAY_POLL_INTERVAL_MS", "160"))  # 0.16s ต่อช็อต

def wait_overlay_result() -> Optional[str]:
    """
    โพลล์ overlay หลังคลิกอัปเกรด:
    - พยายามอย่างน้อย 3 ครั้งภายใน 1.5s (ปรับได้ผ่าน env)
    - ต้องได้ผลซ้ำกัน >= 2 ครั้ง (debounce) หรือพบติดกันสองเฟรม
    - คืน "success"/"fail"/None(ไม่แน่ใจ)
    """
    t0 = time.time()
    attempts = 0
    seen = {"success":0, "fail":0}
    last = None; consec = 0

    while True:
        attempts += 1
        rs = _read_overlay_once()

        if rs:
            seen[rs] += 1
            # consecutive confirm
            if rs == last:
                consec += 1
            else:
                consec = 1
                last = rs

            # เงื่อนไขยืนยันผล:
            if seen[rs] >= 2 or consec >= 2:
                LOG.tee(f"[overlay] ยืนยันผล '{rs}' (attempts={attempts}, seen={seen}, consec={consec})")
                return rs

        # ออกจากลูปเมื่อครบหน้าต่างเวลา และยิงอย่างน้อย MIN_ATTEMPTS
        if (time.time() - t0) >= OVERLAY_POLL_WINDOW_SEC and attempts >= OVERLAY_POLL_MIN_ATTEMPTS:
            break
        if attempts >= OVERLAY_POLL_MAX_ATTEMPTS:
            break

        time.sleep(max(0.05, OVERLAY_POLL_INTERVAL_MS / 1000.0))

    LOG.tee(f"[overlay] ไม่แน่ใจ (attempts={attempts}, seen={seen})")
    return None

# =============== ลูปอัปเกรดแบบนับ “สำเร็จ” ถึงเป้า ===============
def _click_upgrade():
    ADB.tap(C.UPGRADE_BTN_X, C.UPGRADE_BTN_Y)

def upgrade_count_successes(start_level: Optional[int], target_level: int = 5) -> Tuple[bool, Optional[int], str]:
    """
    อัปเกรดโดยนับจำนวน “สำเร็จ” ให้ถึง target_level (เช่น 5)
    ขั้นตอนต่อคลิก:
      1) กันทะลุ: re-OCR ก่อนคลิก ถ้า >= target → จบ
      2) คลิกอัปเกรด
      3) โพลล์ overlay ภายในหน้าต่างเวลา (1.5s) แบบเร็ว
      4) re-OCR ระดับ [n] จาก slot_status เพื่อรับรองผลจริง
      5) ตัดสินใจตามกติกา (ถ้า fail และระดับก่อนหน้ามากกว่า/เท่ากับ +4 ให้ถือว่าแตก)
    """
    t0 = time.time()
    max_clicks = max(1, C.MAX_UPGRADE_CLICKS_PER_ITEM)
    end_level = start_level
    unknown_streak = 0

    # guard: ถ้าตั้งต้น >= target แล้ว ให้จบเลย
    now = read_bracket_level_from_status(retry=2)
    if now is not None and now >= target_level:
        return True, now, "ถึงเป้าตั้งแต่ต้น"

    for click_idx in range(1, max_clicks + 1):
        # กันทะลุ: ตรวจซ้ำก่อนคลิก
        now = read_bracket_level_from_status(retry=2)
        if now is not None and now >= target_level:
            return True, now, "ถึงเป้าก่อนคลิกถัดไป"

        LOG.tee(f"[อัปเกรด] คลิกครั้งที่ {click_idx}")
        _click_upgrade()
        # หน่วงเล็กน้อยให้กราฟิกเริ่ม (สั้นลงเพื่อ “ทัน” แอนิเมชัน)
        time.sleep(0.08)

        # โพลล์ overlay อย่างรวดเร็วใน 1.5s
        rs = wait_overlay_result()
        LOG.tee(f"[อัปเดตผล] overlay = {rs}")

        # อ่านระดับจริงหลังเอฟเฟกต์จบลงเล็กน้อย
        lvl = read_bracket_level_from_status(retry=3)
        if lvl is not None:
            end_level = lvl

        if rs == "success":
            unknown_streak = 0
            if end_level is not None and end_level >= target_level:
                return True, end_level, "สำเร็จถึงเป้า"
        elif rs == "fail":
            unknown_streak = 0
            # กติกา: ตั้งแต่ +4 ขึ้นไป ถ้า fail ให้ถือว่าแตก/หาย
            if (end_level is not None and end_level >= 4) or (now is not None and now >= 4):
                return False, end_level, "แตก/หาย"
            # ถ้าต่ำกว่า +4 บางเกมไม่แตก ก็วนต่อ (ให้ cooldown สั้น ๆ)
        else:
            # ไม่แน่ใจ: พยายามอ่านระดับอีกรอบ
            unknown_streak += 1
            LOG.tee(f"[อัปเดตผล] ไม่แน่ใจ (unknown_streak={unknown_streak}) → ตรวจระดับซ้ำ")
            time.sleep(0.12)
            lvl2 = read_bracket_level_from_status(retry=2)
            if lvl2 is not None:
                end_level = lvl2
                if end_level >= target_level:
                    return True, end_level, "ไม่แน่ใจแต่ตรวจซ้ำ พบถึงเป้า"

        # เงื่อนไขออกจากลูปต่อชิ้น
        if (time.time() - t0) > C.MAX_ITEM_TIME_SEC:
            return False, end_level, "หมดเวลาต่อชิ้น"
        if click_idx >= max_clicks:
            return False, end_level, "ครบจำนวนคลิกสูงสุด"

        # cooldown สั้น ๆ ก่อนคลิกถัดไป
        ADB.sleep_rand(max(0.20, C.UPGRADE_COOLDOWN_MIN), max(0.50, C.UPGRADE_COOLDOWN_MAX))

    return False, end_level, "จบลูปโดยเงื่อนไขสำรอง"

# ================= API ระดับสูง (compat เดิม) =================
def is_plus5(geo=None) -> bool:
    lvl = read_bracket_level_from_status(retry=3)
    return bool(lvl is not None and lvl >= 5)

def upgrade_until_plus5_or_break():
    start = read_bracket_level_from_status(retry=3)
    done, end_lvl, reason = upgrade_count_successes(start, 5)
    return done
