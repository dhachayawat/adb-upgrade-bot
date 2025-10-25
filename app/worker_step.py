# app/worker_step.py
from __future__ import annotations
from typing import Dict, Any, Optional, Tuple
import os
import re, time
import cv2
import numpy as np
import html as _html  # สำหรับสร้าง HTML ไปโชว์บนเว็บ
import json, glob
from datetime import datetime

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

# -------------------- Quick helpers --------------------
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

# ---- Utils for robust ndarray checks & image prep ----
def _truthy_img(arr) -> bool:
    return (arr is not None) and hasattr(arr, "size") and (arr.size > 0) and (getattr(arr, "shape", (0, 0))[0] > 0) and (getattr(arr, "shape", (0, 0))[1] > 0)

def _prep_gray_for_match(bgr_or_gray: np.ndarray) -> np.ndarray:
    g = bgr_or_gray if len(bgr_or_gray.shape) == 2 else cv2.cvtColor(bgr_or_gray, cv2.COLOR_BGR2GRAY)
    g = cv2.equalizeHist(g)
    g = cv2.GaussianBlur(g, (3, 3), 0)
    return g

# thresholds / timings (fallback to env vars if provided)
_CONF_INSERT_THR = float(_CFG.get("conf_insert_thr", os.getenv("CONF_INSERT_THR", "0.86")))
_MAX_ITEM_TIME_SEC = int(os.getenv("MAX_ITEM_TIME_SEC", "30"))

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

def _icon_roi_norm(adb, center_xy, box_size: int = 68) -> np.ndarray:
    """คืนภาพ ROI (gray) ของมุมขวาบนไอคอนแบบ normalize 32x28 เพื่อนำไปเปรียบเทียบ"""
    (cx, cy) = center_xy
    img = _grab(adb)
    crop, (x0, y0) = _crop_center(img, cx, cy, box_size, box_size)

    h, w = crop.shape[:2]
    rx0 = int(w * float(os.getenv("ICON_ROI_XRATIO", "0.55")))
    ry0 = 0
    rw  = w - rx0
    rh  = int(h * float(os.getenv("ICON_ROI_HRATIO", "0.50")))
    roi = crop[ry0:ry0+rh, rx0:rx0+rw].copy()

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    norm = cv2.resize(gray, (32, 28), interpolation=cv2.INTER_AREA)
    return norm

# ======== Snapshot for icon-level reading ========
def _save_item_lv_snap(crop, roi, thr, x0, y0, x1, y1, w, h, level, method, debug_tag: str):
    try:
        dbgdir = _CFG.get("debug_dir", "/app/cache/debug")
        outdir = os.path.join(dbgdir, "item_lv")
        os.makedirs(outdir, exist_ok=True)
        ts = int(time.time() * 1000)
        cv2.imwrite(os.path.join(outdir, f"{debug_tag}_{ts}_crop_{method}_lv{level}.png"), crop)
        cv2.imwrite(os.path.join(outdir, f"{debug_tag}_{ts}_roi_{method}_lv{level}.png"), roi)
        if _truthy_img(thr):
            cv2.imwrite(os.path.join(outdir, f"{debug_tag}_{ts}_thr_{method}_lv{level}.png"), thr)
        vis = crop.copy()
        cv2.rectangle(vis, (x1 - x0, y1 - y0), (x1 - x0 + w, y1 - y0 + h), (0, 255, 0), 2)
        cv2.imwrite(os.path.join(outdir, f"{debug_tag}_{ts}_box_{method}_lv{level}.png"), vis)
    except Exception as e:
        LOG.w(f"[item_lv_snap] save fail: {e}")

# ======== Dataset helpers (icon + overlay) ========
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

# ====== ตัวช่วยบันทึก overlay dataset ======
_OVERLAY_DATASET_DIR = os.getenv("OVERLAY_DATASET_DIR", "/app/data/dataset/overlay")
_OVERLAY_SNAP = os.getenv("OVERLAY_SNAP", "1") == "1"
_OVERLAY_SNAP_UNLABELED = os.getenv("OVERLAY_SNAP_UNLABELED", "1") == "1"

_SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9_.-]+")

def _safe_name(s: str, maxlen: int = 40) -> str:
    return _SAFE_NAME_RE.sub("-", str(s))[:maxlen].strip("-") or "x"

