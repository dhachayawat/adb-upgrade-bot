# app/config.py
import os
import json
from typing import Dict, Any, List, Tuple

# -------------------- Paths --------------------
CONFIG_FILE = os.getenv("CONFIG_FILE", "/app/data/config/config.json")
DEBUG_DIR   = os.getenv("DEBUG_DIR", "/app/cache/debug")
TEMPLATES_DIR = os.getenv("TEMPLATES_DIR", "/app/templates")
SCREENCAP_PREFIX = os.getenv("SCREENCAP_PREFIX", "snap")
SAVE_SCREENCAP = True

# -------------------- Defaults (schema) --------------------
# NOTE:
# - slot_status_roi ใช้รูปแบบ {x1,y1,w,h}
# - ยังคงรองรับไฟล์เก่าที่ให้ slot_status=[cx,cy] + slot_status_roi=[w,h] (จะคำนวณ rect ให้)
_DEFAULT: Dict[str, Any] = {
    "device": "host.docker.internal:5605",
    "slot_center": [850, 327],
    "slot_status": [850, 327],  # legacy center, ยังอ่านได้ (เผื่อไฟล์เก่า)
    "slot_roi":    [57, 55],    # legacy size, ใช้คำนวณได้ถ้า slot_status_roi เป็น [w,h]
    "slot_status_roi": {"x1": 460, "y1": 159, "w": 80, "h": 80},  # << ใหม่: rect เต็ม
    "overlay_abs": {"x1":740,"y1":264,"w":220,"h":124},
    "insert_roi":  {"x1":544,"y1":575,"w":391,"h":80},
    "upgrade_btn": [844, 626],
    "items": [[285,170],[345,170],[405,170],[465,170],[525,170],[585,170]],
    "swipe": {"x":585,"y":230,"dy":-64,"ms":800},
}

# -------------------- Load config (file first) --------------------
def _load_from_file(path: str):
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None

def _merge_defaults(cfg: Dict[str, Any], defaults: Dict[str, Any]) -> Dict[str, Any]:
    out = defaults.copy()
    if cfg:
        out.update(cfg)
    return out

def _coerce_int_tuple2(v, fallback) -> List[int]:
    try:
        return [int(v[0]), int(v[1])]
    except Exception:
        return list(fallback)

def _coerce_rect(d, fallback) -> Dict[str, int]:
    """
    รับได้ทั้ง:
      - dict: {"x1":..,"y1":..,"w":..,"h":..}
      - list/tuple: [x1,y1,w,h]
    """
    try:
        if isinstance(d, dict):
            return {"x1": int(d["x1"]), "y1": int(d["y1"]), "w": int(d["w"]), "h": int(d["h"])}
        if isinstance(d, (list, tuple)) and len(d) == 4:
            return {"x1": int(d[0]), "y1": int(d[1]), "w": int(d[2]), "h": int(d[3])}
    except Exception:
        pass
    return dict(fallback)

def _items_list(cfg: Dict[str, Any]) -> List[tuple]:
    arr = cfg.get("items", _DEFAULT["items"])
    out = []
    for p in arr[:6]:
        try:
            out.append((int(p[0]), int(p[1])))
        except Exception:
            out.append((0, 0))
    while len(out) < 6:
        out.append((0, 0))
    return out

def _swipe_dict(cfg: Dict[str, Any]):
    s = cfg.get("swipe", _DEFAULT["swipe"])
    try:
        return {"x": int(s["x"]), "y": int(s["y"]), "dy": int(s["dy"]), "ms": int(s["ms"])}
    except Exception:
        return dict(_DEFAULT["swipe"])

