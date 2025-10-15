import os, ast, json

# =========================
# Config file (โหลดก่อน ENV)
# =========================
CONFIG_FILE = os.getenv("CONFIG_FILE", "/app/cache/debug/runtime_config.json")

def _load_config_file(path: str):
    if not path: return {}
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"[config] WARN: load {path} failed: {e}")
    return {}

_CFG = _load_config_file(CONFIG_FILE)

def _get(dct, keys, default=None):
    cur = dct
    try:
        for k in (keys if isinstance(keys,(list,tuple)) else [keys]):
            cur = cur[k]
        return cur
    except Exception:
        return default

def _env_or(default_key, env_key, default_val):
    return type(default_val)(os.getenv(env_key, str(default_val)))

def _cfg_env(keys, env_key, default_val):
    v = _get(_CFG, keys, None)
    if v is not None:
        try:
            return type(default_val)(v)
        except Exception:
            return v
    return _env_or(keys if isinstance(keys,str) else keys[-1], env_key, default_val)

# =========================
# ADB / Device
# =========================
DEVICE = os.getenv("DEVICE", "host.docker.internal:5605")

# =========================
# Upgrade Slot (ตำแหน่งช่องอัปเกรด)
# =========================
USE_ABS_SLOT_CENTER = True
SLOT_CENTER_X = _cfg_env(("slot_center",0), "SLOT_CENTER_X", 1059)
SLOT_CENTER_Y = _cfg_env(("slot_center",1), "SLOT_CENTER_Y", 408)
SLOT_ROI_W    = _cfg_env(("slot_roi",0),    "SLOT_ROI_W",    90)
SLOT_ROI_H    = _cfg_env(("slot_roi",1),    "SLOT_ROI_H",    90)

# (สำรอง dX/dY)
UPGRADE_SLOT_DX    = float(os.getenv("UPGRADE_SLOT_DX", "77.4"))
UPGRADE_SLOT_DY    = float(os.getenv("UPGRADE_SLOT_DY", "74.8"))
UPGRADE_SLOT_ROI_W = int(os.getenv("UPGRADE_SLOT_ROI_W", "120"))
UPGRADE_SLOT_ROI_H = int(os.getenv("UPGRADE_SLOT_ROI_H", "120"))

# =========================
# Insert button (CV ROI เท่านั้น)
# =========================
INSERT_USE_CV = True
INSERT_ROI_X1 = _cfg_env(("insert_roi","x1"), "INSERT_ROI_X1", 480)
INSERT_ROI_Y1 = _cfg_env(("insert_roi","y1"), "INSERT_ROI_Y1", 560)
INSERT_ROI_W  = _cfg_env(("insert_roi","w"),  "INSERT_ROI_W",  420)
INSERT_ROI_H  = _cfg_env(("insert_roi","h"),  "INSERT_ROI_H",  140)
CONF_INSERT_THR = float(os.getenv("CONF_INSERT_THR", "0.83"))

# ดีเลย์ “ก่อนจับภาพเพื่อหาใส่ลง” + “เวลาอนุญาตให้หาใส่ลงสูงสุด”
INSERT_FIND_DELAY_SEC    = _cfg_env(("insert","find_delay"), "INSERT_FIND_DELAY_SEC", 0.25)
INSERT_FIND_TIMEOUT_SEC  = _cfg_env(("insert","find_timeout"), "INSERT_FIND_TIMEOUT_SEC", 5.0)

# หน่วงหลังแตะไอเทมก่อนเริ่มหาใส่ลง
PRE_INSERT_DELAY = _cfg_env(("insert","pre_delay"), "PRE_INSERT_DELAY", 0.8)

# =========================
# Upgrade button
# =========================
UPGRADE_BTN_X = _cfg_env(("upgrade_btn",0), "UPGRADE_BTN_X", 844)
UPGRADE_BTN_Y = _cfg_env(("upgrade_btn",1), "UPGRADE_BTN_Y", 626)

# =========================
# Overlay detect (+5 / fail / success)
# =========================
CONF_PLUS5_THR       = float(os.getenv("CONF_PLUS5_THR", "0.83"))
CONF_FAIL_THR        = float(os.getenv("CONF_FAIL_THR", "0.80"))
CONF_SUCCESS_THR     = float(os.getenv("CONF_SUCCESS_THR", "0.80"))
POST_UPGRADE_WAIT_SEC= float(os.getenv("POST_UPGRADE_WAIT_SEC", "2.0"))

# ── Overlay ROI (absolute preferred) ──
OVERLAY_USE_ABS  = True
OVERLAY_X1       = _cfg_env(("overlay_abs","x1"), "OVERLAY_X1", 720)
OVERLAY_Y1       = _cfg_env(("overlay_abs","y1"), "OVERLAY_Y1", 170)
OVERLAY_W        = _cfg_env(("overlay_abs","w"),  "OVERLAY_W",  560)
OVERLAY_H        = _cfg_env(("overlay_abs","h"),  "OVERLAY_H",  320)

# (centered mode backup)
FAIL_ROI_W       = int(os.getenv("FAIL_ROI_W", "420"))
FAIL_ROI_H       = int(os.getenv("FAIL_ROI_H", "220"))
FAIL_CHECK_MS    = int(os.getenv("FAIL_CHECK_MS", "900"))

