# app/worker_step.py
from __future__ import annotations
from typing import Dict, Any, Optional, Tuple
import os
import re, time
import cv2
import numpy as np
import html as _html

from .core.adb_adapter import ADBAdapter
from .config_store import ConfigStore
from .summary_store import SummaryStore
from . import rtlog as LOG
from . import cv_utils as CV

# ===================== Global config =====================
_CFG = ConfigStore().load_defaults()
_SUMMARY: Optional[SummaryStore] = None
_DEVICE_CTX: Dict[str, dict] = {}  # per-device state
_PLUS_RE = re.compile(r"\+?\s*(\d{1,2})")
_PLUS_ONE_DIGIT_RE = re.compile(r"\+?\s*([1-9])")

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

# thresholds / timings
_CONF_INSERT_THR     = float(_CFG.get("conf_insert_thr", os.getenv("CONF_INSERT_THR", "0.86")))
_MAX_ITEM_TIME_SEC   = int(os.getenv("MAX_ITEM_TIME_SEC", "30"))

# ===================== Web UI log helpers =====================
def _lv_color(lv: Optional[int], target: int) -> str:
    if lv is None: return "#9e9e9e"
    if lv >= target: return "#43a047"
    if lv >= (target - 1): return "#fb8c00"
    return "#e53935"

def _lv_html(lv: Optional[int], target: int) -> str:
    txt = "none" if lv is None else str(lv)
    col = _lv_color(lv, target)
    return f'<span class="adb-lv" style="color:{col};font-weight:600">{_html.escape(txt)}</span>'

def _log_web(dev_id: str, html_msg: str, level: str = "INFO"):
    """
    เขียน rich-log ต่อ device โดยใช้ mp_web เพื่อให้โปรเซสลูกส่งกลับมาที่โปรเซสหลัก
    (mp_web จะ fallback เป็น web ถ้าไม่มีคิวแนบไว้)
    """
    try:
        from .rtlog import mp_web as _rtlog_mp_web
        _rtlog_mp_web(dev_id, html_msg, level.upper())
    except Exception:
        # ตกมา plain log ต่อ-device กันหาย
        LOG.dev_info(dev_id or "global", f"[WEB-LOG fallback] {html_msg}")


# ===================== OCR / Image helpers =====================
try:
    import pytesseract
    _HAVE_TESS = True
except Exception:
    _HAVE_TESS = False

def _grab(adb: ADBAdapter) -> np.ndarray:
    return adb.screencap()

def _crop_center(img: np.ndarray, cx: int, cy: int, w: int, h: int) -> Tuple[np.ndarray, Tuple[int, int]]:
    x1 = max(0, cx - w // 2); y1 = max(0, cy - h // 2)
    x2 = min(img.shape[1], x1 + w); y2 = min(img.shape[0], y1 + h)
    return img[y1:y2, x1:x2].copy(), (x1, y1)

def _yellow_digit_mask(bgr: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h1 = int(os.getenv("Y_H1", "18"))
    h2 = int(os.getenv("Y_H2", "45"))
    s  = int(os.getenv("Y_S_MIN", "120"))
    v  = int(os.getenv("Y_V_MIN", "140"))
    mask = cv2.inRange(hsv, (h1, s, v), (h2, 255, 255))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2,2), np.uint8), iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((2,2), np.uint8), iterations=1)
    return mask

def _ocr_text(gray: np.ndarray, psm: int = 7) -> str:
    if not _HAVE_TESS:
        return ""
    cfg = f"--psm {psm}"
    txt = pytesseract.image_to_string(gray, lang="eng", config=cfg)
    return (txt or "").strip().replace("\n", " ")

