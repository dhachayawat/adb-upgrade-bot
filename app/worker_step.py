# app/worker_step.py
from __future__ import annotations
from typing import Dict, Any, Optional, Tuple
import os
import re, time
import cv2
import numpy as np

from .core.adb_adapter import ADBAdapter
from .config_store import ConfigStore
from .summary_store import SummaryStore
from . import rtlog as LOG
from . import cv_utils as CV

# ===================== Global config (normalized via ConfigStore) =====================
_CFG = ConfigStore().load_defaults()
_SUMMARY: Optional[SummaryStore] = None
_DEVICE_CTX: Dict[str, dict] = {}  # per-device state
_BRACKET_NUM_RE = re.compile(r"[［\[\(（]\s*(\d{1,2})\s*[］\]\)）]")
_PLUS_RE = re.compile(r"\+?\s*(\d{1,2})")

# -------------------- Quick getters --------------------
def _summary() -> SummaryStore:
    global _SUMMARY
    if _SUMMARY is None:
        _SUMMARY = SummaryStore()
    return _SUMMARY

def _pt(key: str, default=(0, 0)) -> Tuple[int, int]:
    v = _CFG.get(key)
    if isinstance(v, (list, tuple)) and len(v) == 2:
        return int(v[0]), int(v[1])
    return int(default[0]), int(default[1])

def _rect(key: str, default=(0, 0, 0, 0)) -> Tuple[int, int, int, int]:
    r = _CFG.get(key)
    if isinstance(r, dict):
        return int(r.get("x1", 0)), int(r.get("y1", 0)), int(r.get("w", 0)), int(r.get("h", 0))
    if isinstance(r, (list, tuple)) and len(r) == 4:
        return int(r[0]), int(r[1]), int(r[2]), int(r[3])
    return int(default[0]), int(default[1]), int(default[2]), int(default[3])

def _size2(key: str, default=(0, 0)) -> Tuple[int, int]:
    r = _CFG.get(key)
    if isinstance(r, (list, tuple)) and len(r) == 2:
        return int(r[0]), int(r[1])
    return int(default[0]), int(default[1])

def _items():
    its = _CFG.get("items") or []
    out = []
    for p in its:
        if isinstance(p, (list, tuple)) and len(p) == 2:
            out.append((int(p[0]), int(p[1])))
    return out

def _swipe():
    s = _CFG.get("swipe") or {}
    return dict(x=int(s.get("x", 0)), y=int(s.get("y", 0)), dy=int(s.get("dy", 0)), ms=int(s.get("ms", 300)))

# thresholds / timings (fallback to env vars if provided)
_CONF_INSERT_THR = float(_CFG.get("conf_insert_thr", os.getenv("CONF_INSERT_THR", "0.86")))
_POST_UPGRADE_WAIT = float(os.getenv("POST_UPGRADE_WAIT_SEC", "1.0"))
_MAX_ITEM_TIME_SEC = int(os.getenv("MAX_ITEM_TIME_SEC", "30"))
_BREAK_LEVEL_MIN   = int(_CFG.get("break_level_min", 4))

# ===================== OCR / Image helpers (ported & adapted) =====================
try:
    import pytesseract
    _HAVE_TESS = True
except Exception:
    _HAVE_TESS = False

def _grab(adb: ADBAdapter) -> np.ndarray:
    """screencap via ADBAdapter → BGR ndarray"""
    return adb.screencap()

def _crop_rect(img: np.ndarray, rect: Tuple[int, int, int, int]) -> Tuple[np.ndarray, Tuple[int, int]]:
    x1, y1, w, h = rect
    x2, y2 = x1 + max(0, w), y1 + max(0, h)
    x1 = max(0, x1); y1 = max(0, y1)
    x2 = min(img.shape[1], x2); y2 = min(img.shape[0], y2)
    return img[y1:y2, x1:x2].copy(), (x1, y1)

def _crop_center(img: np.ndarray, cx: int, cy: int, w: int, h: int) -> Tuple[np.ndarray, Tuple[int, int]]:
    x1 = max(0, cx - w // 2); y1 = max(0, cy - h // 2)
    x2 = min(img.shape[1], x1 + w); y2 = min(img.shape[0], y1 + h)
    return img[y1:y2, x1:x2].copy(), (x1, y1)

def _binarize(img_bgr: np.ndarray) -> np.ndarray:
    g = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    g = cv2.GaussianBlur(g, (3, 3), 0)
    thr = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                cv2.THRESH_BINARY_INV, 31, 9)
    return thr

def _ocr_text(gray: np.ndarray, psm: int = 7) -> str:
    if not _HAVE_TESS:
        return ""
    cfg = f"--psm {psm}"
    txt = pytesseract.image_to_string(gray, lang="eng", config=cfg)
    return (txt or "").strip().replace("\n", " ")

