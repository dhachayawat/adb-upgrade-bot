# app/worker_step.py
from __future__ import annotations
from typing import Dict, Any, Optional, Tuple
import os
import re, time
import cv2
import numpy as np
import html as _html  # สำหรับสร้าง HTML ไปโชว์บนเว็บ

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
_POST_UPGRADE_WAIT = float(os.getenv("POST_UPGRADE_WAIT_SEC", "0.7"))
_MAX_ITEM_TIME_SEC = int(os.getenv("MAX_ITEM_TIME_SEC", "30"))
_BREAK_LEVEL_MIN   = int(_CFG.get("break_level_min", 4))

# ===================== Web UI log helpers =====================
def _lv_color(lv: Optional[int], target: int) -> str:
    if lv is None:
        return "#9e9e9e"   # เทา
    if lv >= target:
        return "#43a047"   # เขียว
    if lv >= (target - 1):
        return "#fb8c00"   # ส้ม
    return "#e53935"       # แดง

def _lv_html(lv: Optional[int], target: int) -> str:
    txt = "none" if lv is None else str(lv)
    col = _lv_color(lv, target)
    return f'<span class="adb-lv" style="color:{col};font-weight:600">{_html.escape(txt)}</span>'

def _log_web(dev_id: str, html_msg: str, level: str = "INFO"):
    """ส่งข้อความ HTML ไปยัง Web UI log; ถ้าไม่มี rtlog.web จะ fallback เป็น LOG.i"""
    try:
        from .rtlog import web as _rtlog_web
        _rtlog_web(dev_id, html_msg, level.upper())
    except Exception:
        LOG.i(f"[WEB-LOG fallback] {html_msg}")

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
    _log_web(adb.serial if hasattr(adb, "serial") else "dev", f'INSERT: click @({gx},{gy})')
    adb.tap(gx, gy)
    return True

# ======== Snapshot for icon-level reading ========
def _save_item_lv_snap(crop, roi, thr, x0, y0, x1, y1, w, h, level, method, debug_tag: str):
    try:
        dbgdir = _CFG.get("debug_dir", "/app/cache/debug")
        outdir = os.path.join(dbgdir, "item_lv")
        os.makedirs(outdir, exist_ok=True)
        ts = int(time.time() * 1000)
        cv2.imwrite(os.path.join(outdir, f"{debug_tag}_{ts}_crop_{method}_lv{level}.png"), crop)
        cv2.imwrite(os.path.join(outdir, f"{debug_tag}_{ts}_roi_{method}_lv{level}.png"), roi)
        if thr is not None:
            cv2.imwrite(os.path.join(outdir, f"{debug_tag}_{ts}_thr_{method}_lv{level}.png"), thr)
        vis = crop.copy()
        cv2.rectangle(vis, (x1 - x0, y1 - y0), (x1 - x0 + w, y1 - y0 + h), (0, 255, 0), 2)
        cv2.imwrite(os.path.join(outdir, f"{debug_tag}_{ts}_box_{method}_lv{level}.png"), vis)
    except Exception as e:
        LOG.w(f"[item_lv_snap] save fail: {e}")

# ======== Dataset helpers ========
_DATASET_BASE = _CFG.get("dataset_dir", os.getenv("DATASET_DIR", "/app/data/dataset/level"))
_NORM_SIZE = (32, 28)  # ขนาด normalize สำหรับเทมเพลต bank

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
        p_ori = os.path.join(outdir, f"{debug_tag}_{ts}_{method}_ori.png")
        cv2.imwrite(p_ori, roi_bgr)

        roi_gray = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2GRAY)
        norm = _norm_roi_size(roi_gray)
        p_norm = os.path.join(outdir, f"{debug_tag}_{ts}_{method}_norm.png")
        cv2.imwrite(p_norm, norm)

        LOG.i(f"[dataset] saved → {p_ori} , {p_norm}")
    except Exception as e:
        LOG.w(f"[dataset] save fail: {e}")

# === Template bank (loaded from dataset) ===
_PLUS_TPL_BANK: Dict[int, list] = {}  # {level: [gray ndarray templates ...]}

def _load_plus_template_bank(base_dir: Optional[str] = None) -> Dict[int, list]:
    """
    โหลด bank ของเทมเพลตจาก DATASET_DIR/plus{1..6}/*_norm.png
    """
    base = base_dir or _DATASET_BASE
    bank: Dict[int, list] = {}
    loaded = []
    for n in range(1, 7):  # ใช้แค่ 1..6 ตามที่กำหนด
        d = os.path.join(base, f"plus{n}")
        if not os.path.isdir(d):
            continue
        tpls = []
        for fn in os.listdir(d):
            if not fn.endswith("_norm.png"):
                continue
            path = os.path.join(d, fn)
            im = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
            if im is None:
                continue
            im = _norm_roi_size(im)
            tpls.append(im)
        if tpls:
            bank[n] = tpls
            loaded.append(f"+{n}:{len(tpls)}")
    LOG.i("[plus-bank] loaded → " + (", ".join(loaded) if loaded else "(empty)"))
    return bank

def _best_level_by_bank_on_mask(ymask_norm: np.ndarray) -> Tuple[Optional[int], float]:
    best_lvl, best_sc = None, -1.0
    for lvl, tpls in _PLUS_TPL_BANK.items():
        for tpl in tpls:
            res = cv2.matchTemplate(ymask_norm, tpl, cv2.TM_CCOEFF_NORMED)
            _, sc, _, _ = cv2.minMaxLoc(res)
            if sc > best_sc:
                best_sc, best_lvl = sc, lvl
    return best_lvl, best_sc