# ======== Insert button via CV ========
def _click_insert_via_cv(adb: ADBAdapter, insert_roi_rect: Tuple[int, int, int, int], wait_pre: Optional[float] = None) -> bool:
    if wait_pre is None:
        wait_pre = float(os.getenv("PRE_INSERT_DELAY", "0.35"))
    time.sleep(wait_pre)
    img = _grab(adb)
    x1, y1, w, h = insert_roi_rect
    if w > 0 and h > 0:
        roi = img[y1:y1+h, x1:x1+w]; ox, oy = x1, y1
    else:
        roi = img; ox, oy = 0, 0

    pt, sc = CV.match_center_multiscale(roi, CV.read_tpl("insert.png"), thr=_CONF_INSERT_THR, scales=(0.9, 1.0, 1.1))
    if not pt:
        pt, sc = CV.match_center_multiscale(roi, CV.read_tpl("insert_alt.png"), thr=_CONF_INSERT_THR, scales=(0.9, 1.0, 1.1))
    if not pt:
        LOG.dev_warn(getattr(adb, "dev_id", "global"), "[INSERT] หา/กดปุ่ม 'ใส่ลง' ไม่เจอใน ROI")
        return False

    gx, gy = ox + pt[0], oy + pt[1]
    LOG.dev_info(getattr(adb, "dev_id", "global"), f"[INSERT] click at ({gx},{gy}) score={sc:.3f} thr={_CONF_INSERT_THR}")
    _log_web(getattr(adb, "dev_id", "global"), f'INSERT: click @({gx},{gy})')
    adb.tap(gx, gy)
    return True

# ======== Template bank / classifier ========
_DATASET_BASE = _CFG.get("dataset_dir", os.getenv("DATASET_DIR", "/app/data/dataset/level"))
_NORM_SIZE = (32, 28)

def _norm_roi_size(img, size=_NORM_SIZE):
    return cv2.resize(img, size, interpolation=cv2.INTER_AREA)

_PLUS_TPL_BANK: Dict[int, list] = {}
_PLUS_CLS_CENTROIDS: Dict[int, np.ndarray] = {}

def _load_plus_template_bank(base_dir: Optional[str] = None) -> Dict[int, list]:
    base = base_dir or _DATASET_BASE
    bank: Dict[int, list] = {}
    loaded = []
    for n in range(1, 7):
        d = os.path.join(base, f"plus{n}")
        if not os.path.isdir(d): continue
        tpls = []
        for fn in os.listdir(d):
            if not fn.endswith("_norm.png"): continue
            path = os.path.join(d, fn)
            im = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
            if im is None: continue
            im = _norm_roi_size(im)
            tpls.append(im)
        if tpls:
            bank[n] = tpls
            loaded.append(f"+{n}:{len(tpls)}")
    # ใช้ global log เพื่อไม่แตกไฟล์อุปกรณ์
    LOG.i("[plus-bank] loaded → " + (", ".join(loaded) if loaded else "(empty)"))
    return bank

def _feat_from_norm(gray_norm: np.ndarray) -> np.ndarray:
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
        if not os.path.isdir(d): continue
        feats = []
        for fn in os.listdir(d):
            if not fn.endswith("_norm.png"): continue
            path = os.path.join(d, fn)
            im = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
            if im is None: continue
            im = _norm_roi_size(im)
            feats.append(_feat_from_norm(im))
        if feats:
            cents[lvl] = np.mean(np.stack(feats, axis=0), axis=0)
    # ใช้ global log เพื่อไม่แตกไฟล์อุปกรณ์
    LOG.i("[plus-cls] centroids → " + (", ".join([f"+{k}" for k in sorted(cents.keys())]) if cents else "(empty)"))
    return cents