# ======== Insert button via CV (ported) ========
def _click_insert_via_cv(adb: ADBAdapter, insert_roi_rect: Tuple[int, int, int, int], wait_pre: Optional[float] = None) -> bool:
    if wait_pre is None:
        wait_pre = float(os.getenv("PRE_INSERT_DELAY", "0.8"))
    time.sleep(wait_pre)
    img = _grab(adb)
    x1, y1, w, h = insert_roi_rect
    if w > 0 and h > 0:
        roi = img[y1:y1+h, x1:x1+w]
        ox, oy = x1, y1
    else:
        roi = img
        ox, oy = 0, 0

    pt, sc = CV.match_center_multiscale(roi, CV.read_tpl("insert.png"), thr=_CONF_INSERT_THR, scales=(0.9, 1.0, 1.1))
    if not pt:
        pt, sc = CV.match_center_multiscale(roi, CV.read_tpl("insert_alt.png"), thr=_CONF_INSERT_THR, scales=(0.9, 1.0, 1.1))
    if not pt:
        LOG.w("[INSERT] หา/กดปุ่ม 'ใส่ลง' ไม่เจอใน ROI")
        return False

    gx, gy = ox + pt[0], oy + pt[1]
    LOG.i(f"[INSERT] click at ({gx},{gy}) score={sc:.3f} thr={_CONF_INSERT_THR}")
    adb.tap(gx, gy)
    return True

def _save_item_lv_snap(crop, roi, thr, x0, y0, x1, y1, w, h, level, method, debug_tag: str):
    try:
        dbgdir = _CFG.get("debug_dir", "/app/cache/debug")
        outdir = os.path.join(dbgdir, "item_lv")
        os.makedirs(outdir, exist_ok=True)
        ts = int(time.time() * 1000)

        # 1) crop ทั้งกรอบ 60x60
        cv2.imwrite(os.path.join(outdir, f"{debug_tag}_{ts}_crop_{method}_lv{level}.png"), crop)

        # 2) roi (มุมขวาบนของ crop)
        cv2.imwrite(os.path.join(outdir, f"{debug_tag}_{ts}_roi_{method}_lv{level}.png"), roi)

        # 3) threshold สำหรับ OCR
        if thr is not None:
            cv2.imwrite(os.path.join(outdir, f"{debug_tag}_{ts}_thr_{method}_lv{level}.png"), thr)

        # 4) กล่อง overlay ให้เห็นตำแหน่งที่ใช้บน crop
        vis = crop.copy()
        # วาดกรอบของ ROI ภายใน crop
        cv2.rectangle(vis, (x1 - x0, y1 - y0), (x1 - x0 + w, y1 - y0 + h), (0, 255, 0), 2)
        cv2.imwrite(os.path.join(outdir, f"{debug_tag}_{ts}_box_{method}_lv{level}.png"), vis)
    except Exception as e:
        LOG.w(f"[item_lv_snap] save fail: {e}")

_PLUS_RE = re.compile(r"\+?\s*(\d{1,2})")

