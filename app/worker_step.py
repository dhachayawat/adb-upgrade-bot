# app/worker_step.py
from __future__ import annotations
from typing import Dict, Any, Optional, Tuple
import os
import time
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

# ======== Pre-insert: read [n] from slot_status_roi ========
import re
_BRACKET_NUM_RE = re.compile(r"\[\s*(\d{1,2})\s*\]")

def _read_bracket_level_from_status(adb: ADBAdapter,
                                    slot_status_roi_rect: Tuple[int, int, int, int],
                                    debug_tag: Optional[str] = "preinsert") -> Optional[int]:
    img = _grab(adb)
    roi, (ox, oy) = _crop_rect(img, slot_status_roi_rect)
    thr = _binarize(roi)
    text = _ocr_text(thr, psm=7)

    lvl = None
    m = _BRACKET_NUM_RE.search(text)
    if m:
        try:
            lvl = int(m.group(1))
        except Exception:
            lvl = None

    LOG.i(f"[OCR ก่อนใส่ลง] text='{text}' → ระดับในวงเล็บ = {lvl} (roi {ox},{oy},{roi.shape[1]}x{roi.shape[0]})")

    if os.getenv("SAVE_SCREENCAP", "1") != "0":
        try:
            os.makedirs(_CFG.get("debug_dir", "/app/cache/debug"), exist_ok=True)
            cv2.imwrite(os.path.join(_CFG.get("debug_dir", "/app/cache/debug")), roi)
            cv2.imwrite(os.path.join(_CFG.get("debug_dir", "/app/cache/debug")), thr)
        except Exception:
            pass
    return lvl

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

        lvl = _read_bracket_level_from_status(adb, slot_status_roi)
        c["base_level"] = lvl if isinstance(lvl, int) else None

        if lvl is None or lvl <= 4:
            LOG.i(f"[{dev_id}] ตัดสินใจ INSERT (level={lvl})")
            c["stage"] = "insert"
            c["successes"] = 0
            c["last_action_ts"] = time.time()
        else:
            LOG.i(f"[{dev_id}] ข้ามไอเทมนี้ (level={lvl} >= 5)")
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
            cur = base + c["successes"]
            attempt_level = cur + 1
            LOG.i(f"[{dev_id}] ผล: ล้มเหลว (กำลังไป +{attempt_level})")
            if cur >= _BREAK_LEVEL_MIN:
                _summary().add_break(controller.id, attempt_level)
                LOG.i(f"[{dev_id}] แตกที่ +{attempt_level} (>= min {_BREAK_LEVEL_MIN}) → ไปชิ้นถัดไป")
                _next_item(dev_id, adb, items, swipe_cfg, c)
                out["break_at_level"] = attempt_level
                return out
            else:
                LOG.i(f"[{dev_id}] ล้มเหลวแต่ยัง <{_BREAK_LEVEL_MIN} → ลองต่อ")
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
            cur = base + c["successes"]
            attempt_level = cur + 1
            LOG.i(f"[{dev_id}] (ดีเลย์) ล้มเหลวที่ +{attempt_level}")
            if cur >= _BREAK_LEVEL_MIN:
                _summary().add_break(controller.id, attempt_level)
                LOG.i(f"[{dev_id}] แตกที่ +{attempt_level} → ไปชิ้นถัดไป")
                _next_item(dev_id, adb, items, swipe_cfg, c)
                out["break_at_level"] = attempt_level
                return out
            else:
                LOG.i(f"[{dev_id}] ยัง <{_BREAK_LEVEL_MIN} → ลองต่อ")
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