def _best_level_by_bank_on_gray(roi_gray_norm: np.ndarray) -> Tuple[Optional[int], float]:
    best_lvl, best_sc = None, -1.0
    for lvl, tpls in _PLUS_TPL_BANK.items():
        for tpl in tpls:
            res = cv2.matchTemplate(roi_gray_norm, tpl, cv2.TM_CCOEFF_NORMED)
            _, sc, _, _ = cv2.minMaxLoc(res)
            if sc > best_sc:
                best_sc, best_lvl = sc, lvl
    return best_lvl, best_sc

# ======== Dataset classifier (PASS 0) ========
_PLUS_CLS_CENTROIDS: Dict[int, np.ndarray] = {}  # level -> mean feature vector

def _feat_from_norm(gray_norm: np.ndarray) -> np.ndarray:
    # binary + gray flatten + L2 normalize
    _, bw = cv2.threshold(gray_norm, 0, 255, cv2.THRESH_BINARY+cv2.THRESH_OTSU)
    g = (gray_norm.astype(np.float32) / 255.0).ravel()
    b = (bw.astype(np.float32) / 255.0).ravel()
    v = np.concatenate([g, b], axis=0)
    n = np.linalg.norm(v) + 1e-8
    return v / n

def _build_plus_classifier(base_dir: Optional[str] = None) -> Dict[int, np.ndarray]:
    base = base_dir or _DATASET_BASE
    cents: Dict[int, np.ndarray] = {}
    for lvl in range(1, 7):
        d = os.path.join(base, f"plus{lvl}")
        if not os.path.isdir(d):
            continue
        feats = []
        for fn in os.listdir(d):
            if not fn.endswith("_norm.png"):
                continue
            path = os.path.join(d, fn)
            im = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
            if im is None:
                continue
            im = _norm_roi_size(im)
            feats.append(_feat_from_norm(im))
        if feats:
            cents[lvl] = np.mean(np.stack(feats, axis=0), axis=0)
    LOG.i("[plus-cls] centroids → " + (", ".join([f"+{k}" for k in sorted(cents.keys())]) if cents else "(empty)"))
    return cents

def _predict_level_by_classifier(roi_gray_norm: np.ndarray) -> Tuple[Optional[int], float]:
    if not _PLUS_CLS_CENTROIDS:
        return None, 0.0
    q = _feat_from_norm(roi_gray_norm)
    best_lvl, best_sim = None, -1.0
    for lvl, c in _PLUS_CLS_CENTROIDS.items():
        sim = float(np.dot(q, c) / (np.linalg.norm(c) + 1e-8))  # cosine
        if sim > best_sim:
            best_sim, best_lvl = sim, lvl
    return best_lvl, best_sim