def _read_level_from_icon_topright(
    adb: ADBAdapter,
    item_center: Tuple[int,int],
    box_size: int = 60,
    debug_tag: str = "item"
) -> Optional[int]:
    """
    อ่านระดับไอเทมจากมุมขวาบนของไอคอนในช่อง
    ขั้นตอน:
      PASS A: yellow-mask + template matching (แม่น/เร็ว)
      PASS B: RGB template matching (สำรอง)
      PASS C: OCR เฉพาะ +0123456789 (สำรองสุดท้าย)
    จะบันทึกสแน็ปไว้ที่ {debug_dir}/item_lv/ และ (ถ้าตั้งค่า) เก็บ ROI ลง dataset:
      - MISS  -> {DATASET_DIR}/plus_unlabeled/
      - HIT 1..6 (เมื่อ SAVE_DATASET_ALL=1) -> {DATASET_DIR}/plus{n}/
    ต้องมี helper/ทรัพยากร: _yellow_digit_mask, _save_item_lv_snap, _save_plus_dataset, CV.read_tpl(), _grab, _crop_center
    """
    cx, cy = item_center
    img = _grab(adb)
    crop, (x0, y0) = _crop_center(img, cx, cy, box_size, box_size)

    # ROI: มุมขวาบนของไอคอน
    h, w = crop.shape[:2]
    rx0 = int(w * float(os.getenv("ICON_ROI_XRATIO", "0.55")))   # เริ่ม 55% ของความกว้าง
    ry0 = 0
    rw  = w - rx0
    rh  = int(h * float(os.getenv("ICON_ROI_HRATIO", "0.50")))   # สูง 50% บนสุด
    roi = crop[ry0:ry0+rh, rx0:rx0+rw].copy()
    tag = f"{debug_tag}_roi"

    # ---------- PASS A: Template matching บน "มาสก์สีเหลือง" ----------
    ymask = _yellow_digit_mask(roi)  # -> uint8 0/255
    best_lvl, best_sc = None, -1.0
    tm_mask_thr = float(os.getenv("CONF_PLUS_TM_THR", "0.78"))  # เกณฑ์บนมาสก์

    for n in range(1, 10):
        tpl = CV.read_tpl(f"plus{n}.png")
        if tpl is None:
            continue
        # แปลง template เป็นไบนารีเพื่อใช้ match กับมาสก์
        if tpl.ndim == 3:
            tpl = cv2.cvtColor(tpl, cv2.COLOR_BGR2GRAY)
        _, tpl_bin = cv2.threshold(tpl, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        res = cv2.matchTemplate(ymask, tpl_bin, cv2.TM_CCOEFF_NORMED)
        _, sc, _, _ = cv2.minMaxLoc(res)
        if sc > best_sc:
            best_sc = sc
            best_lvl = n

    if best_lvl is not None and best_sc >= tm_mask_thr:
        LOG.i(f"[ICON-PLUS MASK] level=+{best_lvl} score={best_sc:.3f} (thr={tm_mask_thr})")
        if os.getenv("SAVE_ITEM_LV_SNAP", "1") != "0":
            _save_item_lv_snap(crop, roi, ymask, x0, y0, x0+rx0, y0+ry0, rw, rh, best_lvl, "mask_tm", tag)
        if 1 <= best_lvl <= 6:
            _save_plus_dataset(roi, level=best_lvl, method="mask_tm", debug_tag=tag)
        return best_lvl

    # ---------- PASS B: Template matching (RGB ปกติ) ----------
    best_lvl, best_sc = None, -1.0
    tm_rgb_thr = float(os.getenv("CONF_PLUS_TM_THR", "0.82"))
    for n in range(1, 10):
        tpl = CV.read_tpl(f"plus{n}.png")
        if tpl is None:
            continue
        pt, sc = CV.match_center_multiscale(
            roi, tpl, thr=tm_rgb_thr, scales=(0.90, 0.95, 1.00, 1.05, 1.10)
        )
        if pt and sc > best_sc:
            best_sc = sc
            best_lvl = n

    if best_lvl is not None and best_sc >= tm_rgb_thr:
        LOG.i(f"[ICON-PLUS TM] level=+{best_lvl} score={best_sc:.3f} (thr={tm_rgb_thr})")
        if os.getenv("SAVE_ITEM_LV_SNAP", "1") != "0":
            _save_item_lv_snap(crop, roi, None, x0, y0, x0+rx0, y0+ry0, rw, rh, best_lvl, "rgb_tm", tag)
        if 1 <= best_lvl <= 6:
            _save_plus_dataset(roi, level=best_lvl, method="rgb_tm", debug_tag=tag)
        return best_lvl

    # ---------- PASS C: OCR (+0123456789) ----------
    if _HAVE_TESS:
        g  = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        # top-hat เน้นส่วนสว่างเล็ก ๆ (ตัวเลข) ก่อน threshold
        tophat = cv2.morphologyEx(g, cv2.MORPH_TOPHAT, np.ones((3,3), np.uint8), iterations=1)
        thr = cv2.adaptiveThreshold(tophat, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                    cv2.THRESH_BINARY_INV, 31, 9)
        cfg = "--psm 7 --oem 1 -c tessedit_char_whitelist=+0123456789"
        raw = pytesseract.image_to_string(thr, lang="eng", config=cfg)
        text = (raw or "").strip().replace("\n", " ")
        m = _PLUS_RE.search(text)
        if m:
            try:
                cand = int(m.group(1))
                if 0 <= cand <= 15:
                    LOG.i(f"[ICON-PLUS OCR] text='{text}' → level≈+{cand}")
                    if os.getenv("SAVE_ITEM_LV_SNAP", "1") != "0":
                        _save_item_lv_snap(crop, roi, thr, x0, y0, x0+rx0, y0+ry0, rw, rh, cand, "ocr", tag)
                    if 1 <= cand <= 6:
                        _save_plus_dataset(roi, level=cand, method="ocr", debug_tag=tag)
                    return cand
            except Exception:
                pass

    LOG.i("[ICON-PLUS] ไม่พบระดับ (mask/rgb/OCR ไม่ผ่าน)")
    if os.getenv("SAVE_ITEM_LV_SNAP", "1") != "0":
        _save_item_lv_snap(crop, roi, ymask, x0, y0, x0+rx0, y0+ry0, rw, rh, -1, "miss", tag)
    _save_plus_dataset(roi, level=None, method="miss", debug_tag=tag)  # เก็บไว้ให้คุณ label เอง
    return None



def _find_tooltip_box(roi_bgr: np.ndarray) -> Optional[Tuple[int,int,int,int]]:
    """
    หา tooltip box ภายใน ROI:
    1) ทำ HSV แล้วหาโซนมืด (พื้นดำโปร่ง ๆ) ด้วย threshold
    2) ปรับนอยซ์ (morph close/open)
    3) เลือกคอมโพเนนต์ที่ใหญ่สุดที่มีอัตราส่วน/ขนาดสมเหตุผล
    คืนค่าเป็น (x, y, w, h) ในพิกัดของ ROI หรือ None ถ้าไม่พบ
    """
    hsv = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2HSV)
    # ค่าพื้น ๆ สำหรับพื้นดำโปร่ง: V ต่ำ, S ไม่จำเป็นต้องต่ำมาก
    v_max = int(os.getenv("TOOLTIP_V_MAX", "80"))
    s_max = int(os.getenv("TOOLTIP_S_MAX", "255"))
    mask_dark = cv2.inRange(hsv, (0, 0, 0), (180, s_max, v_max))

    # ปรับนอยซ์ให้เป็นก้อนสี่เหลี่ยม
    k = int(os.getenv("TOOLTIP_KERNEL", "5"))
    kernel = np.ones((k, k), np.uint8)
    mask = cv2.morphologyEx(mask_dark, cv2.MORPH_CLOSE, kernel, iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,  kernel, iterations=1)

    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None

    h, w = roi_bgr.shape[:2]
    area_img = w * h
    best = None
    best_score = -1.0

    for c in cnts:
        x, y, ww, hh = cv2.boundingRect(c)
        area = ww * hh
        if area < area_img * 0.02:   # กรองชิ้นเล็ก ๆ <2% ของ ROI
            continue
        # tooltip มักจะผอมยาวแนวนอน
        ar = ww / max(1.0, float(hh))
        if ar < 2.2:                 # ถ้าสั้นเกินไปไม่น่าใช่ tooltip
            continue
        # ขอบกล่องควรอยู่ใน ROI ไม่ติดขอบเกินไป
        pad_ok = (x > 1 and y > 1 and (x+ww) < (w-1) and (y+hh) < (h-1))
        if not pad_ok:
            continue

        # ให้คะแนนตามพื้นที่และอัตราส่วน (อยากได้ก้อนยาวและใหญ่)
        score = area * (0.6 + 0.4 * min(ar/6.0, 1.0))
        if score > best_score:
            best_score = score
            best = (x, y, ww, hh)

    return best