def _ensure_dir(p: str):
    try:
        os.makedirs(p, exist_ok=True)
    except Exception:
        pass

# ---- CONFIG (overlay matcher) ----
_DIR_SUCCESS = os.path.join(_OVERLAY_DATASET_DIR, "state_success")
_DIR_FAIL    = os.path.join(_OVERLAY_DATASET_DIR, "state_fail")
_DIR_UNLBL   = os.path.join(_OVERLAY_DATASET_DIR, "state_unlabeled")

_W_PHASH = float(os.getenv("OVERLAY_W_PHASH", "0.45"))
_W_ORB   = float(os.getenv("OVERLAY_W_ORB",   "0.55"))

_PHASH_MAX_DIST = int(os.getenv("OVERLAY_MATCH_PHASH_MAX_DIST", "16"))
_ORB_GOOD_RATIO_THR = float(os.getenv("OVERLAY_MATCH_ORB_GOOD_RATIO_THR", "0.12"))
_ORB_MIN_GOOD       = int(os.getenv("OVERLAY_MATCH_ORB_MIN_GOOD", "18"))

_MATCH_SCORE_THR = float(os.getenv("OVERLAY_MATCH_SCORE_THR", "0.58"))

_SIZE_TOL_RATIO = float(os.getenv("OVERLAY_SIZE_TOL_RATIO", "0.35"))

# Motion-driven snap defaults (can be overridden via ENV)
_OVERLAY_MIN_WAIT = float(os.getenv("OVERLAY_MIN_WAIT", "0.20"))
_OVERLAY_MAX_WAIT = float(os.getenv("OVERLAY_MAX_WAIT", "0.80"))
_OVERLAY_POLL_INT = float(os.getenv("OVERLAY_POLL_INTERVAL", "0.12"))

for _d in (_DIR_SUCCESS, _DIR_FAIL, _DIR_UNLBL):
    _ensure_dir(_d)

# ---- pHash helpers ----
def _phash(gray32):
    g = cv2.resize(gray32, (32, 32), interpolation=cv2.INTER_AREA)
    g = np.float32(g)
    dct = cv2.dct(g)
    dct_low = dct[:8, :8]
    med = np.median(dct_low)
    bits = (dct_low > med).astype(np.uint8).flatten()
    v = 0
    for b in bits:
        v = (v << 1) | int(b)
    return np.uint64(v)

def _phash_dist(a: np.uint64, b: np.uint64):
    x = int(a ^ b)
    return bin(x).count("1")

def _phash_sim(dist: int, maxd: int = _PHASH_MAX_DIST):
    d = float(dist)
    if d >= maxd:
        return 0.0
    return max(0.0, 1.0 - (d / maxd))

# ---- ORB helpers ----
_ORB = cv2.ORB_create(nfeatures=600, scaleFactor=1.2, nlevels=8, edgeThreshold=15, patchSize=31, fastThreshold=12)
_BFM = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)

def _orb_desc(gray):
    kp = _ORB.detect(gray, None)
    kp, des = _ORB.compute(gray, kp)
    return kp or [], des

def _orb_good_ratio(des1, des2):
    if des1 is None or des2 is None or len(des1) == 0 or len(des2) == 0:
        return 0.0, 0
    matches = _BFM.knnMatch(des1, des2, k=2)
    good = []
    for m in matches:
        if len(m) == 2 and m[0].distance < 0.75 * m[1].distance:
            good.append(m[0])
    good_n = len(good)
    all_n = len(matches)
    ratio = float(good_n) / float(max(1, all_n))
    return ratio, good_n