# ===================== Slot empty checks (after insert / before upgrade taps) =====================
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
    ปรับได้ด้วย ENV: Y_H1, Y_H2, Y_S_MIN, Y_V_MIN
    """
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h1 = int(os.getenv("Y_H1", "18"))
    h2 = int(os.getenv("Y_H2", "45"))
    s  = int(os.getenv("Y_S_MIN", "120"))
    v  = int(os.getenv("Y_V_MIN", "140"))
    mask = cv2.inRange(hsv, (h1, s, v), (h2, 255, 255))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2,2), np.uint8), iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((2,2), np.uint8), iterations=1)
    return mask

# ======== Overlay detector (smart fused) ========
def _overlay_detect(adb: ADBAdapter, overlay_rect: Tuple[int,int,int,int]) -> Optional[str]:
    """
    return: 'success' / 'fail' / None

    เวอร์ชัน smart:
      - รอแบบไดนามิกด้วย OVERLAY_MIN_WAIT..OVERLAY_MAX_WAIT และ OVERLAY_POLL_INTERVAL
      - ตรวจ motion + โทนสีเอฟเฟกต์ (อุ่น=fail, เย็น/ขาว=success) + template + OCR แล้วฟิวส์คะแนน
      - ถ้าไม่มีสัญญาณ (สีต่ำ + motion ต่ำ) → ถือว่ายังไม่ upgrade → คืน None
    """
    def _color_scores(bgr_roi):
        hsv = cv2.cvtColor(bgr_roi, cv2.COLOR_BGR2HSV)
        # fail (อุ่น/ร้อน)
        f_or_h1 = int(os.getenv("FAIL_ORANGE_H_MIN", _CFG.get("FAIL_ORANGE_H_MIN", 10)))
        f_or_h2 = int(os.getenv("FAIL_ORANGE_H_MAX", _CFG.get("FAIL_ORANGE_H_MAX", 25)))
        f_s     = int(os.getenv("FAIL_S_MIN",        _CFG.get("FAIL_S_MIN",        80)))
        f_v     = int(os.getenv("FAIL_V_MIN",        _CFG.get("FAIL_V_MIN",        80)))
        fail_orange = cv2.inRange(hsv, (f_or_h1, f_s, f_v), (f_or_h2, 255, 255))
        f_r1 = cv2.inRange(hsv, (0,   f_s, f_v), (5,   255, 255))
        f_r2 = cv2.inRange(hsv, (170, f_s, f_v), (180, 255, 255))
        warm_mask = cv2.bitwise_or(fail_orange, cv2.bitwise_or(f_r1, f_r2))
        # success (เย็น/ฟ้า)
        s_h1 = int(os.getenv("SUCCESS_H_MIN", _CFG.get("SUCCESS_H_MIN", 90)))
        s_h2 = int(os.getenv("SUCCESS_H_MAX", _CFG.get("SUCCESS_H_MAX", 140)))
        s_s  = int(os.getenv("SUCCESS_S_MIN", _CFG.get("SUCCESS_S_MIN", 60)))
        s_v  = int(os.getenv("SUCCESS_V_MIN", _CFG.get("SUCCESS_V_MIN", 80)))
        cool_mask = cv2.inRange(hsv, (s_h1, s_s, s_v), (s_h2, 255, 255))
        # success (ขาว)
        w_s_max = int(os.getenv("SUCCESS_WHITE_S_MAX", _CFG.get("SUCCESS_WHITE_S_MAX", 40)))
        w_v_min = int(os.getenv("SUCCESS_WHITE_V_MIN", _CFG.get("SUCCESS_WHITE_V_MIN", 200)))
        white_mask = cv2.inRange(hsv, (0, 0, w_v_min), (180, w_s_max, 255))
        area = max(1, bgr_roi.shape[0] * bgr_roi.shape[1])
        warm_ratio  = float(cv2.countNonZero(warm_mask))  / float(area)
        cool_ratio  = float(cv2.countNonZero(cool_mask))  / float(area)
        white_ratio = float(cv2.countNonZero(white_mask)) / float(area)
        return warm_ratio, cool_ratio, white_ratio

    def _motion_score(prev_gray, cur_bgr):
        g = cv2.cvtColor(cur_bgr, cv2.COLOR_BGR2GRAY)
        if prev_gray is None:
            return g, 0.0
        diff = cv2.absdiff(g, prev_gray)
        return g, float(np.mean(diff)) / 255.0

    # --- พารามิเตอร์รอ/โพลล์ ---
    min_wait   = float(os.getenv("OVERLAY_MIN_WAIT",   "0.25"))
    max_wait   = float(os.getenv("OVERLAY_MAX_WAIT",   "1.80"))
    poll_int   = float(os.getenv("OVERLAY_POLL_INTERVAL", "0.12"))
    mot_thr    = float(os.getenv("OVERLAY_MOTION_THR", "0.015"))

    succ_min = float(os.getenv("SUCCESS_COLOR_MIN", _CFG.get("SUCCESS_COLOR_MIN", 0.06)))
    fail_min = float(os.getenv("FAIL_COLOR_MIN",    _CFG.get("FAIL_COLOR_MIN",    0.06)))

    tm_succ_thr = float(os.getenv("CONF_SUCCESS_THR", _CFG.get("CONF_SUCCESS_THR", 0.83)))
    tm_fail_thr = float(os.getenv("CONF_FAIL_THR",    _CFG.get("CONF_FAIL_THR",    0.83)))

    w_ocr   = float(os.getenv("OVERLAY_WEIGHT_OCR",   "0.50"))
    w_tm    = float(os.getenv("OVERLAY_WEIGHT_TM",    "0.30"))
    w_color = float(os.getenv("OVERLAY_WEIGHT_COLOR", "0.20"))
    margin  = float(os.getenv("OVERLAY_CONF_MARGIN",  "0.15"))

    t0 = time.time()
    prev_gray = None
    seen_signal = False
    best = dict(verdict=None, conf=0.0, ocr="", tm_s=0.0, tm_f=0.0,
                warm=0.0, cool=0.0, white=0.0, motion=0.0)

    # รอขั้นต่ำก่อนเริ่มอ่าน
    while time.time() - t0 < min_wait:
        time.sleep(0.02)

    while time.time() - t0 < max_wait:
        time.sleep(poll_int)
        img = _grab(adb)
        roi, _ = _crop_rect(img, overlay_rect)

        # motion
        prev_gray, mot = _motion_score(prev_gray, roi)

        # สี
        warm, cool, white = _color_scores(roi)
        coolwhite = cool + white

        # template (คะแนนดิบ 0..1)
        _, scs = CV.match_center_multiscale(roi, CV.read_tpl("success.png"), thr=0.0, scales=(0.95,1.0,1.05))
        _, scf = CV.match_center_multiscale(roi, CV.read_tpl("fail.png"),    thr=0.0, scales=(0.95,1.0,1.05))

        # OCR
        ocr_score_succ = ocr_score_fail = 0.0
        ocr_txt = ""
        if _HAVE_TESS:
            thrimg = _binarize(roi)
            ocr_txt = _ocr_text(thrimg, psm=7)
            if "สำเร็จ" in ocr_txt: ocr_score_succ = 1.0
            if "ล้มเหลว" in ocr_txt: ocr_score_fail = 1.0

        # เห็นสัญญาณหรือยัง
        color_hit = (coolwhite >= (succ_min*0.6)) or (warm >= (fail_min*0.6))
        mot_hit   = (mot >= mot_thr)
        if color_hit or mot_hit:
            seen_signal = True

        # รวมคะแนน
        tm_s_n = max(0.0, (scs - tm_succ_thr) / max(1e-6, 1.0 - tm_succ_thr))
        tm_f_n = max(0.0, (scf - tm_fail_thr) / max(1e-6, 1.0 - tm_fail_thr))

        col_s = min(1.0, coolwhite / max(1e-6, succ_min)) if coolwhite >= succ_min else 0.0
        col_f = min(1.0, warm      / max(1e-6, fail_min)) if warm      >= fail_min  else 0.0

        sc_succ = (w_ocr*ocr_score_succ) + (w_tm*tm_s_n) + (w_color*col_s)
        sc_fail = (w_ocr*ocr_score_fail) + (w_tm*tm_f_n) + (w_color*col_f)
        conf = abs(sc_succ - sc_fail)

        cand = None
        if sc_succ - sc_fail > margin:
            cand = "success"
        elif sc_fail - sc_succ > margin:
            cand = "fail"

        if (cand is not None) and (conf > best["conf"]):
            best.update(dict(verdict=cand, conf=float(min(1.0, conf)),
                             ocr=ocr_txt, tm_s=float(scs), tm_f=float(scf),
                             warm=float(warm), cool=float(cool), white=float(white), motion=float(mot)))

        # ออกจากลูปเมื่อได้ verdict หลัง min_wait หรือถึง max_wait
        if (best["verdict"] is not None) and (time.time() - t0 >= min_wait):
            break

    # ไม่มีสัญญาณเลย → ยังไม่ upgrade
    if best["verdict"] is None and not seen_signal:
        LOG.i("[overlay smart] no-signal: screen steady / no effect → undecided")
        return None

    # ยังไม่มี verdict แต่เห็นสัญญาณ → ใช้สีชี้ขาดแบบ fallback
    if best["verdict"] is None:
        if best["warm"] > (best["cool"] + best["white"]):
            best["verdict"], best["conf"] = "fail", 0.4
        elif (best["cool"] + best["white"]) > best["warm"]:
            best["verdict"], best["conf"] = "success", 0.4

    # log ไป Web UI
    try:
        col = "#42a5f5" if best["verdict"] == "success" else ("#ef5350" if best["verdict"] == "fail" else "#6c757d")
        _log_web(getattr(adb, "dev_id", "?"),
                 ('overlay: <b style="color:%s">%s</b> '
                  '(conf=%.2f, warm=%.3f, cool=%.3f, white=%.3f, tmS=%.2f, tmF=%.2f, mot=%.3f, ocr="%s")'
                  % (col, best["verdict"] or "None", best["conf"], best["warm"], best["cool"], best["white"], best["tm_s"], best["tm_f"], best["motion"], best["ocr"])),
                 "INFO")
    except Exception:
        pass

    if best["verdict"]:
        LOG.i(f"[overlay fused] {best['verdict']} (conf={best['conf']:.2f}; warm={best['warm']:.3f}, cool={best['cool']:.3f}, white={best['white']:.3f}, tmS={best['tm_s']:.2f}, tmF={best['tm_f']:.2f})")
    else:
        LOG.i("[overlay fused] unclear")

    return best["verdict"]

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
    LOG.i(f"[{dev_id}] CFG insert_roi=(x1={insert_roi[0]},{insert_roi[1]},{insert_roi[2]},{insert_roi[3]})")
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
            adb.tap(970, 120)   # touch for close tooltip, safe
            time.sleep(0.25)
            adb.swipe(x, y, dy=dy, ms=ms)
            time.sleep(0.25)
        except Exception as e:
            LOG.w(f"[{dev_id}] swipe fail: {e}")
    c["stage"] = "pick"
    c["successes"] = 0
    c["base_level"] = None

# ===================== Level readers (icon & tooltip) =====================
def _read_level_from_icon_topright(
    adb: ADBAdapter,
    item_center: Tuple[int,int],
    box_size: int = 60,
    debug_tag: str = "item",
    force: bool = False
) -> Optional[int]:
    """
    อ่านระดับไอเทมจากมุมขวาบนของไอคอนในช่อง
    ลำดับ:
      PASS 0 : dataset classifier (centroid cosine)  **เพิ่มใหม่**
      PASS A1: dataset bank บน yellow-mask
      PASS A : yellow-mask + single template TM
      PASS B1: dataset bank บน gray ROI
      PASS B : RGB template
      PASS C : OCR (+0123456789)
    """
    cx, cy = item_center
    img = _grab(adb)
    crop, (x0, y0) = _crop_center(img, cx, cy, box_size, box_size)

    # ROI: มุมขวาบนของไอคอน
    h, w = crop.shape[:2]
    rx0 = int(w * float(os.getenv("ICON_ROI_XRATIO", "0.55")))
    ry0 = 0
    rw  = w - rx0
    rh  = int(h * float(os.getenv("ICON_ROI_HRATIO", "0.50")))
    roi = crop[ry0:ry0+rh, rx0:rx0+rw].copy()
    tag = f"{debug_tag}_roi"

    # เตรียมโดเมนต่าง ๆ
    ymask = _yellow_digit_mask(roi)          # mask (0/255)
    ymask_norm = _norm_roi_size(ymask)       # normalize
    roi_gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    roi_gray_norm = _norm_roi_size(roi_gray)

    if force:
        _save_plus_dataset(roi, level=None, method="cls", debug_tag=tag) #force save
        return None
    # ---------- PASS 0: dataset classifier (centroid cosine) ----------
    cls_thr = float(os.getenv("CONF_PLUS_CLS_THR", "0.88"))
    lvl_c, sc_c = _predict_level_by_classifier(roi_gray_norm)
    if lvl_c is not None and sc_c >= cls_thr:
        LOG.i(f"[ICON-PLUS CLS] level=+{lvl_c} cos={sc_c:.3f} (thr={cls_thr})")
        if os.getenv("SAVE_ITEM_LV_SNAP", "1") != "0":
            _save_item_lv_snap(crop, roi, roi_gray_norm, x0, y0, x0+rx0, y0+ry0, rw, rh, lvl_c, "cls", tag)
        if 1 <= lvl_c <= 6:
            _save_plus_dataset(roi, level=lvl_c, method="cls", debug_tag=tag)
        return lvl_c

    # ---------- PASS A1: dataset bank on yellow-mask ----------
    if _PLUS_TPL_BANK:
        lvl_bm, sc_bm = _best_level_by_bank_on_mask(ymask_norm)
        thr_bank = float(os.getenv("CONF_PLUS_BANK_THR", "0.80"))
        if lvl_bm is not None and sc_bm >= thr_bank:
            LOG.i(f"[ICON-PLUS BANK(mask)] level=+{lvl_bm} score={sc_bm:.3f} (thr={thr_bank})")
            if os.getenv("SAVE_ITEM_LV_SNAP", "1") != "0":
                _save_item_lv_snap(crop, roi, ymask_norm, x0, y0, x0+rx0, y0+ry0, rw, rh, lvl_bm, "bank_mask", tag)
            if 1 <= lvl_bm <= 6:
                _save_plus_dataset(roi, level=lvl_bm, method="bank_mask", debug_tag=tag)
            return lvl_bm

    # ---------- PASS A: yellow-mask + single-template TM ----------
    # best_lvl, best_sc = None, -1.0
    # tm_mask_thr = float(os.getenv("CONF_PLUS_TM_THR", "0.78"))
    # for n in range(1, 10):
    #     tpl = CV.read_tpl(f"plus{n}.png")
    #     if tpl is None:
    #         continue
    #     if tpl.ndim == 3:
    #         tpl = cv2.cvtColor(tpl, cv2.COLOR_BGR2GRAY)
    #     _, tpl_bin = cv2.threshold(tpl, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    #     res = cv2.matchTemplate(ymask, tpl_bin, cv2.TM_CCOEFF_NORMED)
    #     _, sc, _, _ = cv2.minMaxLoc(res)
    #     if sc > best_sc:
    #         best_sc = sc
    #         best_lvl = n
    # if best_lvl is not None and best_sc >= tm_mask_thr:
    #     LOG.i(f"[ICON-PLUS MASK] level=+{best_lvl} score={best_sc:.3f} (thr={tm_mask_thr})")
    #     if os.getenv("SAVE_ITEM_LV_SNAP", "1") != "0":
    #         _save_item_lv_snap(crop, roi, ymask, x0, y0, x0+rx0, y0+ry0, rw, rh, best_lvl, "mask_tm", tag)
    #     if 1 <= best_lvl <= 6:
    #         _save_plus_dataset(roi, level=best_lvl, method="mask_tm", debug_tag=tag)
    #     return best_lvl

    # ---------- PASS B1: dataset bank on gray ROI ----------
    if _PLUS_TPL_BANK:
        lvl_bg, sc_bg = _best_level_by_bank_on_gray(roi_gray_norm)
        thr_bank_rgb = float(os.getenv("CONF_PLUS_BANK_RGB_THR", "0.83"))
        if lvl_bg is not None and sc_bg >= thr_bank_rgb:
            LOG.i(f"[ICON-PLUS BANK(gray)] level=+{lvl_bg} score={sc_bg:.3f} (thr={thr_bank_rgb})")
            if os.getenv("SAVE_ITEM_LV_SNAP", "1") != "0":
                _save_item_lv_snap(crop, roi, None, x0, y0, x0+rx0, y0+ry0, rw, rh, lvl_bg, "bank_gray", tag)
            if 1 <= lvl_bg <= 6:
                _save_plus_dataset(roi, level=lvl_bg, method="bank_gray", debug_tag=tag)
            return lvl_bg

    # ---------- PASS B: RGB template matching (สำรอง) ----------
    # best_lvl, best_sc = None, -1.0
    # tm_rgb_thr = float(os.getenv("CONF_PLUS_TM_THR", "0.82"))
    # for n in range(1, 10):
    #     tpl = CV.read_tpl(f"plus{n}.png")
    #     if tpl is None:
    #         continue
    #     pt, sc = CV.match_center_multiscale(
    #         roi, tpl, thr=tm_rgb_thr, scales=(0.90, 0.95, 1.00, 1.05, 1.10)
    #     )
    #     if pt and sc > best_sc:
    #         best_sc = sc
    #         best_lvl = n
    # if best_lvl is not None and best_sc >= tm_rgb_thr:
    #     LOG.i(f"[ICON-PLUS TM] level=+{best_lvl} score={best_sc:.3f} (thr={tm_rgb_thr})")
    #     if os.getenv("SAVE_ITEM_LV_SNAP", "1") != "0":
    #         _save_item_lv_snap(crop, roi, None, x0, y0, x0+rx0, y0+ry0, rw, rh, best_lvl, "rgb_tm", tag)
    #     if 1 <= best_lvl <= 6:
    #         _save_plus_dataset(roi, level=best_lvl, method="rgb_tm", debug_tag=tag)
    #     return best_lvl

    # ---------- PASS C: OCR (+0123456789) ----------
    if _HAVE_TESS:
        g  = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
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

    LOG.i("[ICON-PLUS] ไม่พบระดับ (cls/bank/mask/rgb/OCR ไม่ผ่าน)")
    if os.getenv("SAVE_ITEM_LV_SNAP", "1") != "0":
        _save_item_lv_snap(crop, roi, ymask, x0, y0, x0+rx0, y0+ry0, rw, rh, -1, "miss", tag)
    _save_plus_dataset(roi, level=None, method="miss", debug_tag=tag)  # เก็บไว้ให้คุณ label เอง
    return None

# ===================== Main step =====================
def worker_step(controller) -> Dict[str, Any]:
    """
    State machine (integrated with high-accuracy logic):
    - pick    : tap item
    - inspect : read level (icon top-right preferred) AFTER insert (อ่าน next-level แล้ว -1)
    - insert  : click 'insert' via CV
    - upgrade : tap upgrade button, detect overlay result
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
    _log_web(dev_id, f'stage=<b>{_html.escape(c["stage"])}</b> item_idx={c["item_idx"]} target={target}')

    # ---------- Stage: pick ----------
    if c["stage"] == "pick":
        if not items:
            LOG.w(f"[{dev_id}] ไม่พบ items ใน config")
            _log_web(dev_id, 'ไม่พบ items ใน config', "WARN")
            time.sleep(0.1)
            return {}

        idx = c["item_idx"] % len(items)
        ix, iy = items[idx]
        LOG.i(f"[{dev_id}] แตะไอเทม idx={idx} @({ix},{iy})")
        _log_web(dev_id, f'แตะไอเทม idx={idx} @({ix},{iy})')
        adb.tap(ix, iy)
        c["stage"] = "insert"
        c["last_action_ts"] = time.time()
        return {}

    # ---------- Stage: inspect (tap → insert → read level at fixed point) ----------
    if c["stage"] == "inspect":
        remain = 0.20 - (time.time() - c["last_action_ts"])
        if remain > 0:
            time.sleep(min(0.05, remain))
            return {}

        # ไอเทมปัจจุบัน
        idx = c["item_idx"] % len(items)

        # ให้ภาพนิ่งเล็กน้อย (ปรับได้ด้วย POST_INSERT_SETTLE, ดีฟอลต์ 0.35s)
        time.sleep(float(os.getenv("POST_INSERT_SETTLE", "0.35")))

        # อ่านระดับจาก "มุมขวาบนของช่อง" ที่พิกัดตายตัว (ค่าเริ่ม 960,323)
        fx = int(os.getenv("ICON_FIX_CX", "960"))
        fy = int(os.getenv("ICON_FIX_CY", "323"))
        lvl = _read_level_from_icon_topright(
            adb,
            (fx, fy),
            box_size=int(os.getenv("ICON_BOX_SIZE", "68")),
            debug_tag=f"{dev_id}_idx{idx}"
        )
        # next-level view → ต้อง -1; ถ้า None ให้เป็น 0
        if lvl is None:
            lvl = 0
        else:
            lvl = max(0, int(lvl) - 1)

        c["base_level"] = lvl if isinstance(lvl, int) else None

        LOG.i(f"[{dev_id}] หลัง INSERT อ่านระดับ level={lvl}")
        _log_web(dev_id, f'หลัง INSERT อ่านระดับ = {_lv_html(lvl, target)}')

        # เข้าสู่สเตจ 'upgrade'
        c["stage"] = "upgrade"
        c["successes"] = 0
        c["last_action_ts"] = time.time()
        return {}

    # ---------- Stage: insert (CV insert then wait 0.3s) ----------
    if c["stage"] == "insert":
        idx = c["item_idx"] % len(items)
        ix, iy = items[idx]
        LOG.i(f"[{dev_id}] INSERT: เตรียมกด 'ใส่ลง' ด้วย CV สำหรับ idx={idx} @({ix},{iy})")
        _log_web(dev_id, f'INSERT: เตรียมกด "ใส่ลง" idx={idx} @({ix},{iy})')

        LOG.i(f"[{dev_id}] INSERT: try CV insert in roi=({insert_roi[0]},{insert_roi[1]},{insert_roi[2]},{insert_roi[3]})")
        ok = _click_insert_via_cv(adb, insert_roi_rect=insert_roi, wait_pre=None)
        if not ok:
            sx, sy = _pt("slot_center", (0, 0))
            LOG.w(f"[{dev_id}] INSERT: CV not found → fallback slot_center @({sx},{sy})")
            _log_web(dev_id, f'INSERT: CV not found → fallback slot_center @({sx},{sy})', "WARN")
            # adb.tap(sx, sy)   # เผื่อจำเป็น

        LOG.i(f"[{dev_id}] INSERT: done → รอ 0.3s")
        _log_web(dev_id, 'INSERT: done → รอ 0.3s')
        time.sleep(0.3)

        c["stage"] = "inspect"
        c["last_action_ts"] = time.time()
        return {}

    # ---------- Stage: upgrade (loop tap -> overlay detect -> count successes) ----------
    # ensure defaults for flags
    c.setdefault("inflight", False)
    c.setdefault("inflight_ts", None)
    c.setdefault("unclear_n", 0)

    # tunables
    POLL_MAX = float(os.getenv("CONF_OVERLAY_POLL_MAX", "2.5"))          # วินาที รอผลสูงสุดของ inflight ก่อนยอมรี-tap
    UNCLEAR_GRACE = int(os.getenv("CONF_OVERLAY_UNCLEAR_GRACE", "4"))    # อนุโลมจำนวนครั้ง "unclear" ก่อนรี-tap
    ICON_LVL_OFFSET = int(os.getenv("ICON_LVL_OFFSET", "-1"))            # UI บางเกมโชว์ next-level → ชดเชย -1
    ICON_SUCCESS_CLAMP_INC = int(os.getenv("ICON_SUCCESS_CLAMP_INC", "1"))  # จำกัดจำนวนขั้นที่เพิ่มจาก icon (1=เพิ่มครั้งละ 1)
    fx = int(os.getenv("ICON_FIX_CX", "960"))
    fy = int(os.getenv("ICON_FIX_CY", "323"))
    icon_box = int(os.getenv("ICON_BOX_SIZE", "68"))

    if c["stage"] == "upgrade":
        base = c["base_level"] or 0
        cur = base + c["successes"]
        LOG.i(f"[{dev_id}] UPGRADE base={base} succ={c['successes']} cur={cur} / target=+{target}")
        _log_web(dev_id, f'UPGRADE: curr={_lv_html(cur, target)} / target={target}')

        # -------- Stop conditions (ก่อนคลิก) --------
        if cur >= target:
            LOG.i(f"[{dev_id}] บรรลุเป้าหมาย +{target} → นับชิ้นสำเร็จ 1 ชิ้น และไปชิ้นถัดไป")
            _log_web(dev_id, f'เสร็จสิ้นชิ้นนี้: curr={_lv_html(cur, target)} / target={target} ✅')
            idx = c.get("item_idx", 0) % max(1, len(items))
            lvl = _read_level_from_icon_topright(
                adb, (fx, fy),
                box_size=icon_box,
                debug_tag=f"skip_{dev_id}_{idx}"
            )
            # ไม่ว่าบางครั้ง lvl จะ None หรือไม่ เราไปชิ้นถัดไปทันที (กัน overshoot)
            _next_item(dev_id, adb, items, swipe_cfg, c)
            # reset inflight state when leaving item
            c["inflight"] = False
            c["inflight_ts"] = None
            c["unclear_n"] = 0
            return {"done_item": True}

        # -------- Pre-check: slot ยังอยู่ไหม --------
        if _is_slot_empty(adb, _pt("slot_center", (0, 0)), _size2("slot_roi", (80, 80))):
            LOG.i(f"[{dev_id}] ช่องว่าง (ไอเทมหาย/แตก) → ข้ามชิ้นนี้")
            _log_web(dev_id, 'ช่องว่าง (ไอเทมหาย/แตก) → ข้ามชิ้นนี้', "WARN")
            _next_item(dev_id, adb, items, swipe_cfg, c)
            c["inflight"] = False
            c["inflight_ts"] = None
            c["unclear_n"] = 0
            return {"break_at_level": cur}

        overlay_abs = _rect("overlay_abs", (0, 0, 0, 0))

        # -------- helper: resolve success/fail verdict --------
        def _apply_verdict(v: str) -> Dict:
            nonlocal base, cur
            if v == "success":
                c["successes"] += 1
                new_cur = (c["base_level"] or 0) + c["successes"]
                out["success_clicks"] = out.get("success_clicks", 0) + 1
                LOG.i(f"[{dev_id}] ผล: สำเร็จ (+1) → success={c['successes']}")
                _log_web(dev_id, f'ผล: <b>สำเร็จ</b> → curr={_lv_html(new_cur, target)} / target={target}')
                if new_cur >= target:
                    LOG.i(f"[{dev_id}] บรรลุเป้าหมาย +{target} → ไปชิ้นถัดไป (instant)")
                    _log_web(dev_id, f'เสร็จสิ้นชิ้นนี้: curr={_lv_html(new_cur, target)} / target={target} ✅')
                    _next_item(dev_id, adb, items, swipe_cfg, c)
                    c["inflight"] = False
                    c["inflight_ts"] = None
                    c["unclear_n"] = 0
                    out["done_item"] = True
                return out
            else:
                LOG.i(f"[{dev_id}] ผล: ล้มเหลว → พยายามต่อจนถึง target={target}")
                _log_web(dev_id, f'ผล: <b style="color:#e53935">ล้มเหลว</b> → curr={_lv_html(cur, target)} / target={target}', "WARN")
                return out

        # -------- inflight branch: มีคลิกค้างรอผลอยู่ --------
        if c.get("inflight"):
            verdict = _overlay_detect(adb, overlay_abs)
            if verdict in ("success", "fail"):
                c["inflight"] = False
                c["inflight_ts"] = None
                c["unclear_n"] = 0
                return _apply_verdict(verdict)

            # poll ภายในรอบนี้ก่อน
            t0 = time.time()
            got = None
            while time.time() - t0 < 1.5:
                time.sleep(0.3)
                chk = _overlay_detect(adb, overlay_abs)
                if chk in ("success", "fail"):
                    got = chk
                    break
            if got in ("success", "fail"):
                c["inflight"] = False
                c["inflight_ts"] = None
                c["unclear_n"] = 0
                return _apply_verdict(got)

            # icon fallback: overlay ยังไม่ชัด → ลองอ่านระดับจากมุมขวาบน
            try:
                idx = c.get("item_idx", 0) % max(1, len(items))
                raw_lvl = _read_level_from_icon_topright(
                    adb, (fx, fy),
                    box_size=icon_box,
                    debug_tag=f"{dev_id}_idx{idx}"
                )
                if raw_lvl is not None:
                    read_lvl = max(0, int(raw_lvl) + ICON_LVL_OFFSET)
                    cur = (c["base_level"] or 0) + c["successes"]
                    if read_lvl > cur:
                        inc = read_lvl - cur
                        gain = inc if ICON_SUCCESS_CLAMP_INC <= 0 else min(inc, ICON_SUCCESS_CLAMP_INC)
                        c["successes"] += gain
                        new_cur = (c["base_level"] or 0) + c["successes"]
                        out["success_clicks"] = out.get("success_clicks", 0) + 1
                        LOG.i(f"[{dev_id}] (icon-fallback) สำเร็จ → raw={raw_lvl} off={ICON_LVL_OFFSET} read={read_lvl} gain=+{gain}")
                        _log_web(dev_id, f'(icon) <b>สำเร็จ</b> → curr={_lv_html(new_cur, target)} / target={target}')
                        c["inflight"] = False
                        c["inflight_ts"] = None
                        c["unclear_n"] = 0
                        if new_cur >= target:
                            LOG.i(f"[{dev_id}] (icon-fallback) บรรลุเป้าหมาย +{target} → ไปชิ้นถัดไป (instant)")
                            _log_web(dev_id, f'เสร็จสิ้นชิ้นนี้: curr={_lv_html(new_cur, target)} / target={target} ✅')
                            _next_item(dev_id, adb, items, swipe_cfg, c)
                            out["done_item"] = True
                        return out
                    else:
                        LOG.i(f"[{dev_id}] (icon-fallback) read_lvl={read_lvl} ≤ cur={cur} → ยังไม่ฟันธง")
            except Exception as e:
                LOG.w(f"[{dev_id}] icon-fallback error: {e}")

            # timeout/grace → เคลียร์ inflight เพื่ออนุญาตให้คลิกรอบใหม่
            c["unclear_n"] = c.get("unclear_n", 0) + 1
            waited = time.time() - (c.get("inflight_ts") or time.time())
            LOG.i(f"[{dev_id}] overlay ยังไม่ชัดเจน (inflight) → unclear_n={c['unclear_n']} waited={waited:.2f}s")
            _log_web(dev_id, f'overlay ยังไม่ชัดเจน (inflight) → n={c["unclear_n"]} t={waited:.1f}s', "WARN")
            if waited >= POLL_MAX or c["unclear_n"] >= UNCLEAR_GRACE:
                c["inflight"] = False
                c["inflight_ts"] = None
                c["unclear_n"] = 0
                LOG.w(f"[{dev_id}] overlay ไม่ชัดนานเกิน → เคลียร์ inflight เพื่อรี-tap")
                _log_web(dev_id, 'overlay ไม่ชัดนานเกิน → จะลองคลิกใหม่ในรอบถัดไป', "WARN")
            return out

        # -------- ไม่มี inflight → คลิกใหม่ได้ --------
        ux, uy = _pt("upgrade_btn", (0, 0))
        LOG.i(f"[{dev_id}] UPGRADE: click upgrade_btn @({ux},{uy})")
        _log_web(dev_id, f'UPGRADE: click upgrade_btn @({ux},{uy})')
        adb.tap(ux, uy)
        out["upgrade_clicks"] = out.get("upgrade_clicks", 0) + 1

        # set inflight for this click
        c["inflight"] = True
        c["inflight_ts"] = time.time()
        c["unclear_n"] = 0

        time.sleep(_POST_UPGRADE_WAIT)

        # ลองอ่านผลทันที
        verdict = _overlay_detect(adb, overlay_abs)
        if verdict in ("success", "fail"):
            c["inflight"] = False
            c["inflight_ts"] = None
            c["unclear_n"] = 0
            return _apply_verdict(verdict)

        # poll ดีเลย์สั้น ๆ
        t0 = time.time()
        got = None
        while time.time() - t0 < 1.5:
            time.sleep(0.3)
            chk = _overlay_detect(adb, overlay_abs)
            if chk in ("success", "fail"):
                got = chk
                break
        if got in ("success", "fail"):
            c["inflight"] = False
            c["inflight_ts"] = None
            c["unclear_n"] = 0
            return _apply_verdict(got)

        # icon fallback หลัง poll แล้วไม่ชัด
        try:
            idx = c.get("item_idx", 0) % max(1, len(items))
            raw_lvl = _read_level_from_icon_topright(
                adb, (fx, fy),
                box_size=icon_box,
                debug_tag=f"{dev_id}_idx{idx}"
            )
            if raw_lvl is not None:
                read_lvl = max(0, int(raw_lvl) + ICON_LVL_OFFSET)
                cur = (c["base_level"] or 0) + c["successes"]
                if read_lvl > cur:
                    inc = read_lvl - cur
                    gain = inc if ICON_SUCCESS_CLAMP_INC <= 0 else min(inc, ICON_SUCCESS_CLAMP_INC)
                    c["successes"] += gain
                    new_cur = (c["base_level"] or 0) + c["successes"]
                    out["success_clicks"] = out.get("success_clicks", 0) + 1
                    LOG.i(f"[{dev_id}] (icon-fallback) สำเร็จหลังคลิก → raw={raw_lvl} off={ICON_LVL_OFFSET} read={read_lvl} gain=+{gain}")
                    _log_web(dev_id, f'(icon) <b>สำเร็จ</b> → curr={_lv_html(new_cur, target)} / target={target}')
                    c["inflight"] = False
                    c["inflight_ts"] = None
                    c["unclear_n"] = 0
                    if new_cur >= target:
                        LOG.i(f"[{dev_id}] (icon-fallback) บรรลุเป้าหมาย +{target} → ไปชิ้นถัดไป (instant)")
                        _log_web(dev_id, f'เสร็จสิ้นชิ้นนี้: curr={_lv_html(new_cur, target)} / target={target} ✅')
                        _next_item(dev_id, adb, items, swipe_cfg, c)
                        out["done_item"] = True
                    return out
                else:
                    LOG.i(f"[{dev_id}] (icon-fallback) read_lvl={read_lvl} ≤ cur={cur} → ยังไม่ฟันธง")
        except Exception as e:
            LOG.w(f"[{dev_id}] icon-fallback error: {e}")

        # ถึงตรงนี้ยังไม่ชัด → ปล่อย inflight ค้างไว้ให้รอบถัดไปตัดสินใจตาม timeout/grace
        LOG.i(f"[{dev_id}] overlay ยังไม่ชัดเจน → จะลองต่อในรอบถัดไป (inflight=True)")
        _log_web(dev_id, 'overlay ยังไม่ชัดเจน → จะลองต่อในรอบถัดไป', "WARN")
        return out