def _read_bracket_level_from_status(adb: ADBAdapter,
                                    slot_status_roi_rect: Tuple[int, int, int, int],
                                    debug_tag: Optional[str] = "preinsert") -> Optional[int]:
    img = _grab(adb)
    roi, (ox, oy) = _crop_rect(img, slot_status_roi_rect)

    # 1) หา tooltip box ทั้งโซน (ใช้ที่เราคุยกันรอบก่อน)
    tip_rect = _find_tooltip_box(roi) if '_find_tooltip_box' in globals() else None
    if tip_rect:
        tx, ty, tw, th = tip_rect
        focus = roi[ty:ty+th, tx:tx+tw].copy()
        src = "tooltip"
    else:
        focus = roi
        tx, ty, tw, th = 0, 0, roi.shape[1], roi.shape[0]
        src = "roi-fallback"

    # 2) เตรียมภาพให้ชัดสำหรับฟอนต์ไทยบนพื้นมืด
    g  = cv2.cvtColor(focus, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
    g  = clahe.apply(g)
    thr = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                cv2.THRESH_BINARY_INV, 31, 9)
    thr = cv2.morphologyEx(thr, cv2.MORPH_OPEN, np.ones((2,2), np.uint8), iterations=1)

    if not _HAVE_TESS:
        return None

    # -------- Pass A: OCR ไทย+อังกฤษ (ไม่ whitelist) แล้ว regex หาวงเล็บ+ตัวเลข --------
    cfg_a = "--psm 7 --oem 1"
    raw_a = pytesseract.image_to_string(thr, lang="tha+eng", config=cfg_a)
    text_a = (raw_a or "").strip().replace("\n", " ")
    m = _BRACKET_NUM_RE.search(text_a)
    if m:
        try:
            lvl = int(m.group(1))
            LOG.i(f"[OCR-THA/ENG] src={src} text='{text_a}' → level={lvl}")
            _dump_ocr_debug(roi, focus, thr, tip_rect, debug_tag)
            return lvl
        except Exception:
            pass  # ลองเฟสถัดไป

    # -------- Pass B: หาเลขกลุ่มขวาสุด (กรณีไม่มีวงเล็บถูกอ่าน) --------
    # โฟกัสเฉพาะ 35% ขวาสุด
    h, w = thr.shape[:2]
    rx = int(w * 0.65)
    right_thr = thr[:, rx:].copy()
    cfg_b = "--psm 7 --oem 1 -c tessedit_char_whitelist=0123456789"
    raw_b = pytesseract.image_to_string(right_thr, lang="eng", config=cfg_b)
    text_b = (raw_b or "").strip().replace("\n", " ")
    # เอากลุ่มตัวเลขท้ายสุด 1–2 หลัก
    m2 = re.search(r"(\d{1,2})\s*$", text_b)
    if m2:
        try:
            cand = int(m2.group(1))
            # เงื่อนไขป้องกันพลาด: ต้องมีตัวอักษร/ช่องว่างก่อนหน้าใน focus (เหมือนชื่อไอเทม)
            # และค่าควรอยู่ในช่วง 0–15 (สมมติระดับสูงสุดไม่เกินนี้)
            if 0 <= cand <= 15:
                LOG.i(f"[OCR-rightmost-digits] src={src} text='{text_b}' → level≈{cand} (no brackets)")
                _dump_ocr_debug(roi, focus, thr, tip_rect, debug_tag)
                return cand
        except Exception:
            pass

    LOG.i(f"[OCR] ไม่พบระดับ → ถือว่าไม่มีระดับ (src={src}, textA='{text_a}', textB='{text_b}')")
    _dump_ocr_debug(roi, focus, thr, tip_rect, debug_tag)
    return None


# ======== Debug saver (safe path, unique name) ========
def _dump_ocr_debug(roi, focus, thr, tip_rect, debug_tag):
    if os.getenv("SAVE_SCREENCAP", "1") == "0":
        return
    try:
        dbgdir = _CFG.get("debug_dir", "/app/cache/debug")
        os.makedirs(dbgdir, exist_ok=True)
        ts = int(time.time()*1000)
        cv2.imwrite(os.path.join(dbgdir, f"{debug_tag}_roi_{ts}.png"), roi)
        cv2.imwrite(os.path.join(dbgdir, f"{debug_tag}_focus_{ts}.png"), focus)
        cv2.imwrite(os.path.join(dbgdir, f"{debug_tag}_thr_{ts}.png"), thr)
        if tip_rect:
            tx, ty, tw, th = tip_rect
            box = roi.copy()
            cv2.rectangle(box, (tx,ty), (tx+tw,ty+th), (0,255,0), 2)
            cv2.imwrite(os.path.join(dbgdir, f"{debug_tag}_box_{ts}.png"), box)
    except Exception as e:
        LOG.w(f"[debug dump] save fail: {e}")

