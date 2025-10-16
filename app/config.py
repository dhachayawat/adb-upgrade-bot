# app/config.py
import os
import json
from typing import Dict, Any, List

# -------------------- Paths --------------------
CONFIG_FILE = os.getenv("CONFIG_FILE", "/app/data/config/config.json")
DEBUG_DIR   = os.getenv("DEBUG_DIR", "/app/cache/debug")
TEMPLATES_DIR = os.getenv("TEMPLATES_DIR", "/app/templates")
SCREENCAP_PREFIX = os.getenv("SCREENCAP_PREFIX", "snap")
SAVE_SCREENCAP = True

# -------------------- Defaults (schema) --------------------
_DEFAULT: Dict[str, Any] = {
    "device": "host.docker.internal:5605",
    "slot_center": [850, 327],
    "slot_status": [850, 327],
    "slot_roi":    [57, 55],            # [w, h]
    "slot_status_roi": [80, 80],        # << ใหม่: ขนาด ROI ของ slot_status
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

def _coerce_int_tuple2(v, fallback):
    try:
        return [int(v[0]), int(v[1])]
    except Exception:
        return list(fallback)

def _coerce_rect(d, fallback):
    try:
        return {
            "x1": int(d["x1"]), "y1": int(d["y1"]),
            "w":  int(d["w"]),  "h":  int(d["h"]),
        }
    except Exception:
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

# โหลดครั้งแรก
_FCFG = _load_from_file(CONFIG_FILE)
FCFG: Dict[str, Any] = _merge_defaults(_FCFG, _DEFAULT)

# -------------------- Public getters / mapping --------------------
DEVICE = FCFG.get("device") or os.getenv("DEVICE", _DEFAULT["device"])
ADB_BIN = os.getenv("ADB_BIN", "adb")

# Slot centers / rois
_slot_center = _coerce_int_tuple2(FCFG.get("slot_center", _DEFAULT["slot_center"]), _DEFAULT["slot_center"])
SLOT_CENTER_X, SLOT_CENTER_Y = _slot_center

_slot_status = _coerce_int_tuple2(FCFG.get("slot_status", _DEFAULT["slot_status"]), _DEFAULT["slot_status"])
SLOT_STATUS_X, SLOT_STATUS_Y = _slot_status

_slot_roi    = FCFG.get("slot_roi", _DEFAULT["slot_roi"])
SLOT_ROI_W, SLOT_ROI_H = int(_slot_roi[0]), int(_slot_roi[1])

_slot_status_roi = FCFG.get("slot_status_roi", _DEFAULT["slot_status_roi"])
SLOT_STATUS_ROI_W, SLOT_STATUS_ROI_H = int(_slot_status_roi[0]), int(_slot_status_roi[1])  # << ใหม่

# Rectangles
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
POST_UPGRADE_WAIT_SEC = float(os.getenv("POST_UPGRADE_WAIT_SEC", "1.0"))
FAIL_RETRY_DELAY_SEC  = float(os.getenv("FAIL_RETRY_DELAY_SEC", "1.5"))
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
        "slot_status": [SLOT_STATUS_X, SLOT_STATUS_Y],
        "slot_roi":    [SLOT_ROI_W, SLOT_ROI_H],
        "slot_status_roi": [SLOT_STATUS_ROI_W, SLOT_STATUS_ROI_H],  # << ใหม่
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
    """
    global FCFG, DEVICE
    global SLOT_CENTER_X, SLOT_CENTER_Y, SLOT_STATUS_X, SLOT_STATUS_Y
    global SLOT_ROI_W, SLOT_ROI_H, SLOT_STATUS_ROI_W, SLOT_STATUS_ROI_H
    global OVERLAY_X1, OVERLAY_Y1, OVERLAY_W, OVERLAY_H
    global INSERT_ROI_X1, INSERT_ROI_Y1, INSERT_ROI_W, INSERT_ROI_H
    global UPGRADE_BTN_X, UPGRADE_BTN_Y
    global ITEM_POSITIONS
    global TRAY_SWIPE_X, TRAY_SWIPE_Y, TRAY_SWIPE_DY, TRAY_SWIPE_MS

    _cfg = _load_from_file(CONFIG_FILE)
    FCFG = _merge_defaults(_cfg, _DEFAULT)

    DEVICE = FCFG.get("device") or os.getenv("DEVICE", _DEFAULT["device"])

    _slot_center = _coerce_int_tuple2(FCFG.get("slot_center", _DEFAULT["slot_center"]), _DEFAULT["slot_center"])
    SLOT_CENTER_X, SLOT_CENTER_Y = _slot_center

    _slot_status = _coerce_int_tuple2(FCFG.get("slot_status", _DEFAULT["slot_status"]), _DEFAULT["slot_status"])
    SLOT_STATUS_X, SLOT_STATUS_Y = _slot_status

    _slot_roi = FCFG.get("slot_roi", _DEFAULT["slot_roi"])
    SLOT_ROI_W, SLOT_ROI_H = int(_slot_roi[0]), int(_slot_roi[1])

    _slot_status_roi = FCFG.get("slot_status_roi", _DEFAULT["slot_status_roi"])
    SLOT_STATUS_ROI_W, SLOT_STATUS_ROI_H = int(_slot_status_roi[0]), int(_slot_status_roi[1])

    _overlay = _coerce_rect(FCFG.get("overlay_abs", _DEFAULT["overlay_abs"]), _DEFAULT["overlay_abs"])
    OVERLAY_X1, OVERLAY_Y1, OVERLAY_W, OVERLAY_H = _overlay["x1"], _overlay["y1"], _overlay["w"], _overlay["h"]

    _insertroi = _coerce_rect(FCFG.get("insert_roi", _DEFAULT["insert_roi"]), _DEFAULT["insert_roi"])
    INSERT_ROI_X1, INSERT_ROI_Y1, INSERT_ROI_W, INSERT_ROI_H = _insertroi["x1"], _insertroi["y1"], _insertroi["w"], _insertroi["h"]

    _upg = _coerce_int_tuple2(FCFG.get("upgrade_btn", _DEFAULT["upgrade_btn"]), _DEFAULT["upgrade_btn"])
    UPGRADE_BTN_X, UPGRADE_BTN_Y = _upg

    ITEM_POSITIONS = _items_list(FCFG)

    _swipe = _swipe_dict(FCFG)
    TRAY_SWIPE_X, TRAY_SWIPE_Y, TRAY_SWIPE_DY, TRAY_SWIPE_MS = _swipe["x"], _swipe["y"], _swipe["dy"], _swipe["ms"]