# ======== Core readers ========
def _read_level_from_icon_topright(
    adb: ADBAdapter,
    item_center: Tuple[int,int],
    box_size: int = 60,
    debug_tag: str = "item",
) -> Optional[int]:
    """อ่านตัวเลข +n จากมุมขวาบนของไอคอน (หลากหลายวิธี รวม OCR สำรอง)"""
    cx, cy = item_center
    img = _grab(adb)
    crop, _ = _crop_center(img, cx, cy, box_size, box_size)

    h, w = crop.shape[:2]
    rx0 = int(w * float(os.getenv("ICON_ROI_XRATIO", "0.55")))
    ry0 = 0
    rw  = w - rx0
    rh  = int(h * float(os.getenv("ICON_ROI_HRATIO", "0.50")))
    roi = crop[ry0:ry0+rh, rx0:rx0+rw].copy()

    roi_gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    roi_gray_norm = _norm_roi_size(roi_gray)

    # PASS 0: classifier
    cls_thr = float(os.getenv("CONF_PLUS_CLS_THR", "0.88"))
    if _PLUS_CLS_CENTROIDS:
        q = _feat_from_norm(roi_gray_norm)
        best_lvl, best_sim = None, -1.0
        for lvl, c in _PLUS_CLS_CENTROIDS.items():
            sim = float(np.dot(q, c) / (np.linalg.norm(c) + 1e-8))
            if sim > best_sim:
                best_sim, best_lvl = sim, lvl
        if best_lvl is not None and best_sim >= cls_thr:
            LOG.dev_info(getattr(adb, "dev_id", "global"), f"[ICON-PLUS CLS] level=+{best_lvl} cos={best_sim:.3f} (thr={cls_thr})")
            return best_lvl

    # PASS A1: template-bank on gray
    if _PLUS_TPL_BANK:
        best_lvl, best_sc = None, -1.0
        for lvl, tpls in _PLUS_TPL_BANK.items():
            for tpl in tpls:
                res = cv2.matchTemplate(roi_gray_norm, tpl, cv2.TM_CCOEFF_NORMED)
                _, sc, _, _ = cv2.minMaxLoc(res)
                if sc > best_sc:
                    best_sc, best_lvl = sc, lvl
        thr_bank_rgb = float(os.getenv("CONF_PLUS_BANK_RGB_THR", "0.83"))
        if best_lvl is not None and best_sc >= thr_bank_rgb:
            LOG.dev_info(getattr(adb, "dev_id", "global"), f"[ICON-PLUS BANK(gray)] level=+{best_lvl} score={best_sc:.3f} (thr={thr_bank_rgb})")
            return best_lvl

    # PASS C: OCR (+0123456789)
    if _HAVE_TESS:
        tophat = cv2.morphologyEx(roi_gray, cv2.MORPH_TOPHAT, np.ones((3,3), np.uint8), iterations=1)
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
                    LOG.dev_info(getattr(adb, "dev_id", "global"), f"[ICON-PLUS OCR] text='{text}' → level≈+{cand}")
                    return cand
            except Exception:
                pass

    LOG.dev_info(getattr(adb, "dev_id", "global"), "[ICON-PLUS] ไม่พบระดับ")
    return None

# --------- ROI snapshot & diff ----------
def _icon_roi_norm(adb: ADBAdapter, center: Tuple[int,int], box_size: int = 68) -> np.ndarray:
    cx, cy = center
    img = _grab(adb)
    crop, _ = _crop_center(img, cx, cy, box_size, box_size)
    h, w = crop.shape[:2]
    rx0 = int(w * float(os.getenv("ICON_ROI_XRATIO", "0.55")))
    ry0 = 0
    rw  = w - rx0
    rh  = int(h * float(os.getenv("ICON_ROI_HRATIO", "0.50")))
    roi = crop[ry0:ry0+rh, rx0:rx0+rw].copy()
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    norm = _norm_roi_size(gray, size=_NORM_SIZE)
    return norm

def _roi_diff_score(a: Optional[np.ndarray], b: Optional[np.ndarray]) -> float:
    if a is None or b is None: return 1.0
    if a.shape != b.shape: return 1.0
    diff = cv2.absdiff(a, b).astype(np.float32) / 255.0
    return float(diff.mean())