# ======== Slot empty checks (after insert / before upgrade taps) ========
def _is_slot_empty(adb: ADBAdapter, slot_center: Tuple[int,int], slot_roi_size: Tuple[int,int]) -> bool:
    cx, cy = slot_center
    sw, sh = slot_roi_size
    img = _grab(adb)
    crop, _ = _crop_center(img, cx, cy, sw, sh)

    tpl = CV.read_tpl("slot_empty.png")
    if tpl is not None:
        thr = float(os.getenv("CONF_SLOT_EMPTY_THR", _CFG.get("CONF_SLOT_EMPTY_THR", 0.85)))
        pt, sc = CV.match_center_multiscale(crop, tpl, thr=thr, scales=(0.9, 1.0, 1.1))
        emp = (pt is not None and sc >= thr)
        LOG.i(f"[ตรวจช่อง] ว่าง={emp} (เทมเพลต score={sc:.3f} thr={thr})")
        return emp

    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    v = float(np.var(g))
    edges = cv2.Canny(g, 40, 120)
    edge_ratio = edges.mean()/255.0
    emp = (v < 400.0 and edge_ratio < 0.03)
    LOG.i(f"[ตรวจช่อง] ว่าง={emp} (ฮิวริสติก var={v:.1f} edge={edge_ratio:.3f})")
    return emp

def _yellow_digit_mask(bgr: np.ndarray) -> np.ndarray:
    """
    คืนค่ามาสก์สี (uint8 0/255) สำหรับข้อความ +n สีเหลืองเขียว
    ปรับได้ด้วย ENV:
      Y_H1, Y_H2 (0..179), Y_S_MIN (0..255), Y_V_MIN (0..255)
    ดีฟอลต์ครอบช่วงเหลือง->เหลืองเขียว
    """
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h1 = int(os.getenv("Y_H1", "18"))   # ~18
    h2 = int(os.getenv("Y_H2", "45"))   # ~45
    s  = int(os.getenv("Y_S_MIN", "120"))
    v  = int(os.getenv("Y_V_MIN", "140"))
    mask = cv2.inRange(hsv, (h1, s, v), (h2, 255, 255))
    # ทำให้สภาพอักษรต่อเนื่องขึ้น
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2,2), np.uint8), iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((2,2), np.uint8), iterations=1)
    return mask


# ======== Overlay result detection (OCR -> template -> color) ========
def _overlay_detect(adb: ADBAdapter, overlay_rect: Tuple[int,int,int,int]) -> Optional[str]:
    """
    return 'success' / 'fail' / None
    """
    img = _grab(adb)
    roi, _ = _crop_rect(img, overlay_rect)
    thr = _binarize(roi)

    txt = _ocr_text(thr, psm=7)
    if "สำเร็จ" in txt:
        LOG.i("[overlay OCR] พบ 'สำเร็จ'")
        return "success"
    if "ล้มเหลว" in txt:
        LOG.i("[overlay OCR] พบ 'ล้มเหลว'")
        return "fail"

    succ_thr = float(os.getenv("CONF_SUCCESS_THR", _CFG.get("CONF_SUCCESS_THR", 0.83)))
    fail_thr = float(os.getenv("CONF_FAIL_THR", _CFG.get("CONF_FAIL_THR", 0.83)))

    pt, sc = CV.match_center_multiscale(roi, CV.read_tpl("success.png"), thr=succ_thr, scales=(0.95, 1.00, 1.05))
    if pt:
        LOG.i("[overlay TM] success by template")
        return "success"

    pt, sc = CV.match_center_multiscale(roi, CV.read_tpl("fail.png"), thr=fail_thr, scales=(0.95, 1.00, 1.05))
    if pt:
        LOG.i("[overlay TM] fail by template")
        return "fail"

    # color heuristic
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    s_h1 = int(os.getenv("SUCCESS_H_MIN", _CFG.get("SUCCESS_H_MIN", 90)))
    s_h2 = int(os.getenv("SUCCESS_H_MAX", _CFG.get("SUCCESS_H_MAX", 140)))
    s_s  = int(os.getenv("SUCCESS_S_MIN", _CFG.get("SUCCESS_S_MIN", 60)))
    s_v  = int(os.getenv("SUCCESS_V_MIN", _CFG.get("SUCCESS_V_MIN", 80)))
    success_blue  = cv2.inRange(hsv, (s_h1, s_s, s_v), (s_h2, 255, 255))

    w_s_max = int(os.getenv("SUCCESS_WHITE_S_MAX", _CFG.get("SUCCESS_WHITE_S_MAX", 40)))
    w_v_min = int(os.getenv("SUCCESS_WHITE_V_MIN", _CFG.get("SUCCESS_WHITE_V_MIN", 200)))
    success_white = cv2.inRange(hsv, (0, 0, w_v_min), (180, w_s_max, 255))
    success_mask = cv2.bitwise_or(success_blue, success_white)

    f_or_h1 = int(os.getenv("FAIL_ORANGE_H_MIN", _CFG.get("FAIL_ORANGE_H_MIN", 10)))
    f_or_h2 = int(os.getenv("FAIL_ORANGE_H_MAX", _CFG.get("FAIL_ORANGE_H_MAX", 25)))
    f_s     = int(os.getenv("FAIL_S_MIN", _CFG.get("FAIL_S_MIN", 80)))
    f_v     = int(os.getenv("FAIL_V_MIN", _CFG.get("FAIL_V_MIN", 80)))
    fail_orange = cv2.inRange(hsv, (f_or_h1, f_s, f_v), (f_or_h2, 255, 255))
    f_r1 = cv2.inRange(hsv, (0,   f_s, f_v), (5,   255, 255))
    f_r2 = cv2.inRange(hsv, (170, f_s, f_v), (180, 255, 255))
    fail_mask = cv2.bitwise_or(fail_orange, cv2.bitwise_or(f_r1, f_r2))

    area = max(1, roi.shape[0] * roi.shape[1])
    succ_ratio = float(cv2.countNonZero(success_mask)) / float(area)
    fail_ratio = float(cv2.countNonZero(fail_mask)) / float(area)

    succ_min = float(os.getenv("SUCCESS_COLOR_MIN", _CFG.get("SUCCESS_COLOR_MIN", 0.06)))
    fail_min = float(os.getenv("FAIL_COLOR_MIN", _CFG.get("FAIL_COLOR_MIN", 0.06)))

    LOG.i(f"[overlay สี] success≈{succ_ratio:.3f}, fail≈{fail_ratio:.3f} (เกณฑ์ {succ_min:.2f}/{fail_min:.2f})")
    if succ_ratio >= succ_min and fail_ratio < fail_min:
        LOG.i("[overlay สี] ตัดสิน: success")
        return "success"
    if fail_ratio >= fail_min and succ_ratio < succ_min:
        LOG.i("[overlay สี] ตัดสิน: fail")
        return "fail"
    LOG.i("[overlay] ยังไม่ชัดเจน")
    return None

