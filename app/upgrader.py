# app/upgrader.py
"""
อัปเกรดไอเทมให้ถึง +5 ตามลอจิก:
- หลัง "ใส่ลง" แล้ว: ตรวจตรา +N ที่มุมขวาบนของไอคอนไอเทมในช่องอัปเกรด
  - ถ้าเป็น +5 -> ข้ามไอเทมนี้
  - ถ้าไม่ใช่ -> เข้าลูปอัปเกรด (กดปุ่ม → หน่วง → ตรวจแตก/ตรวจ +5 → วน)
- ใช้ OCR เป็นหลัก (อ่าน +N) และ fallback เป็นเทียบรูป badge (+5)
"""

from __future__ import annotations
from typing import Dict, Tuple
import time

from app import config as C
from app import cv_utils as CV
from app import ocr
from app.geometry import Geo, ensure_geo
from app.adb import tap

# -----------------------------
# เทมเพลต/พารามิเตอร์ที่ใช้ตรวจ
# -----------------------------

# ตราที่มุม (ใช้เป็น fallback ถ้า OCR มั่นใจไม่พอ)
BADGE_TPLS = ["badge_plus5.png", "badge_plus5_alt.png"]  # วางใน templates/
BADGE_SCALES = (0.80, 0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.15, 1.20)

# เทมเพลตช่องว่าง (แตก/ไม่มีของ)
SLOT_EMPTY_TPL = "slot_empty.png"


# -----------------------------
# Utilities
# -----------------------------

def _match_any(roi_bgr, names, thr, scales) -> Tuple[bool, float, str]:
    """
    ลองจับคู่หลายเทมเพลตใน ROI ด้วย multi-scale
    คืน (found?, best_score, best_name)
    """
    best = (False, 0.0, "")
    for nm in names:
        tpl = CV.read_tpl(nm)
        if tpl is None:
            continue
        pt, score = CV.match_center_multiscale(roi_bgr, tpl, thr=thr, scales=scales)
        if score is None:
            score = 0.0
        if score > best[1]:
            best = (pt is not None, score, nm)
    return best


def slot_has_item(geo: Geo) -> bool:
    """
    True  = ยังมีไอเทมในช่องอัปเกรด
    False = ว่าง/แตก (พบภาพ slot_empty)
    """
    img = CV.screencap_bgr(save_tag="slot_chk")
    x, y, w, h = geo.slot_roi
    crop = img[y:y + h, x:x + w]
    tpl = CV.read_tpl(SLOT_EMPTY_TPL)
    # เจอ slot_empty => ว่าง (ไม่มีของ)
    pt, score = CV.match_center_multiscale(crop, tpl, thr=C.CONF_SLOT_EMPTY_THR, scales=(1.0,))
    has_item = (pt is None)
    return has_item


def is_plus5_by_badge(geo: Geo) -> bool:
    """
    ตรวจ +5 โดยอ่าน 'ตรา +N' ที่มุมขวาบนของไอคอนไอเทมในช่องอัปเกรด
    ขั้นตอน:
      1) ครอป ROI ย่อยจาก slot_roi เฉพาะมุมขวาบน (กว้าง=BADGE_ROI_W, สูง=BADGE_ROI_H)
      2) OCR หา +N
      3) ถ้า OCR มั่นใจ (conf >= OCR_BADGE_MIN_CONF) และ N == 5 -> True
      4) ถ้าไม่มั่นใจ -> fallback เทียบรูป badge +5 ด้วย multi-scale
    """
    img = CV.screencap_bgr(save_tag="badge5_chk")
    x, y, w, h = geo.slot_roi

    # ROI ย่อยที่มุมขวาบนของช่อง
    rw = max(8, int(getattr(C, "BADGE_ROI_W", 34)))
    rh = max(8, int(getattr(C, "BADGE_ROI_H", 26)))
    x1 = x + max(0, w - rw)
    y1 = y
    x2 = min(x + w, x1 + rw)
    y2 = min(y + h, y1 + rh)
    crop = img[y1:y2, x1:x2]

    # OCR ก่อน
    n, conf = ocr.ocr_plus_n(crop)
    if n is not None:
        print(f"[UPG][OCR] read '+{n}' conf={conf:.1f}")
        if conf >= float(getattr(C, "OCR_BADGE_MIN_CONF", 60.0)) and n == 5:
            return True
        # ถ้าอ่านได้แต่มั่นใจไม่พอ -> ลอง fallback ต่อ

    # Fallback: เทียบรูป badge +5
    found, score, tpl = _match_any(
        crop,
        BADGE_TPLS,
        float(getattr(C, "CONF_BADGE5_THR", 0.68)),
        BADGE_SCALES,
    )
    if found:
        print(f"[UPG][TPL] +5 badge detected (score={score:.3f}, tpl={tpl})")
    else:
        print(f"[UPG][TPL] +5 badge not found (best={score:.3f})")
    return found


# -----------------------------
# Main upgrading loop
# -----------------------------

def upgrade_until_plus5_or_break(geo: Geo, stop_event=None) -> Dict:
    """
    หลัง "ใส่ลง" แล้ว และตรวจแล้วว่ายังไม่ใช่ +5:
      - กดปุ่มอัปเกรด -> หน่วง POST_UPGRADE_WAIT_SEC (ดีฟอลต์ 1.0s)
      - ตรวจแตก: ถ้าช่องว่าง -> จบ (broken=True)
      - ตรวจ +5: ถ้าใช่ -> จบ (plus5=True)
      - ไม่ใช่ -> วนกดต่อ
    มีเพดานจำนวนคลิกและเวลาป้องกันลูปไม่จบ
    """
    wait_sec = max(0.2, float(getattr(C, "POST_UPGRADE_WAIT_SEC", 1.0)))

    # sync geometry จากภาพล่าสุด
    ensure_geo(geo, CV.screencap_bgr(save_tag="pre_upg_geo"))

    clicks = 0
    start_t = time.time()
    max_clicks = max(1, int(getattr(C, "MAX_UPGRADE_CLICKS_PER_ITEM", 40)))
    max_secs = max(5, int(getattr(C, "MAX_ITEM_TIME_SEC", 30)))

    while True:
        if stop_event and stop_event.is_set():
            break
        if clicks >= max_clicks or (time.time() - start_t) >= max_secs:
            print(f"[UPG] stop by limit: clicks={clicks}, secs={int(time.time()-start_t)}")
            break

        # กดอัปเกรด
        tap(C.UPGRADE_BTN_X, C.UPGRADE_BTN_Y)
        clicks += 1

        # หน่วงให้แอนิเมชันโชว์
        time.sleep(wait_sec)

        # อัปเดต geometry จากภาพจริง
        ensure_geo(geo, CV.screencap_bgr(save_tag="post_upg_geo"))

        # แตกหรือไม่ (ว่าง = แตก)
        if not slot_has_item(geo):
            print("[UPG] 💥 แตก/หาย → หยุดไอเทมนี้")
            return {"plus5": False, "broken": True, "clicks": clicks}

        # ยังมีของ → เป็น +5 แล้วหรือยัง
        if is_plus5_by_badge(geo):
            print("[UPG] ✅ ถึง +5 — จบไอเทมนี้")
            return {"plus5": True, "broken": False, "clicks": clicks}

        # ยังไม่ถึง +5 → วนต่อ
        continue

    # ออกโดย limit/stop event
    return {"plus5": False, "broken": False, "clicks": clicks}