def _ocr_plus_from_icon_norm(norm_gray: np.ndarray) -> Optional[int]:
    if not _HAVE_TESS or norm_gray is None or norm_gray.size == 0:
        return None
    tophat = cv2.morphologyEx(norm_gray, cv2.MORPH_TOPHAT, np.ones((3,3), np.uint8), iterations=1)
    thr = cv2.adaptiveThreshold(tophat, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                cv2.THRESH_BINARY_INV, 31, 9)
    cfg = "--psm 7 --oem 1 -c tessedit_char_whitelist=+123456789"
    try:
        raw = pytesseract.image_to_string(thr, lang="eng", config=cfg)
        txt = (raw or "").strip().replace("\n", " ")
        m = _PLUS_ONE_DIGIT_RE.search(txt)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return None

# ===================== Slot empty checks =====================
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
        LOG.dev_info(getattr(adb, "dev_id", "global"), f"[ตรวจช่อง] ว่าง={emp} (เทมเพลต score={sc:.3f} thr={thr})")
        return emp

    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    v = float(np.var(g))
    edges = cv2.Canny(g, 40, 120)
    edge_ratio = edges.mean()/255.0
    emp = (v < 400.0 and edge_ratio < 0.03)
    LOG.dev_info(getattr(adb, "dev_id", "global"), f"[ตรวจช่อง] ว่าง={emp} (ฮิวริสติก var={v:.1f} edge={edge_ratio:.3f})")
    return emp

# ===================== Device context & logging =====================
def _ctx(dev_id: str) -> dict:
    c = _DEVICE_CTX.get(dev_id)
    if not c:
        c = dict(
            stage="pick", item_idx=0,
            base_level=None, successes=0,
            icon_snap=None, icon_digit=None,
            last_cur=None,
            last_action_ts=0.0, _cfg_dumped=False
        )
        _DEVICE_CTX[dev_id] = c
        LOG.dev_info(dev_id, f"[{dev_id}] เริ่มคอนเท็กซ์ใหม่ stage=pick item_idx=0")
    return c

def _log_config_once(dev_id: str, *, slot_center, slot_status_roi, slot_roi_size, upgrade_btn, insert_roi, items, swipe_cfg):
    c = _ctx(dev_id)
    if c.get("_cfg_dumped"):
        return
    LOG.dev_info(dev_id, f"[{dev_id}] CFG slot_center={slot_center}")
    LOG.dev_info(dev_id, f"[{dev_id}] CFG slot_roi_size=[{slot_roi_size[0]},{slot_roi_size[1]}]")
    LOG.dev_info(dev_id, f"[{dev_id}] CFG upgrade_btn={upgrade_btn}")
    LOG.dev_info(dev_id, f"[{dev_id}] CFG insert_roi=(x1={insert_roi[0]},{insert_roi[1]},{insert_roi[2]},{insert_roi[3]})")
    LOG.dev_info(dev_id, f"[{dev_id}] CFG items(len)={len(items)} sample={items[:6]}")
    LOG.dev_info(dev_id, f"[{dev_id}] CFG swipe={swipe_cfg}")
    c["_cfg_dumped"] = True

def _next_item(dev_id: str, adb: ADBAdapter, items, swipe_cfg, c):
    c["item_idx"] += 1
    if items and (c["item_idx"] % len(items) == 0):
        x, y, dy, ms = swipe_cfg["x"], swipe_cfg["y"], swipe_cfg["dy"], swipe_cfg["ms"]
        LOG.dev_info(dev_id, f"[{dev_id}] SWIPE after {len(items)} items → ({x},{y})→({x},{y+dy}) ms={ms}")
        try:
            adb.tap(970, 120); time.sleep(0.15)
            adb.swipe(x, y, dy=dy, ms=ms); time.sleep(0.15)
        except Exception as e:
            LOG.dev_warn(dev_id, f"[{dev_id}] swipe fail: {e}")
    c["stage"] = "pick"
    c["successes"] = 0
    c["base_level"] = None
    c["icon_snap"] = None
    c["icon_digit"] = None
    c["last_cur"] = None