# ===================== Device context & logging =====================
def _ctx(dev_id: str) -> dict:
    c = _DEVICE_CTX.get(dev_id)
    if not c:
        c = dict(stage="pick", item_idx=0, base_level=None, successes=0, last_action_ts=0.0, _cfg_dumped=False)
        _DEVICE_CTX[dev_id] = c
        LOG.i(f"[{dev_id}] เริ่มคอนเท็กซ์ใหม่ stage=pick item_idx=0")
    return c

def _log_config_once(dev_id: str, *, slot_center, slot_status_roi, slot_roi_size, upgrade_btn, overlay_abs, insert_roi, items, swipe_cfg):
    c = _ctx(dev_id)
    if c.get("_cfg_dumped"):
        return
    LOG.i(f"[{dev_id}] CFG slot_center={slot_center}")
    LOG.i(f"[{dev_id}] CFG slot_status_roi=(x1={slot_status_roi[0]},y1={slot_status_roi[1]},w={slot_status_roi[2]},h={slot_status_roi[3]})")
    LOG.i(f"[{dev_id}] CFG slot_roi_size=[{slot_roi_size[0]},{slot_roi_size[1]}]")
    LOG.i(f"[{dev_id}] CFG upgrade_btn={upgrade_btn}")
    LOG.i(f"[{dev_id}] CFG overlay_abs=(x1={overlay_abs[0]},y1={overlay_abs[1]},w={overlay_abs[2]},h={overlay_abs[3]})")
    LOG.i(f"[{dev_id}] CFG insert_roi=(x1={insert_roi[0]},y1={insert_roi[1]},w={insert_roi[2]},h={insert_roi[3]})")
    LOG.i(f"[{dev_id}] CFG items(len)={len(items)} sample={items[:6]}")
    LOG.i(f"[{dev_id}] CFG swipe={swipe_cfg}")
    c["_cfg_dumped"] = True

# ---- centralized "next item" transition ----
def _next_item(dev_id: str, adb: ADBAdapter, items, swipe_cfg, c):
    c["item_idx"] += 1
    if items and (c["item_idx"] % len(items) == 0):
        x, y, dy, ms = swipe_cfg["x"], swipe_cfg["y"], swipe_cfg["dy"], swipe_cfg["ms"]
        LOG.i(f"[{dev_id}] SWIPE after {len(items)} items → ({x},{y})→({x},{y+dy}) ms={ms}")
        try:
            adb.swipe(x, y, dy=dy, ms=ms)
            time.sleep(0.25)
        except Exception as e:
            LOG.w(f"[{dev_id}] swipe fail: {e}")
    c["stage"] = "pick"
    c["successes"] = 0
    c["base_level"] = None

_DATASET_BASE = _CFG.get("dataset_dir", os.getenv("DATASET_DIR", "/app/data/dataset/level"))
_NORM_SIZE = (32, 28)  # ปรับได้ตามต้องการ

def _norm_roi_size(img, size=_NORM_SIZE):
    return cv2.resize(img, size, interpolation=cv2.INTER_AREA)