# ===================== Loop wrapper =====================
def worker_loop(ctrl, step_fn, sleep_sec: float = 0.15):
    LOG.i(f"[{ctrl.id}] loop start")

    # โหลด template bank และ classifier จาก dataset หนึ่งครั้งก่อนเข้าลูป
    global _PLUS_TPL_BANK, _PLUS_CLS_CENTROIDS
    if not _PLUS_TPL_BANK:
        _PLUS_TPL_BANK = _load_plus_template_bank()
    if not _PLUS_CLS_CENTROIDS:
        _PLUS_CLS_CENTROIDS = _build_plus_classifier()

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
                _log_web(ctrl.id, f'step error: {_html.escape(str(e))}', "ERROR")
                break

            # per-item timeout guard (optional)
            if _MAX_ITEM_TIME_SEC > 0 and (time.time() - t0_item) > _MAX_ITEM_TIME_SEC:
                LOG.i(f"[{ctrl.id}] item timeout {_MAX_ITEM_TIME_SEC}s → advance item")
                _log_web(ctrl.id, f'item timeout {_MAX_ITEM_TIME_SEC}s → advance item', "WARN")
                c = _ctx(ctrl.id)
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
                _log_web(ctrl.id, f'heartbeat items={getattr(ctrl,"items_upgraded_done",0)} target={getattr(ctrl,"target_level",0)}')

            time.sleep(sleep_sec)
    finally:
        LOG.i(f"[{ctrl.id}] loop exit (state={ctrl.state})")
        _log_web(ctrl.id, f'loop exit (state={_html.escape(str(ctrl.state))})')