# ===================== Main step =====================
def worker_step(controller) -> Dict[str, Any]:
    """
    สเตตแมชชีน (no-overlay) + กติกา:
    1) ระดับไม่มีลด
    2) ระดับเพิ่มได้ทีละ 1 ต่อคลิก
    """
    # ----- id only -----
    dev_id = (getattr(controller, "id", None) or "").strip() or "global"

    # เตรียม ADB
    adb = getattr(controller, "adb", None)
    if adb is None:
        adb = ADBAdapter(controller.device)
        controller.adb = adb
    setattr(adb, "dev_id", dev_id)

    target = controller.target_level

    items = _items()
    slot_center = _pt("slot_center", (0, 0))
    slot_roi_size = _size2("slot_roi", (80, 80))
    upgrade_btn = _pt("upgrade_btn", (0, 0))
    insert_roi = _rect("insert_roi", (0, 0, 0, 0))
    swipe_cfg = _swipe()

    _log_config_once(
        dev_id,
        slot_center=slot_center,
        slot_status_roi=(0,0,0,0),
        slot_roi_size=slot_roi_size,
        upgrade_btn=upgrade_btn,
        insert_roi=insert_roi,
        items=items,
        swipe_cfg=swipe_cfg,
    )

    c = _ctx(dev_id)
    out: Dict[str, Any] = {}

    LOG.dev_info(dev_id, f"[{dev_id}] stage={c['stage']} item_idx={c['item_idx']} target=+{target} base={c.get('base_level')} succ={c.get('successes',0)} last_cur={c.get('last_cur')}")
    _log_web(dev_id, f'stage=<b>{_html.escape(c["stage"])}</b> item_idx={c["item_idx"]} target={target}')

    # ---------- pick ----------
    if c["stage"] == "pick":
        if not items:
            LOG.dev_warn(dev_id, f"[{dev_id}] ไม่พบ items ใน config")
            _log_web(dev_id, 'ไม่พบ items ใน config', "WARN")
            time.sleep(0.05)
            return {}
        idx = c["item_idx"] % len(items)
        ix, iy = items[idx]
        LOG.dev_info(dev_id, f"[{dev_id}] แตะไอเทม idx={idx} @({ix},{iy})")
        _log_web(dev_id, f'แตะไอเทม idx={idx} @({ix},{iy})')
        adb.tap(ix, iy)
        c["stage"] = "insert"
        c["last_action_ts"] = time.time()
        return {}

    # ---------- insert ----------
    if c["stage"] == "insert":
        idx = c["item_idx"] % len(items)
        ix, iy = items[idx]
        LOG.dev_info(dev_id, f"[{dev_id}] INSERT: เตรียมกด 'ใส่ลง' ด้วย CV สำหรับ idx={idx} @({ix},{iy})")
        _log_web(dev_id, f'INSERT: เตรียมกด "ใส่ลง" idx={idx} @({ix},{iy})')

        ok = _click_insert_via_cv(adb, insert_roi_rect=insert_roi, wait_pre=None)
        if not ok:
            sx, sy = slot_center
            LOG.dev_warn(dev_id, f"[{dev_id}] INSERT: CV not found → fallback slot_center @({sx},{sy})")
            _log_web(dev_id, f'INSERT: CV not found → fallback slot_center @({sx},{sy})', "WARN")

        _log_web(dev_id, 'INSERT: done → รอ 0.25s')
        time.sleep(float(os.getenv("POST_INSERT_SETTLE", "0.25")))

        c["stage"] = "inspect"
        c["last_action_ts"] = time.time()
        return {}

    # ---------- inspect ----------
    if c["stage"] == "inspect":
        fx = int(os.getenv("ICON_FIX_CX", "960"))
        fy = int(os.getenv("ICON_FIX_CY", "323"))
        icon_box = int(os.getenv("ICON_BOX_SIZE", "68"))

        base_snap = _icon_roi_norm(adb, (fx, fy), box_size=icon_box)
        c["icon_snap"] = base_snap
        c["icon_digit"] = _ocr_plus_from_icon_norm(base_snap)

        ICON_LVL_OFFSET = int(os.getenv("ICON_LVL_OFFSET", "-1"))
        base_num = _read_level_from_icon_topright(adb, (fx, fy), box_size=icon_box)
        if base_num is None:
            lvl = 0
        else:
            lvl = max(0, int(base_num) + ICON_LVL_OFFSET)

        c["base_level"] = lvl
        c["successes"] = 0
        c["last_cur"] = lvl  # ← เก็บ ground truth ตอน inspect

        LOG.dev_info(dev_id, f"[{dev_id}] หลัง INSERT baseline level={lvl} digit={c['icon_digit']}")
        _log_web(dev_id, f'หลัง INSERT อ่านระดับ = {_lv_html(lvl, target)} (digit={c["icon_digit"]})')

        c["stage"] = "upgrade"
        c["last_action_ts"] = time.time()
        return {}

    # ---------- upgrade ----------
    if c["stage"] == "upgrade":
        MIN_TAP_INTERVAL   = float(os.getenv("MIN_TAP_INTERVAL",   "0.05"))
        POST_UPGRADE_WAIT  = float(os.getenv("POST_UPGRADE_WAIT",  "0.16"))
        ICON_DIFF_THR      = float(os.getenv("ICON_DIFF_THR",      "0.045"))
        ICON_POLL_BUDGET   = float(os.getenv("ICON_POLL_BUDGET",   "0.40"))
        ICON_POLL_STEP     = float(os.getenv("ICON_POLL_STEP",     "0.10"))

        base = c.get("base_level", 0) or 0
        cur  = base + c.get("successes", 0)
        prev_cur = c.get("last_cur", cur)
        if c.get("last_cur") is None:
            c["last_cur"] = cur
            prev_cur = cur

        LOG.dev_info(dev_id, f"[{dev_id}] UPGRADE base={base} succ={c['successes']} cur={cur} (prev_cur={prev_cur}) / target=+{target}")
        _log_web(dev_id, f'UPGRADE: curr={_lv_html(cur, target)} / target={target}')

        if cur >= target:
            LOG.dev_info(dev_id, f"[{dev_id}] บรรลุเป้าหมาย +{target} → ไปชิ้นถัดไป")
            _log_web(dev_id, f'เสร็จสิ้นชิ้นนี้: curr={_lv_html(cur, target)} / target={target} ✅')
            _next_item(dev_id, adb, items, swipe_cfg, c)
            return {"done_item": True}

        if _is_slot_empty(adb, slot_center, slot_roi_size):
            LOG.dev_info(dev_id, f"[{dev_id}] ช่องว่าง (ไอเทมหาย/แตก) → ข้ามชิ้นนี้")
            _log_web(dev_id, 'ช่องว่าง (ไอเทมหาย/แตก) → ข้ามชิ้นนี้', "WARN")
            _next_item(dev_id, adb, items, swipe_cfg, c)
            return {"break_at_level": cur}

        # debounce
        last = c.get("last_action_ts", 0.0)
        remain = MIN_TAP_INTERVAL - (time.time() - last)
        if remain > 0:
            time.sleep(min(0.04, remain))
        c["last_action_ts"] = time.time()

        # tap
        ux, uy = upgrade_btn
        LOG.dev_info(dev_id, f"[{dev_id}] UPGRADE: click upgrade_btn @({ux},{uy})")
        _log_web(dev_id, f'UPGRADE: click upgrade_btn @({ux},{uy})')
        adb.tap(ux, uy)

        time.sleep(POST_UPGRADE_WAIT)

        fx = int(os.getenv("ICON_FIX_CX", "960"))
        fy = int(os.getenv("ICON_FIX_CY", "323"))
        icon_box = int(os.getenv("ICON_BOX_SIZE", "68"))
        ICON_LVL_OFFSET = int(os.getenv("ICON_LVL_OFFSET", "-1"))

        before_snap = c.get("icon_snap")
        before_digit = c.get("icon_digit")

        def _judge_once(tag: str) -> Optional[bool]:
            # ช่องว่างหลัง tap → ข้ามชิ้น
            if _is_slot_empty(adb, slot_center, slot_roi_size):
                LOG.dev_info(dev_id, f"[{dev_id}] ช่องว่างหลัง tap → ข้ามชิ้น")
                _log_web(dev_id, 'ช่องว่างหลัง tap → ข้ามชิ้น', "WARN")
                _next_item(dev_id, adb, items, swipe_cfg, c)
                out["break_at_level"] = prev_cur
                return True

            # อ่านระดับเชิงตัวเลขก่อน (ถ้าได้จะใช้ตัดสินตามกติกา)
            raw_after = _read_level_from_icon_topright(adb, (fx, fy), box_size=icon_box)
            after_num = None if raw_after is None else max(0, int(raw_after) + ICON_LVL_OFFSET)

            after_snap = _icon_roi_norm(adb, (fx, fy), box_size=icon_box)
            diff = _roi_diff_score(before_snap, after_snap)
            after_digit = _ocr_plus_from_icon_norm(after_snap)

            LOG.dev_info(dev_id, f"[{dev_id}] [{tag}] num={after_num} vs prev_cur={prev_cur} | diff={diff:.3f} digit {before_digit}→{after_digit}")

            # ---- กติกา 1/2: ไม่มีลด และเพิ่มได้ทีละ 1 ----
            if after_num is not None:
                if after_num < prev_cur:
                    LOG.dev_warn(dev_id, f"[{dev_id}] [{tag}] after_num<{prev_cur} → treat as FAIL (no-decrease rule)")
                    _log_web(dev_id, f'判定: <b style="color:#e53935">ล้มเหลว</b> (อ่านลดลง → ปัดตก)', "WARN")
                    return False
                elif after_num == prev_cur:
                    return False
                elif after_num >= prev_cur + 1:
                    # clamp +1
                    c["successes"] = c.get("successes", 0) + 1
                    c["last_cur"] = prev_cur + 1
                    c["icon_snap"] = after_snap
                    c["icon_digit"] = after_digit
                    new_cur = c["last_cur"]
                    out["success_clicks"] = out.get("success_clicks", 0) + 1
                    LOG.dev_info(dev_id, f"[{dev_id}] [{tag}] SUCCESS by numeric (+1 clamp) → new_cur={new_cur}")
                    _log_web(dev_id, f'ผล: <b>สำเร็จ</b> → curr={_lv_html(new_cur, target)} / target={target}')
                    if new_cur >= target:
                        LOG.dev_info(dev_id, f"[{dev_id}] บรรลุเป้าหมาย +{target} → ไปชิ้นถัดไป (instant)")
                        _log_web(dev_id, f'เสร็จสิ้นชิ้นนี้: curr={_lv_html(new_cur, target)} / target={target} ✅')
                        _next_item(dev_id, adb, items, swipe_cfg, c)
                        out["done_item"] = True
                    return True

            # ถ้าอ่านเลขไม่ได้ → ใช้ heuristic diff/ocr (ยึดกติกา +1)
            success_by_diff = (diff > float(os.getenv("ICON_DIFF_THR", str(ICON_DIFF_THR))))
            success_by_ocr  = (after_digit is not None and after_digit != before_digit)
            if success_by_diff or success_by_ocr:
                c["successes"] = c.get("successes", 0) + 1
                c["last_cur"] = prev_cur + 1
                c["icon_snap"] = after_snap
                c["icon_digit"] = after_digit
                new_cur = c["last_cur"]
                out["success_clicks"] = out.get("success_clicks", 0) + 1
                LOG.dev_info(dev_id, f"[{dev_id}] [{tag}] SUCCESS by {'diff' if success_by_diff else 'ocr'} (+1) → new_cur={new_cur}")
                _log_web(dev_id, f'ผล: <b>สำเร็จ</b> → curr={_lv_html(new_cur, target)} / target={target}')
                if new_cur >= target:
                    LOG.dev_info(dev_id, f"[{dev_id}] บรรลุเป้าหมาย +{target} → ไปชิ้นถัดไป (instant)")
                    _log_web(dev_id, f'เสร็จสิ้นชิ้นนี้: curr={_lv_html(new_cur, target)} / target={target} ✅')
                    _next_item(dev_id, adb, items, swipe_cfg, c)
                    out["done_item"] = True
                return True

            return False  # ไม่เปลี่ยน → FAIL

        # ตัดสินครั้งแรก
        res = _judge_once("posttap")
        if res is True:
            return out
        if res is False:
            # รอ render เพิ่มเล็กน้อย
            t0 = time.time()
            decided = False
            while (time.time() - t0) < ICON_POLL_BUDGET:
                time.sleep(ICON_POLL_STEP)
                res = _judge_once("poll")
                if res is True:
                    decided = True
                    return out
            if not decided:
                LOG.dev_info(dev_id, f"[{dev_id}] ผล: ล้มเหลว (no change within {ICON_POLL_BUDGET:.2f}s)")
                _log_web(dev_id, f'ผล: <b style="color:#e53935">ล้มเหลว</b> → curr={_lv_html(prev_cur, target)} / target={target}', "WARN")
                return out

    return out