def _save_plus_dataset(roi_bgr: np.ndarray, *, level: Optional[int], method: str, debug_tag: str):
    """
    level=None หรือ -1  -> เก็บที่ plus_unlabeled/
    level in 1..6       -> เก็บที่ plus{n}/ (เมื่อ SAVE_DATASET_ALL=1)
    """
    try:
        if level is None or level < 1 or level > 6:
            sub = "plus_unlabeled"
        else:
            if os.getenv("SAVE_DATASET_ALL", "0") != "1":
                return  # ไม่เก็บกรณีอ่านได้ เว้นสั่งไว้
            sub = f"plus{level}"

        outdir = os.path.join(_DATASET_BASE, sub)
        os.makedirs(outdir, exist_ok=True)

        ts = int(time.time() * 1000)
        # บันทึกขนาดเดิม
        p_ori = os.path.join(outdir, f"{debug_tag}_{ts}_{method}_ori.png")
        cv2.imwrite(p_ori, roi_bgr)

        # บันทึกเวอร์ชัน normalize (เทรน/ทำเทมเพลตได้ทันที)
        roi_gray = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2GRAY)
        norm = _norm_roi_size(roi_gray)
        p_norm = os.path.join(outdir, f"{debug_tag}_{ts}_{method}_norm.png")
        cv2.imwrite(p_norm, norm)

        LOG.i(f"[dataset] saved → {p_ori} , {p_norm}")
    except Exception as e:
        LOG.w(f"[dataset] save fail: {e}")

# ===================== Main step =====================
def worker_step(controller) -> Dict[str, Any]:
    """
    State machine (integrated with high-accuracy logic):
    - pick    : tap item
    - inspect : read [n] from slot_status_roi BEFORE insert
    - insert  : click 'insert' via CV (no extra item tap); fallback slot_center (commented)
    - upgrade : tap upgrade button, detect overlay result (OCR->TM->Color); break behavior at level>=_BREAK_LEVEL_MIN
    """
    dev_id = controller.id
    target = controller.target_level

    adb = getattr(controller, "adb", None)
    if adb is None:
        adb = ADBAdapter(controller.device)
        controller.adb = adb

    items = _items()
    slot_center = _pt("slot_center", (0, 0))
    slot_roi_size = _size2("slot_roi", (80, 80))  # [w,h] legacy size for emptiness check
    upgrade_btn = _pt("upgrade_btn", (0, 0))
    overlay_abs = _rect("overlay_abs", (0, 0, 0, 0))
    slot_status_roi = _rect("slot_status_roi", (0, 0, 0, 0))  # dict {x1,y1,w,h}
    insert_roi = _rect("insert_roi", (0, 0, 0, 0))
    swipe_cfg = _swipe()

    _log_config_once(
        dev_id,
        slot_center=slot_center,
        slot_status_roi=slot_status_roi,
        slot_roi_size=slot_roi_size,
        upgrade_btn=upgrade_btn,
        overlay_abs=overlay_abs,
        insert_roi=insert_roi,
        items=items,
        swipe_cfg=swipe_cfg,
    )

    c = _ctx(dev_id)
    out: Dict[str, Any] = {}

    LOG.i(f"[{dev_id}] stage={c['stage']} item_idx={c['item_idx']} target=+{target} base={c.get('base_level')} succ={c.get('successes',0)}")

    # ---------- Stage: pick ----------
    if c["stage"] == "pick":
        if not items:
            LOG.w(f"[{dev_id}] ไม่พบ items ใน config")
            time.sleep(0.1)
            return {}

        idx = c["item_idx"] % len(items)
        ix, iy = items[idx]
        LOG.i(f"[{dev_id}] แตะไอเทม idx={idx} @({ix},{iy})")
        adb.tap(ix, iy)
        c["stage"] = "inspect"
        c["last_action_ts"] = time.time()
        return {}

    # ---------- Stage: inspect (read [n] before insert) ----------
    if c["stage"] == "inspect":
        remain = 0.20 - (time.time() - c["last_action_ts"])
        if remain > 0:
            time.sleep(min(0.05, remain))
            return {}

        # หา index และพิกัดของไอเทมปัจจุบัน
        idx = c["item_idx"] % len(items)
        ix, iy = items[idx]

        # 1) พยายามอ่านจากมุมขวาบนของไอคอนในช่อง (60x60)
        lvl = _read_level_from_icon_topright(
            adb,
            (ix, iy),
            box_size=int(os.getenv("ICON_BOX_SIZE", "68")),
            debug_tag=f"{dev_id}_idx{idx}"
        )

        # 2) ถ้ายังไม่ได้ ลองอ่านจาก tooltip (กรอบคำอธิบาย) เป็น fallback
        if lvl is None:
            lvl = _read_bracket_level_from_status(adb, slot_status_roi)

        c["base_level"] = lvl if isinstance(lvl, int) else None

        if lvl is None or (isinstance(lvl, int) and lvl < target):
            LOG.i(f"[{dev_id}] ตัดสินใจ INSERT (level={lvl}, target={target})")
            c["stage"] = "insert"
            c["successes"] = 0
            c["last_action_ts"] = time.time()
        else:
            LOG.i(f"[{dev_id}] ข้ามไอเทมนี้ (level={lvl} >= target={target})")
            _next_item(dev_id, adb, items, swipe_cfg, c)
        return {}

    # ---------- Stage: insert (CV insert then wait 0.5s) ----------
    if c["stage"] == "insert":
        idx = c["item_idx"] % len(items)
        ix, iy = items[idx]
        LOG.i(f"[{dev_id}] INSERT: เตรียมกด 'ใส่ลง' ด้วย CV สำหรับ idx={idx} @({ix},{iy})")

        LOG.i(f"[{dev_id}] INSERT: try CV insert in roi=({insert_roi[0]},{insert_roi[1]},{insert_roi[2]},{insert_roi[3]})")
        ok = _click_insert_via_cv(adb, insert_roi_rect=insert_roi, wait_pre=None)
        if not ok:
            sx, sy = slot_center
            LOG.w(f"[{dev_id}] INSERT: CV not found → fallback slot_center @({sx},{sy})")
            # adb.tap(sx, sy)   # ใช้เมื่อจำเป็นเท่านั้น

        LOG.i(f"[{dev_id}] INSERT: done → รอ 0.5s")
        time.sleep(0.5)

        c["stage"] = "upgrade"
        c["last_action_ts"] = time.time()
        return {}

    # ---------- Stage: upgrade (loop tap -> overlay detect -> count successes) ----------
    if c["stage"] == "upgrade":
        base = c["base_level"] or 0
        cur = base + c["successes"]
        LOG.i(f"[{dev_id}] UPGRADE base={base} succ={c['successes']} cur={cur} / target=+{target}")

        # Stop conditions
        if cur >= target:
            LOG.i(f"[{dev_id}] บรรลุเป้าหมาย +{target} → นับชิ้นสำเร็จ 1 ชิ้น และไปชิ้นถัดไป")
            _next_item(dev_id, adb, items, swipe_cfg, c)
            return {"done_item": True}

        # Pre-check: slot still there?
        if _is_slot_empty(adb, slot_center, slot_roi_size):
            LOG.i(f"[{dev_id}] ช่องว่าง (ไอเทมหาย/แตก) → ข้ามชิ้นนี้")
            _next_item(dev_id, adb, items, swipe_cfg, c)
            return {"break_at_level": cur}

        ux, uy = upgrade_btn
        LOG.i(f"[{dev_id}] UPGRADE: click upgrade_btn @({ux},{uy})")
        adb.tap(ux, uy)
        out["upgrade_clicks"] = out.get("upgrade_clicks", 0) + 1
        time.sleep(_POST_UPGRADE_WAIT)

        # Detect overlay
        verdict = _overlay_detect(adb, overlay_abs)
        if verdict == "success":
            c["successes"] += 1
            out["success_clicks"] = out.get("success_clicks", 0) + 1
            LOG.i(f"[{dev_id}] ผล: สำเร็จ (+1) → success={c['successes']}")
            return out

        # fail ทันที
        if verdict == "fail":
            # เดิม: มีเงื่อนไข break เมื่อ cur >= _BREAK_LEVEL_MIN (ค่าเริ่มต้น 4)
            # แก้: ไม่ break — ให้พยายามต่อจนถึง target
            LOG.i(f"[{dev_id}] ผล: ล้มเหลว → พยายามต่อจนถึง target={target}")
            return out

        # Not clear → poll within 1.5s
        t0 = time.time()
        got = None
        while time.time() - t0 < 1.5:
            time.sleep(0.3)
            chk = _overlay_detect(adb, overlay_abs)
            if chk in ("success", "fail"):
                got = chk
                break
        if got == "success":
            c["successes"] += 1
            out["success_clicks"] = out.get("success_clicks", 0) + 1
            LOG.i(f"[{dev_id}] (ดีเลย์) สำเร็จ → success={c['successes']}")
            return out
        elif got == "fail":
            LOG.i(f"[{dev_id}] (ดีเลย์) ล้มเหลว → พยายามต่อจนถึง target={target}")
            return out

        LOG.i(f"[{dev_id}] overlay ยังไม่ชัดเจน → จะลองต่อในรอบถัดไป")
        return out

    # ---------- Fallback ----------
    LOG.w(f"[{dev_id}] พบ stage ไม่รู้จัก: {c['stage']} → รีเซ็ตเป็น pick")
    c["stage"] = "pick"
    return {}