# =========================
# Slot empty (แตกสลายตรวจช่องว่าง)
# =========================
CONF_SLOT_EMPTY_THR    = float(os.getenv("CONF_SLOT_EMPTY_THR", "0.85"))
CONF_SLOT_EMPTY_UNSURE = float(os.getenv("CONF_SLOT_EMPTY_UNSURE", "0.75"))

# =========================
# Timing
# =========================
CLICK_DELAY_MIN      = float(os.getenv("CLICK_DELAY_MIN", "0.15"))
CLICK_DELAY_MAX      = float(os.getenv("CLICK_DELAY_MAX", "0.30"))
UPGRADE_COOLDOWN_MIN = float(os.getenv("UPGRADE_COOLDOWN_MIN", "0.90"))
UPGRADE_COOLDOWN_MAX = float(os.getenv("UPGRADE_COOLDOWN_MAX", "1.40"))
MAX_UPGRADE_CLICKS_PER_ITEM = int(os.getenv("MAX_UPGRADE_CLICKS_PER_ITEM", "40"))
FAIL_RETRY_DELAY_SEC = float(os.getenv("FAIL_RETRY_DELAY_SEC", "1.5"))
EMPTY_CONFIRM_MS     = int(os.getenv("EMPTY_CONFIRM_MS", "1200"))
EMPTY_NEED_FRAMES    = int(os.getenv("EMPTY_NEED_FRAMES", "2"))
MAX_ITEM_TIME_SEC    = int(os.getenv("MAX_ITEM_TIME_SEC", "25"))

# =========================
# Tray Swipe
# =========================
TRAY_SWIPE_X  = _cfg_env(("swipe","x"),  "TRAY_SWIPE_X", 585)
TRAY_SWIPE_Y  = _cfg_env(("swipe","y"),  "TRAY_SWIPE_Y", 225)
TRAY_SWIPE_DY = _cfg_env(("swipe","dy"), "TRAY_SWIPE_DY", -85)
TRAY_SWIPE_MS = _cfg_env(("swipe","ms"), "TRAY_SWIPE_MS", 800)

# =========================
# Misc + Cache/Debug
# =========================
TEMPLATES_DIR   = os.getenv("TEMPLATES_DIR", "templates")
DEBUG           = (os.getenv("DEBUG", "0") == "1")
CACHE_DIR       = os.getenv("CACHE_DIR", "/app/cache")
DEBUG_DIR       = os.getenv("DEBUG_DIR", "/app/cache/debug")
SAVE_SCREENCAP  = os.getenv("SAVE_SCREENCAP", "1") == "1"
SCREENCAP_PREFIX= os.getenv("SCREENCAP_PREFIX", "adb")
SCREENCAP_KEEP  = int(os.getenv("SCREENCAP_KEEP", "120"))

# ----- OCR for +N badge -----
OCR_BADGE_MIN_CONF = float(os.getenv("OCR_BADGE_MIN_CONF", "60"))  # 0..100
# ROI ย่อยบริเวณมุมขวาบนของช่องอัปเกรด (ภายใน slot_roi)
BADGE_ROI_W = int(os.getenv("BADGE_ROI_W", "34"))
BADGE_ROI_H = int(os.getenv("BADGE_ROI_H", "26"))

# ถ้า OCR ไม่มั่นใจ จะ fallback ไปเทมเพลตได้
CONF_BADGE5_THR = float(os.getenv("CONF_BADGE5_THR", "0.68"))


# =========================
# Inventory Positions
# =========================
def _parse_item_positions_from_env():
    raw = os.getenv("ITEM_POSITIONS")
    if not raw: return None
    try:
        val = ast.literal_eval(raw)
        if (isinstance(val, (list, tuple)) and len(val) == 6
            and all(isinstance(p, (list, tuple)) and len(p)==2 for p in val)):
            return [(int(p[0]), int(p[1])) for p in val]
    except Exception:
        pass
    print("WARN: ITEM_POSITIONS parse failed. Falling back to ITEM_POS_* or defaults")
    return None

if "items" in _CFG and isinstance(_CFG["items"], list) and len(_CFG["items"])==6:
    ITEM_POSITIONS = [(int(p[0]), int(p[1])) for p in _CFG["items"]]
else:
    _item_positions = _parse_item_positions_from_env()
    if _item_positions is not None:
        ITEM_POSITIONS = _item_positions
    else:
        ITEM_POSITIONS = [
            (int(os.getenv("ITEM_POS_1_X", "285")), int(os.getenv("ITEM_POS_1_Y", "170"))),
            (int(os.getenv("ITEM_POS_2_X", "345")), int(os.getenv("ITEM_POS_2_Y", "170"))),
            (int(os.getenv("ITEM_POS_3_X", "405")), int(os.getenv("ITEM_POS_3_Y", "170"))),
            (int(os.getenv("ITEM_POS_4_X", "465")), int(os.getenv("ITEM_POS_4_Y", "170"))),
            (int(os.getenv("ITEM_POS_5_X", "525")), int(os.getenv("ITEM_POS_5_Y", "170"))),
            (int(os.getenv("ITEM_POS_6_X", "585")), int(os.getenv("ITEM_POS_6_Y", "170"))),
        ]