def _legacy_rect_from_center_and_size(center_xy: List[int], size_wh: List[int]) -> Dict[str, int]:
    cx, cy = int(center_xy[0]), int(center_xy[1])
    w, h = int(size_wh[0]), int(size_wh[1])
    x1 = max(0, cx - w // 2)
    y1 = max(0, cy - h // 2)
    return {"x1": x1, "y1": y1, "w": w, "h": h}

# โหลดครั้งแรก
_FCFG = _load_from_file(CONFIG_FILE)
FCFG: Dict[str, Any] = _merge_defaults(_FCFG, _DEFAULT)

# -------------------- Public getters / mapping --------------------
DEVICE = FCFG.get("device") or os.getenv("DEVICE", _DEFAULT["device"])
ADB_BIN = os.getenv("ADB_BIN", "adb")

# Slot centers / legacy helpers
_slot_center = _coerce_int_tuple2(FCFG.get("slot_center", _DEFAULT["slot_center"]), _DEFAULT["slot_center"])
SLOT_CENTER_X, SLOT_CENTER_Y = _slot_center

# legacy center (ยังคงให้ไว้ สำหรับโค้ด/เทมเพลตเก่า)
_slot_status_center = _coerce_int_tuple2(FCFG.get("slot_status", _DEFAULT["slot_status"]), _DEFAULT["slot_status"])
SLOT_STATUS_X, SLOT_STATUS_Y = _slot_status_center

# legacy size (ใช้คำนวณถ้า slot_status_roi เป็น [w,h])
_slot_roi_size = FCFG.get("slot_roi", _DEFAULT["slot_roi"])

# slot_status_roi (รองรับทั้งรูปแบบใหม่และเก่า)
_raw_ssr = FCFG.get("slot_status_roi", _DEFAULT["slot_status_roi"])
if isinstance(_raw_ssr, dict) and {"x1","y1","w","h"} <= set(_raw_ssr.keys()):
    _slot_status_roi = _coerce_rect(_raw_ssr, _DEFAULT["slot_status_roi"])
elif isinstance(_raw_ssr, (list, tuple)) and len(_raw_ssr) == 2:
    # ไฟล์เก่า: [w,h] → ต้องใช้ center (slot_status) เป็นฐาน
    _slot_status_roi = _legacy_rect_from_center_and_size(_slot_status_center, [int(_raw_ssr[0]), int(_raw_ssr[1])])
else:
    # เผื่อไฟล์เก่ามากที่ไม่ได้ใส่ slot_status_roi แต่มี slot_roi
    try:
        _slot_status_roi = _legacy_rect_from_center_and_size(_slot_status_center, [int(_slot_roi_size[0]), int(_slot_roi_size[1])])
    except Exception:
        _slot_status_roi = dict(_DEFAULT["slot_status_roi"])

SLOT_STATUS_ROI_X1 = _slot_status_roi["x1"]
SLOT_STATUS_ROI_Y1 = _slot_status_roi["y1"]
SLOT_STATUS_ROI_W  = _slot_status_roi["w"]
SLOT_STATUS_ROI_H  = _slot_status_roi["h"]

# Rectangles (other)
_overlay   = _coerce_rect(FCFG.get("overlay_abs", _DEFAULT["overlay_abs"]), _DEFAULT["overlay_abs"])
OVERLAY_X1, OVERLAY_Y1, OVERLAY_W, OVERLAY_H = _overlay["x1"], _overlay["y1"], _overlay["w"], _overlay["h"]

_insertroi = _coerce_rect(FCFG.get("insert_roi", _DEFAULT["insert_roi"]), _DEFAULT["insert_roi"])
INSERT_ROI_X1, INSERT_ROI_Y1, INSERT_ROI_W, INSERT_ROI_H = _insertroi["x1"], _insertroi["y1"], _insertroi["w"], _insertroi["h"]

# Points
_upg = _coerce_int_tuple2(FCFG.get("upgrade_btn", _DEFAULT["upgrade_btn"]), _DEFAULT["upgrade_btn"])
UPGRADE_BTN_X, UPGRADE_BTN_Y = _upg

ITEM_POSITIONS = _items_list(FCFG)

# Swipe
_swipe = _swipe_dict(FCFG)
TRAY_SWIPE_X, TRAY_SWIPE_Y, TRAY_SWIPE_DY, TRAY_SWIPE_MS = _swipe["x"], _swipe["y"], _swipe["dy"], _swipe["ms"]

# -------------------- Timings / thresholds --------------------
CONF_INSERT_THR       = float(os.getenv("CONF_INSERT_THR", "0.75"))
CONF_SLOT_EMPTY_THR   = float(os.getenv("CONF_SLOT_EMPTY_THR", "0.85"))
CONF_SUCCESS_THR      = float(os.getenv("CONF_SUCCESS_THR", "0.83"))
CONF_FAIL_THR         = float(os.getenv("CONF_FAIL_THR", "0.83"))
OCR_BADGE_MIN_CONF    = float(os.getenv("OCR_BADGE_MIN_CONF", "60"))
CLICK_DELAY_MIN       = float(os.getenv("CLICK_DELAY_MIN", "0.15"))
CLICK_DELAY_MAX       = float(os.getenv("CLICK_DELAY_MAX", "0.30"))
UPGRADE_COOLDOWN_MIN  = float(os.getenv("UPGRADE_COOLDOWN_MIN", "0.90"))
UPGRADE_COOLDOWN_MAX  = float(os.getenv("UPGRADE_COOLDOWN_MAX", "1.40"))
POST_UPGRADE_WAIT_SEC = float(os.getenv("POST_UPGRADE_WAIT_SEC", "0.7"))
FAIL_RETRY_DELAY_SEC  = float(os.getenv("FAIL_RETRY_DELAY_SEC", "1"))
MAX_UPGRADE_CLICKS_PER_ITEM = int(os.getenv("MAX_UPGRADE_CLICKS_PER_ITEM", "40"))
MAX_ITEM_TIME_SEC           = int(os.getenv("MAX_ITEM_TIME_SEC", "30"))

# compat
CACHE_DIR = DEBUG_DIR

# -------------------- Export helpers --------------------
def export_schema() -> Dict[str, Any]:
    """คืนค่าคอนฟิกปัจจุบัน (สำหรับ UI/REST)"""
    return {
        "device": DEVICE,
        "slot_center": [SLOT_CENTER_X, SLOT_CENTER_Y],
        "slot_status": [SLOT_STATUS_X, SLOT_STATUS_Y],  # legacy center (ยัง export ไว้ให้)
        "slot_roi":    [int(_slot_roi_size[0]), int(_slot_roi_size[1])],  # legacy size
        "slot_status_roi": {  # รูปแบบใหม่ที่ใช้จริง
            "x1": SLOT_STATUS_ROI_X1, "y1": SLOT_STATUS_ROI_Y1,
            "w":  SLOT_STATUS_ROI_W,  "h":  SLOT_STATUS_ROI_H
        },
        "overlay_abs": {"x1":OVERLAY_X1,"y1":OVERLAY_Y1,"w":OVERLAY_W,"h":OVERLAY_H},
        "insert_roi":  {"x1":INSERT_ROI_X1,"y1":INSERT_ROI_Y1,"w":INSERT_ROI_W,"h":INSERT_ROI_H},
        "upgrade_btn": [UPGRADE_BTN_X, UPGRADE_BTN_Y],
        "items":       [list(p) for p in ITEM_POSITIONS],
        "swipe":       {"x":TRAY_SWIPE_X,"y":TRAY_SWIPE_Y,"dy":TRAY_SWIPE_DY,"ms":TRAY_SWIPE_MS},
        "paths": {"config_file": CONFIG_FILE, "debug_dir": DEBUG_DIR}
    }

def to_dict() -> Dict[str, Any]:
    return export_schema()

# -------------------- Live reload --------------------
def reload():
    """
    อ่านไฟล์คอนฟิกใหม่ แล้ว bind ค่ากลับเข้าตัวแปรโมดูลทั้งหมด
    - รองรับ slot_status_roi แบบ dict {x1,y1,w,h} เป็นหลัก
    - ยังรองรับไฟล์เก่าที่ให้ slot_status=[cx,cy] + slot_status_roi=[w,h]
    """
    global FCFG, DEVICE
    global SLOT_CENTER_X, SLOT_CENTER_Y, SLOT_STATUS_X, SLOT_STATUS_Y
    global SLOT_STATUS_ROI_X1, SLOT_STATUS_ROI_Y1, SLOT_STATUS_ROI_W, SLOT_STATUS_ROI_H
    global OVERLAY_X1, OVERLAY_Y1, OVERLAY_W, OVERLAY_H
    global INSERT_ROI_X1, INSERT_ROI_Y1, INSERT_ROI_W, INSERT_ROI_H
    global UPGRADE_BTN_X, UPGRADE_BTN_Y
    global ITEM_POSITIONS
    global TRAY_SWIPE_X, TRAY_SWIPE_Y, TRAY_SWIPE_DY, TRAY_SWIPE_MS
    global _slot_roi_size, _slot_status_center  # keep for export/compat

    _cfg = _load_from_file(CONFIG_FILE)
    FCFG = _merge_defaults(_cfg, _DEFAULT)

    DEVICE = FCFG.get("device") or os.getenv("DEVICE", _DEFAULT["device"])

    _slot_center = _coerce_int_tuple2(FCFG.get("slot_center", _DEFAULT["slot_center"]), _DEFAULT["slot_center"])
    SLOT_CENTER_X, SLOT_CENTER_Y = _slot_center

    _slot_status_center = _coerce_int_tuple2(FCFG.get("slot_status", _DEFAULT["slot_status"]), _DEFAULT["slot_status"])
    SLOT_STATUS_X, SLOT_STATUS_Y = _slot_status_center

    _slot_roi_size = FCFG.get("slot_roi", _DEFAULT["slot_roi"])

    # slot_status_roi normalize (dict {x1,y1,w,h} OR legacy [w,h])
    _raw_ssr = FCFG.get("slot_status_roi", _DEFAULT["slot_status_roi"])
    if isinstance(_raw_ssr, dict) and {"x1","y1","w","h"} <= set(_raw_ssr.keys()):
        _slot_status_roi = _coerce_rect(_raw_ssr, _DEFAULT["slot_status_roi"])
    elif isinstance(_raw_ssr, (list, tuple)) and len(_raw_ssr) == 2:
        _slot_status_roi = _legacy_rect_from_center_and_size(_slot_status_center, [int(_raw_ssr[0]), int(_raw_ssr[1])])
    else:
        try:
            _slot_status_roi = _legacy_rect_from_center_and_size(_slot_status_center, [int(_slot_roi_size[0]), int(_slot_roi_size[1])])
        except Exception:
            _slot_status_roi = dict(_DEFAULT["slot_status_roi"])

    SLOT_STATUS_ROI_X1 = _slot_status_roi["x1"]
    SLOT_STATUS_ROI_Y1 = _slot_status_roi["y1"]
    SLOT_STATUS_ROI_W  = _slot_status_roi["w"]
    SLOT_STATUS_ROI_H  = _slot_status_roi["h"]

    _overlay = _coerce_rect(FCFG.get("overlay_abs", _DEFAULT["overlay_abs"]), _DEFAULT["overlay_abs"])
    OVERLAY_X1, OVERLAY_Y1, OVERLAY_W, OVERLAY_H = _overlay["x1"], _overlay["y1"], _overlay["w"], _overlay["h"]

    _insertroi = _coerce_rect(FCFG.get("insert_roi", _DEFAULT["insert_roi"]), _DEFAULT["insert_roi"])
    INSERT_ROI_X1, INSERT_ROI_Y1, INSERT_ROI_W, INSERT_ROI_H = _insertroi["x1"], _insertroi["y1"], _insertroi["w"], _insertroi["h"]

    _upg = _coerce_int_tuple2(FCFG.get("upgrade_btn", _DEFAULT["upgrade_btn"]), _DEFAULT["upgrade_btn"])
    UPGRADE_BTN_X, UPGRADE_BTN_Y = _upg

    ITEM_POSITIONS = _items_list(FCFG)

    _swipe = _swipe_dict(FCFG)
    TRAY_SWIPE_X, TRAY_SWIIPE_Y, TRAY_SWIPE_DY, TRAY_SWIPE_MS = _swipe["x"], _swipe["y"], _swipe["dy"], _swipe["ms"]