# ---- Dataset index (lazy cache + auto refresh by mtime) ----
class _OverlayIndex:
    def __init__(self):
        self.items = []  # list of dict(label,path, w,h, phash, des)
        self._last_scan = 0.0
        self._last_mtime = 0.0

    def _dir_latest_mtime(self):
        mt = 0.0
        for d in (_DIR_SUCCESS, _DIR_FAIL):
            for p in glob.glob(os.path.join(d, "*.png")) + glob.glob(os.path.join(d, "*.jpg")) + glob.glob(os.path.join(d, "*.jpeg")):
                try:
                    mt = max(mt, os.path.getmtime(p))
                except Exception:
                    pass
        return mt

    def refresh_if_needed(self, force=False):
        now = time.time()
        if (not force) and (now - self._last_scan < 5.0):
            return
        current_mtime = self._dir_latest_mtime()
        if (not force) and (current_mtime <= self._last_mtime) and self.items:
            self._last_scan = now
            return
        # rebuild
        items = []
        for label, d in (("success", _DIR_SUCCESS), ("fail", _DIR_FAIL)):
            for p in glob.glob(os.path.join(d, "*.png")) + glob.glob(os.path.join(d, "*.jpg")) + glob.glob(os.path.join(d, "*.jpeg")):
                try:
                    raw = np.fromfile(p, dtype=np.uint8)
                    img = cv2.imdecode(raw, cv2.IMREAD_GRAYSCALE)
                    if img is None:
                        continue
                    img = _prep_gray_for_match(img)   # <<< normalize
                    h, w = img.shape[:2]
                    ph = _phash(img)
                    _, des = _orb_desc(img)
                    items.append(dict(label=label, path=p, w=w, h=h, phash=ph, des=des))
                except Exception:
                    continue
        self.items = items
        self._last_scan = now
        self._last_mtime = current_mtime
        LOG.i(f"[overlay-index] indexed samples: {len(self.items)} (success={sum(1 for x in items if x['label']=='success')}, fail={sum(1 for x in items if x['label']=='fail')})")

_OVERLAY_INDEX = _OverlayIndex()

def rebuild_overlay_index():
    """ เรียกใช้เมื่อต้องการบังคับ reindex ด้วยตนเอง """
    _OVERLAY_INDEX.refresh_if_needed(force=True)

def _save_overlay_sample(dev_id: str, overlay_rect, roi_bgr, verdict: str, best_dict: dict, tag: str = ""):
    try:
        if not _OVERLAY_SNAP:
            LOG.i("[overlay-snap] disabled by OVERLAY_SNAP=0")
            return

        label = "state_unlabeled" if verdict not in ("success", "fail") else ("state_success" if verdict == "success" else "state_fail")
        base_dir = os.path.join(_OVERLAY_DATASET_DIR, label)
        _ensure_dir(_OVERLAY_DATASET_DIR)
        _ensure_dir(base_dir)

        ts  = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        dev = _safe_name(dev_id or "dev")  # sanitize เพื่อกัน ?, :, ฯลฯ
        tgg = _safe_name(tag or "snap")
        fname = f"{ts}_{dev}_{tgg}_{(verdict or 'none')}"
        img_path  = os.path.join(base_dir, fname + ".png")
        meta_path = os.path.join(base_dir, fname + ".json")

        if not _truthy_img(roi_bgr):
            LOG.w(f"[overlay-snap] skip write: empty ROI (path={img_path})")
            meta = dict(
                ts=ts, dev_id=dev_id, verdict=verdict, tag=tag,
                reason="empty_roi", overlay_rect=(list(map(int, overlay_rect)) if overlay_rect else None),
                roi_shape=None, match=best_dict or {}
            )
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
            return

        ok = False
        try:
            ok = bool(cv2.imwrite(img_path, roi_bgr))
        except Exception as e:
            LOG.w(f"[overlay-snap] imwrite exception: {e}")

        if not ok:
            try:
                ok2, buf = cv2.imencode(".png", roi_bgr)
                if ok2:
                    buf.tofile(img_path)
                    ok = True
            except Exception as e:
                LOG.w(f"[overlay-snap] imencode/tofile exception: {e}")

        if not ok:
            LOG.w(f"[overlay-snap] write failed: {img_path}")
        else:
            LOG.i(f"[overlay-snap] wrote: {img_path}")

        meta = dict(
            ts=ts, dev_id=dev_id, verdict=verdict, tag=tag,
            overlay_rect=(list(map(int, overlay_rect)) if overlay_rect else None),
            roi_shape=(int(roi_bgr.shape[0]), int(roi_bgr.shape[1])),
            match=best_dict or {},
            dataset_dir=_OVERLAY_DATASET_DIR
        )
        try:
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
        except Exception as e:
            LOG.w(f"[overlay-snap] write meta error: {e} (meta_path={meta_path})")

    except Exception as e:
        LOG.w(f"[overlay-snap] unexpected error: {e}")