# ===================== Loop wrapper =====================
def worker_loop(ctrl, step_fn, sleep_sec: float = 0.12):
    dev_id = (getattr(ctrl, "id", None) or "").strip() or "global"
    LOG.dev_info(dev_id, f"[{dev_id}] loop start")

    global _PLUS_TPL_BANK, _PLUS_CLS_CENTROIDS
    if not _PLUS_TPL_BANK:
        _PLUS_TPL_BANK = _load_plus_template_bank()
    if not _PLUS_CLS_CENTROIDS:
        _PLUS_CLS_CENTROIDS = _build_plus_classifier()

    try:
        t0_item = time.time()
        while not ctrl.stop_event.is_set():
            if ctrl.pause_event.is_set():
                time.sleep(0.15)
                continue
            try:
                step_fn(ctrl)
                ctrl.last_tick = time.time()
            except Exception as e:
                ctrl.last_error = str(e)
                ctrl.state = "error"
                LOG.dev_error(dev_id, f"[{dev_id}] step error: {e}")
                _log_web(dev_id, f'step error: {_html.escape(str(e))}', "ERROR")
                break

            maxsec = int(os.getenv("MAX_ITEM_TIME_SEC", str(_MAX_ITEM_TIME_SEC)))
            if maxsec > 0 and (time.time() - t0_item) > maxsec:
                LOG.dev_info(dev_id, f"[{dev_id}] item timeout {maxsec}s → advance item")
                _log_web(dev_id, f'item timeout {maxsec}s → advance item', "WARN")
                c = _ctx(dev_id)
                items = _items()
                swipe_cfg = _swipe()
                adb = getattr(ctrl, "adb", None) or ADBAdapter(ctrl.device)
                setattr(adb, "dev_id", dev_id)
                ctrl.adb = adb
                _next_item(dev_id, adb, items, swipe_cfg, c)
                t0_item = time.time()

            if int(ctrl.last_tick) % 3 == 0:
                LOG.dev_info(dev_id, f"[{dev_id}] heartbeat items={getattr(ctrl,'items_upgraded_done',0)} target=+{getattr(ctrl,'target_level',0)}")
                _log_web(dev_id, f'heartbeat items={getattr(ctrl,"items_upgraded_done",0)} target={getattr(ctrl,"target_level",0)}')

            time.sleep(sleep_sec)
    finally:
        LOG.dev_info(dev_id, f"[{dev_id}] loop exit (state={ctrl.state})")
        _log_web(dev_id, f'loop exit (state={_html.escape(str(ctrl.state))})')