# ===================== Loop wrapper (unchanged) =====================
def worker_loop(ctrl, step_fn, sleep_sec: float = 0.15):
    LOG.i(f"[{ctrl.id}] loop start")
    try:
        t0_item = time.time()
        while not ctrl.stop_event.is_set():
            if ctrl.pause_event.is_set():
                time.sleep(0.2)
                continue
            try:
                step_fn(ctrl)
                ctrl.last_tick = time.time()
            except Exception as e:
                ctrl.last_error = str(e)
                ctrl.state = "error"
                LOG.e(f"[{ctrl.id}] step error: {e}")
                break

            # per-item timeout guard (optional)
            if time.time() - t0_item > _MAX_ITEM_TIME_SEC:
                LOG.i(f"[{ctrl.id}] item timeout {_MAX_ITEM_TIME_SEC}s → advance item")
                c = _ctx(ctrl.id)
                # ใช้เส้นทางรวมศูนย์ เพื่อคงลอจิก swipe ทุก 6 ชิ้น
                items = _items()
                swipe_cfg = _swipe()
                adb = getattr(ctrl, "adb", None)
                if adb is None:
                    adb = ADBAdapter(ctrl.device)
                    ctrl.adb = adb
                _next_item(ctrl.id, adb, items, swipe_cfg, c)
                t0_item = time.time()

            # heartbeat
            if int(ctrl.last_tick) % 3 == 0:
                LOG.i(f"[{ctrl.id}] heartbeat items={getattr(ctrl,'items_upgraded_done',0)} target=+{getattr(ctrl,'target_level',0)}")

            time.sleep(sleep_sec)
    finally:
        LOG.i(f"[{ctrl.id}] loop exit (state={ctrl.state})")