def _overlay_detect(adb: ADBAdapter, overlay_rect: tuple) -> str | None:
    """
    Dataset-only + Motion-driven snap:
      - เฝ้าดู motion เพื่อจับช่วงแอนิเมชัน success/fail
      - รอหลังพบ motion ช่วงสั้น ๆ เพื่อให้เฟรมนิ่งขึ้น แล้วค่อยเลือกเฟรมที่คมที่สุดไปเทียบกับ dataset
      - ถ้าไม่มี motion เลยภายในเวลารวม → snap unlabeled เพื่อรอ label
    """
    # ======= Tunables (ENV) =======
    POLL_INT          = float(os.getenv("OVERLAY_POLL_INTERVAL",       "0.04"))
    MAX_TOTAL_WAIT    = float(os.getenv("OVERLAY_MAX_TOTAL_WAIT",      "1.80"))
    ARM_MOTION_THR    = float(os.getenv("OVERLAY_ARM_MOTION_THR",      "0.020"))
    SETTLE_MOTION_THR = float(os.getenv("OVERLAY_SETTLE_MOTION_THR",   "0.008"))
    POST_MOTION_WAIT  = float(os.getenv("OVERLAY_POST_MOTION_WAIT",    "0.28"))
    NO_MOTION_EXTRA   = float(os.getenv("OVERLAY_NO_MOTION_EXTRA",     "0.35"))

    # ======= Capture loop =======
    t0 = time.time()
    prev_gray = None
    armed_ts = None
    best_roi = None
    best_focus = -1.0
    last_roi = None

    def _grab_roi():
        img = _grab(adb)
        roi_bgr, _ = _crop_rect(img, overlay_rect)
        return roi_bgr

    def _motion(prev_g, cur_bgr):
        g = cv2.cvtColor(cur_bgr, cv2.COLOR_BGR2GRAY)
        if prev_g is None:
            return g, 0.0
        diff = cv2.absdiff(g, prev_g)
        return g, float(np.mean(diff)) / 255.0

    def _focus_score(gray):
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())

    while time.time() - t0 < MAX_TOTAL_WAIT:
        time.sleep(POLL_INT)
        roi = _grab_roi()
        if _truthy_img(roi):
            last_roi = roi
        else:
            continue

        prev_gray, mot = _motion(prev_gray, roi)
        g = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        foc = _focus_score(g)

        if armed_ts is None:
            if mot >= ARM_MOTION_THR:
                armed_ts = time.time()
                best_roi, best_focus = roi, foc
                LOG.i(f"[overlay/anim] armed: mot={mot:.3f}")
        else:
            if foc > best_focus:
                best_roi, best_focus = roi, foc
            if (time.time() - armed_ts) >= POST_MOTION_WAIT and mot <= SETTLE_MOTION_THR:
                break

    snap_roi = best_roi if _truthy_img(best_roi) else last_roi
    if not _truthy_img(snap_roi):
        LOG.w("[overlay] ROI empty → cannot snap")
        return None

    # ======= Dataset matching =======
    roi_gray = _prep_gray_for_match(snap_roi)
    h, w = roi_gray.shape[:2]
    ph_roi = _phash(roi_gray)
    _, des_roi = _orb_desc(roi_gray)

    _OVERLAY_INDEX.refresh_if_needed()

    if not _OVERLAY_INDEX.items:
        LOG.w("[overlay] dataset empty → snap as unlabeled")
        _save_overlay_sample(getattr(adb, "dev_id", "?"), overlay_rect, snap_roi,
                             None, dict(reason="dataset_empty", armed=bool(armed_ts)), tag="need_label")
        return None

    best = dict(score=0.0, label=None, phash_sim=0.0, orb_ratio=0.0, orb_good=0, ref_path=None, ref_w=0, ref_h=0)
    for it in _OVERLAY_INDEX.items:
        size_penalty = 0.0
        if max(abs(it["w"] - w)/max(1,w), abs(it["h"] - h)/max(1,h)) > _SIZE_TOL_RATIO:
            size_penalty = 0.08

        dist = _phash_dist(ph_roi, it["phash"])
        sim_p = _phash_sim(dist, _PHASH_MAX_DIST)
        r_orb, n_good = _orb_good_ratio(des_roi, it["des"])
        orb_ok = (r_orb >= _ORB_GOOD_RATIO_THR) and (n_good >= _ORB_MIN_GOOD)

        score = (_W_PHASH * sim_p) + (_W_ORB * (r_orb if orb_ok else 0.0))
        score = max(0.0, score - size_penalty)

        if score > best["score"]:
            best.update(score=float(score), label=it["label"], phash_sim=float(sim_p),
                        orb_ratio=float(r_orb), orb_good=int(n_good),
                        ref_path=it["path"], ref_w=int(it["w"]), ref_h=int(it["h"]))

    LOG.i(f"[overlay/dataset] best={best['label']} score={best['score']:.3f} (pH={best['phash_sim']:.3f}, orb={best['orb_ratio']:.3f}/{best['orb_good']}) ref={best['ref_path']} armed={bool(armed_ts)}")

    if best["score"] >= _MATCH_SCORE_THR and best["label"] in ("success", "fail"):
        _save_overlay_sample(getattr(adb, "dev_id", "?"), overlay_rect, snap_roi,
                             "success" if best["label"] == "success" else "fail",
                             dict(best=best, armed=bool(armed_ts)), tag="anim_peak")
        return "success" if best["label"] == "success" else "fail"

    # ยังไม่มั่นใจ → เก็บ unlabeled; ถ้ายังไม่เคย arm ให้ยืดรออีกนิด
    if armed_ts is None and (time.time() - t0) < (MAX_TOTAL_WAIT + NO_MOTION_EXTRA):
        end = time.time() + NO_MOTION_EXTRA
        while time.time() < end:
            time.sleep(POLL_INT)
            nxt = _grab_roi()
            if _truthy_img(nxt):
                snap_roi = nxt

    _save_overlay_sample(getattr(adb, "dev_id", "?"), overlay_rect, snap_roi,
                         None, dict(best=best, armed=bool(armed_ts)), tag="need_label")
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
            time.sleep(0.025)
            adb.swipe(x, y, dy=dy, ms=ms)
            time.sleep(0.025)
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
      PASS 0 : dataset classifier (centroid cosine)
      PASS A1: dataset bank บน yellow-mask
      PASS B1: dataset bank บน gray ROI
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

    ymask = _yellow_digit_mask(roi)
    ymask_norm = _norm_roi_size(ymask)
    roi_gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    roi_gray_norm = _norm_roi_size(roi_gray)

    if force:
        _save_plus_dataset(roi, level=None, method="cls", debug_tag=tag) #force save
        return None

    # ---------- PASS 0: dataset classifier ----------
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
    _save_plus_dataset(roi, level=None, method="miss", debug_tag=tag)
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
            time.sleep(0.01)
            return {}

        idx = c["item_idx"] % len(items)
        ix, iy = items[idx]
        LOG.i(f"[{dev_id}] แตะไอเทม idx={idx} @({ix},{iy})")
        _log_web(dev_id, f'แตะไอเทม idx={idx} @({ix},{iy})')
        adb.tap(ix, iy)
        c["stage"] = "insert"
        c["last_action_ts"] = time.time()
        return {}

    # ---------- Stage: inspect ----------
    if c["stage"] == "inspect":
        remain = 0.20 - (time.time() - c["last_action_ts"])
        if remain > 0:
            return {}

        idx = c["item_idx"] % len(items)

        fx = int(os.getenv("ICON_FIX_CX", "960"))
        fy = int(os.getenv("ICON_FIX_CY", "323"))
        icon_box = int(os.getenv("ICON_BOX_SIZE", "68"))

        ICON_LVL_OFFSET = int(os.getenv("ICON_LVL_OFFSET", "-1"))
        lvl_raw = _read_level_from_icon_topright(
            adb, (fx, fy),
            box_size=icon_box,
            debug_tag=f"{dev_id}_idx{idx}"
        )
        if lvl_raw is None:
            lvl = 0
        else:
            lvl = max(0, int(lvl_raw) + ICON_LVL_OFFSET)

        c["base_level"] = int(lvl)
        try:
            c["icon_roi_prev"] = _icon_roi_norm(adb, (fx, fy), icon_box)
        except Exception as e:
            c["icon_roi_prev"] = None
            LOG.w(f"[{dev_id}] snapshot ROI fail: {e}")

        LOG.i(f"[{dev_id}] หลัง INSERT อ่านระดับ (base) level={lvl}")
        _log_web(dev_id, f'หลัง INSERT อ่านระดับ (base) = {_lv_html(lvl, target)}')

        c["stage"] = "upgrade"
        c["successes"] = 0
        c["last_action_ts"] = time.time()
        return {}

    # ---------- Stage: insert ----------
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

        LOG.i(f"[{dev_id}] INSERT: done → รอ 0.3s")
        _log_web(dev_id, 'INSERT: done → รอ 0.3s')
        time.sleep(0.03)

        c["stage"] = "inspect"
        c["last_action_ts"] = time.time()
        return {}

    # Ensure defaults for per-item state
    c.setdefault("inflight", False)
    c.setdefault("inflight_ts", None)
    c.setdefault("unclear_n", 0)
    c.setdefault("last_action_ts", 0.0)

    # ---- Tunables (env overridable) ----
    _POST_UPGRADE_WAIT = float(os.getenv("POST_UPGRADE_WAIT", "0.18"))
    OVERLAY_QUICK_POLL_BUDGET = float(os.getenv("OVERLAY_QUICK_POLL_BUDGET", "0.45"))
    OVERLAY_QUICK_POLL_STEP = float(os.getenv("OVERLAY_QUICK_POLL_STEP", "0.12"))
    POLL_MAX = float(os.getenv("CONF_OVERLAY_POLL_MAX", "0.60"))
    UNCLEAR_GRACE = int(os.getenv("CONF_OVERLAY_UNCLEAR_GRACE", "2"))
    MIN_TAP_INTERVAL = float(os.getenv("MIN_TAP_INTERVAL", "0.05"))

    ICON_LVL_OFFSET = int(os.getenv("ICON_LVL_OFFSET", "-1"))
    ICON_SUCCESS_CLAMP_INC = int(os.getenv("ICON_SUCCESS_CLAMP_INC", "1"))
    fx = int(os.getenv("ICON_FIX_CX", "960"))
    fy = int(os.getenv("ICON_FIX_CY", "323"))
    icon_box = int(os.getenv("ICON_BOX_SIZE", "68"))

    if c["stage"] == "upgrade":
        base = c.get("base_level", 0) or 0
        cur = base + c.get("successes", 0)
        LOG.i(f"[{dev_id}] UPGRADE base={base} succ={c['successes']} cur={cur} / target=+{target}")
        _log_web(dev_id, f'UPGRADE: curr={_lv_html(cur, target)} / target={target}')

        # -------- Stop conditions (ก่อนคลิก) --------
        if cur >= target:
            LOG.i(f"[{dev_id}] บรรลุเป้าหมาย +{target} → นับชิ้นสำเร็จ 1 ชิ้น และไปชิ้นถัดไป")
            _log_web(dev_id, f'เสร็จสิ้นชิ้นนี้: curr={_lv_html(cur, target)} / target={target} ✅')
            _next_item(dev_id, adb, items, swipe_cfg, c)
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

        # -------- helpers --------
        def _apply_success() -> dict:
            c["successes"] = c.get("successes", 0) + 1
            new_cur = (c.get("base_level", 0) or 0) + c["successes"]
            nonlocal out
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

        def _apply_verdict(v: str) -> dict:
            if v == "success":
                return _apply_success()
            LOG.i(f"[{dev_id}] ผล: ล้มเหลว → พยายามต่อจนถึง target={target}")
            _log_web(dev_id, f'ผล: <b style="color:#e53935">ล้มเหลว</b> → curr={_lv_html(cur, target)} / target={target}', "WARN")
            return out

        def _icon_fallback_maybe_success(debug_label: str) -> bool:
            try:
                idx = c.get("item_idx", 0) % max(1, len(items))
                raw_lvl = _read_level_from_icon_topright(
                    adb, (fx, fy),
                    box_size=icon_box,
                    debug_tag=f"{debug_label}_{dev_id}_idx{idx}"
                )
                if raw_lvl is None:
                    return False
                read_lvl = max(0, int(raw_lvl) + ICON_LVL_OFFSET)
                current = (c.get("base_level", 0) or 0) + c.get("successes", 0)
                if read_lvl > current:
                    inc = read_lvl - current
                    gain = inc if ICON_SUCCESS_CLAMP_INC <= 0 else min(inc, ICON_SUCCESS_CLAMP_INC)
                    c["successes"] = c.get("successes", 0) + gain
                    new_cur2 = (c.get("base_level", 0) or 0) + c["successes"]
                    nonlocal out
                    out["success_clicks"] = out.get("success_clicks", 0) + 1
                    LOG.i(f"[{dev_id}] (icon-fallback) success → raw={raw_lvl} off={ICON_LVL_OFFSET} read={read_lvl} gain=+{gain}")
                    _log_web(dev_id, f'(icon) <b>สำเร็จ</b> → curr={_lv_html(new_cur2, target)} / target={target}')
                    c["inflight"] = False
                    c["inflight_ts"] = None
                    c["unclear_n"] = 0
                    if new_cur2 >= target:
                        LOG.i(f"[{dev_id}] (icon-fallback) บรรลุเป้าหมาย +{target} → ไปชิ้นถัดไป (instant)")
                        _log_web(dev_id, f'เสร็จสิ้นชิ้นนี้: curr={_lv_html(new_cur2, target)} / target={target} ✅')
                        _next_item(dev_id, adb, items, swipe_cfg, c)
                        out["done_item"] = True
                    return True
                else:
                    LOG.i(f"[{dev_id}] (icon-fallback) read_lvl={read_lvl} ≤ cur={current} → ยังไม่ฟันธง")
                    return False
            except Exception as e:
                LOG.w(f"[{dev_id}] icon-fallback error: {e}")
                return False

        # -------- inflight branch --------
        if c.get("inflight"):
            verdict = _overlay_detect(adb, overlay_abs)
            if verdict in ("success", "fail"):
                c["inflight"] = False
                c["inflight_ts"] = None
                c["unclear_n"] = 0
                return _apply_verdict(verdict)

            # Quick poll within budget
            budget_t0 = time.time()
            got = None
            while (time.time() - budget_t0) < OVERLAY_QUICK_POLL_BUDGET:
                time.sleep(OVERLAY_QUICK_POLL_STEP)
                chk = _overlay_detect(adb, overlay_abs)
                if chk in ("success", "fail"):
                    got = chk
                    break
            if got in ("success", "fail"):
                c["inflight"] = False
                c["inflight_ts"] = None
                c["unclear_n"] = 0
                return _apply_verdict(got)

            # Try icon fallback quickly
            if _icon_fallback_maybe_success("inflight"):
                return out

            # Timeout / grace — allow re-tap soon
            c["unclear_n"] = c.get("unclear_n", 0) + 1
            waited = time.time() - (c.get("inflight_ts") or c.get("last_action_ts", time.time()))
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
        last = c.get("last_action_ts", 0.0)
        remain = MIN_TAP_INTERVAL - (time.time() - last)
        if remain > 0:
            time.sleep(min(0.05, remain))
        c["last_action_ts"] = time.time()

        ux, uy = _pt("upgrade_btn", (0, 0))
        LOG.i(f"[{dev_id}] UPGRADE: click upgrade_btn @({ux},{uy})")
        _log_web(dev_id, f'UPGRADE: click upgrade_btn @({ux},{uy})')
        adb.tap(ux, uy)
        out["upgrade_clicks"] = out.get("upgrade_clicks", 0) + 1

        # Set inflight for this click
        c["inflight"] = True
        c["inflight_ts"] = time.time()
        c["unclear_n"] = 0

        time.sleep(_POST_UPGRADE_WAIT)

        # Quick verdict
        verdict = _overlay_detect(adb, overlay_abs)
        if verdict in ("success", "fail"):
            c["inflight"] = False
            c["inflight_ts"] = None
            c["unclear_n"] = 0
            return _apply_verdict(verdict)

        # Quick poll within budget
        budget_t0 = time.time()
        got = None
        while (time.time() - budget_t0) < OVERLAY_QUICK_POLL_BUDGET:
            time.sleep(OVERLAY_QUICK_POLL_STEP)
            chk = _overlay_detect(adb, overlay_abs)
            if chk in ("success", "fail"):
                got = chk
                break
        if got in ("success", "fail"):
            c["inflight"] = False
            c["inflight_ts"] = None
            c["unclear_n"] = 0
            return _apply_verdict(got)

        # Icon fallback after quick poll
        if _icon_fallback_maybe_success("posttap"):
            return out

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
                time.sleep(0.02)
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
