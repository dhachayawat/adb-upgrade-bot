# app/worker_step.py
from __future__ import annotations
from typing import Dict, Any, Optional
import time
import cv2
import numpy as np

from .core.adb_adapter import ADBAdapter
from .config_store import ConfigStore
from .summary_store import SummaryStore
from . import rtlog as LOG

# โหลดค่า defaults จาก config.json หนึ่งครั้ง (ใช้ร่วมทุกอุปกรณ์ เพราะ resolution เท่ากัน)
_CFG = ConfigStore().load_defaults()
_SUMMARY: Optional[SummaryStore] = None
_DEVICE_CTX: Dict[str, dict] = {}  # คอนเท็กซ์ย่อยต่ออุปกรณ์ (state ภายในหลายสเต็ป)

def _summary() -> SummaryStore:
    global _SUMMARY
    if _SUMMARY is None:
        _SUMMARY = SummaryStore()
    return _SUMMARY

# --- helper: ดึงค่าพิกัด/ROIจาก config ---
def _pt(key: str, default=(0,0)):
    v = _CFG.get(key)
    return tuple(v) if isinstance(v, (list, tuple)) and len(v) == 2 else default

def _rect(key: str, default=(0,0,0,0)):
    r = _CFG.get(key)
    if isinstance(r, dict):
        x1,y1,w,h = r.get("x1",0), r.get("y1",0), r.get("w",0), r.get("h",0)
        return (int(x1), int(y1), int(w), int(h))
    elif isinstance(r, (list, tuple)) and len(r) == 4:
        x1,y1,w,h = r
        return (int(x1), int(y1), int(w), int(h))
    return default

def _items():
    its = _CFG.get("items") or []
    out = []
    for p in its:
        if isinstance(p, (list, tuple)) and len(p) == 2:
            out.append((int(p[0]), int(p[1])))
    return out

def _swipe():
    s = _CFG.get("swipe") or {}
    return dict(x=int(s.get("x",0)), y=int(s.get("y",0)), dy=int(s.get("dy",0)), ms=int(s.get("ms",300)))

_BREAK_LEVEL_MIN = int(_CFG.get("break_level_min", 4))

# --- OCR helpers (แบบเบา ๆ) ---
def _ocr_digits(img_bgr: np.ndarray) -> Optional[int]:
    """อ่านตัวเลขธรรมดาหรือในวงเล็บ จาก ROI status เล็กๆ"""
    try:
        import pytesseract
    except Exception:
        LOG.w("[OCR] ไม่พบ pytesseract (ข้ามการอ่านตัวเลข)")
        return None
    if img_bgr is None or img_bgr.size == 0:
        return None
    t0 = time.time()
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    gray = cv2.GaussianBlur(gray, (3,3), 0)
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY+cv2.THRESH_OTSU)
    cfg = r'--oem 1 --psm 7 -c tessedit_char_whitelist=0123456789[]()+-'
    text = (pytesseract.image_to_string(bw, config=cfg) or "").strip()
    # โกยเลขในรูป [n] หรือ +n
    digits = "".join(ch for ch in text if ch.isdigit())
    dt = int((time.time()-t0)*1000)
    LOG.i(f"[OCR] อ่านตัวเลขสถานะ: raw='{text}' -> digits='{digits}' ใช้เวลา {dt}ms")
    if digits.isdigit():
        try:
            return int(digits)
        except Exception:
            return None
    return None

def _ocr_th_success_fail(img_bgr: np.ndarray) -> Optional[str]:
    """OCR คำไทย คร่าวๆ: 'สำเร็จ' หรือ 'ล้มเหลว' จาก overlay"""
    try:
        import pytesseract
    except Exception:
        LOG.w("[OCR] ไม่พบ pytesseract (ข้ามการอ่านคำไทย)")
        return None
    if img_bgr is None or img_bgr.size == 0:
        return None
    t0 = time.time()
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY+cv2.THRESH_OTSU)
    cfg = r'--oem 1 --psm 7'
    text = (pytesseract.image_to_string(bw, config=cfg) or "").replace(" ", "")
    dt = int((time.time()-t0)*1000)
    LOG.i(f"[OCR] อ่านผลลัพธ์ overlay (ไทย): raw='{text}' ใช้เวลา {dt}ms")
    if "สำเร็จ" in text:
        return "success"
    if "ล้มเหลว" in text:
        return "fail"
    return None

# --- โทนสีร้อน/เย็น (cheap check) ---
def _tone_hot_or_cold(img_bgr: np.ndarray) -> Optional[str]:
    if img_bgr is None or img_bgr.size == 0:
        return None
    t0 = time.time()
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    H,S,V = cv2.split(hsv)
    # โซนเย็น (ฟ้า-ขาว) vs ร้อน (ส้ม-แดง) แบบหยาบ
    cold = cv2.inRange(hsv, (80,  30, 120), (130, 255, 255))  # ฟ้า
    white= cv2.inRange(hsv, (0,    0, 200), (179,  30, 255))  # ขาว
    hot1 = cv2.inRange(hsv, (0,   80, 120), (15,  255, 255))  # แดง-ส้ม
    hot2 = cv2.inRange(hsv, (160, 80, 120), (179, 255, 255))  # แดงอีกฝั่งวงล้อ
    cold_ratio = (np.count_nonzero(cold) + np.count_nonzero(white)) / max(1, img_bgr.size/3)
    hot_ratio  = (np.count_nonzero(hot1) + np.count_nonzero(hot2)) / max(1, img_bgr.size/3)
    dt = int((time.time()-t0)*1000)
    LOG.i(f"[TONE] cold={cold_ratio:.3f} hot={hot_ratio:.3f} ใช้เวลา {dt}ms")
    if cold_ratio > hot_ratio*1.2 and cold_ratio > 0.01:
        return "success"
    if hot_ratio > cold_ratio*1.2 and hot_ratio > 0.01:
        return "fail"
    return None

# --- คอนเท็กซ์ per-device ---
def _ctx(dev_id: str) -> dict:
    c = _DEVICE_CTX.get(dev_id)
    if not c:
        c = dict(stage="pick", item_idx=0, base_level=None, successes=0, last_action_ts=0.0)
        _DEVICE_CTX[dev_id] = c
        LOG.i(f"[{dev_id}] เริ่มคอนเท็กซ์ใหม่ stage=pick item_idx=0")
    return c

# --- crop helper ---
def _crop(img, rect):
    x1,y1,w,h = rect
    return img[y1:y1+h, x1:x1+w].copy()

def worker_step(controller) -> Dict[str, Any]:
    """
    สเต็ปทำงานจริง (รวบรัด เพื่อให้วิ่งได้):
    - pick: แตะไอเทม -> inspect
    - inspect: OCR slot status (อ่าน [n]) -> ตัดสินใจ insert หรือ skip
    - insert: แตะไอเทม + แตะ slot_center -> upgrade
    - upgrade: กดปุ่มอัปเกรด -> อ่าน overlay_abs (โทนสี→OCR) -> ตัดสิน success/fail(+break)
    """
    dev_id   = controller.id
    dev_name = controller.name
    dev_addr = controller.device
    target   = controller.target_level

    adb = getattr(controller, "adb", None)
    if adb is None:
        from .core.adb_adapter import ADBAdapter
        adb = ADBAdapter(controller.device)
        controller.adb = adb

    items = _items()
    slot_center = _pt("slot_center", (0,0))
    upgrade_btn = _pt("upgrade_btn", (0,0))
    overlay_abs = _rect("overlay_abs", (0,0,0,0))
    slot_status_roi = _rect("slot_status_roi", (0,0,0,0))
    insert_roi = _rect("insert_roi", (0,0,0,0))
    swipe_cfg = _swipe()

    c = _ctx(dev_id)
    out: Dict[str, Any] = {}

    # ---------- log คอนฟิกเฉพาะจุดสำคัญ ----------
    LOG.i(f"[{dev_id}] stage={c['stage']} item_idx={c['item_idx']} target=+{target} base={c.get('base_level')} succ={c.get('successes',0)}")

    # --------------- Stage machine ---------------
    if c["stage"] == "pick":
        # แตะไอเทมปัจจุบัน (ถ้าเกิน ให้สไลด์ถาดแล้ววน)
        if not items:
            LOG.w(f"[{dev_id}] ไม่พบรายการไอเทมใน config.items (ข้ามสเต็ป)")
            time.sleep(0.1)
            return {}
        idx = c["item_idx"] % len(items)
        ix,iy = items[idx]
        LOG.i(f"[{dev_id}] แตะไอเทม idx={idx} @({ix},{iy})")
        adb.tap(ix, iy)
        c["stage"] = "inspect"
        c["last_action_ts"] = time.time()
        return {}  # step เล็กๆ จบ

    if c["stage"] == "inspect":
        # หน่วงสั้นๆ ให้ UI นิ่งก่อนจับภาพ
        remain = 0.25 - (time.time() - c["last_action_ts"])
        if remain > 0:
            time.sleep(min(0.05, remain))
            return {}
        t0 = time.time()
        img = adb.screencap()
        dt = int((time.time()-t0)*1000)
        LOG.i(f"[{dev_id}] จับภาพสถานะ slot_status (ใช้เวลา {dt}ms)")

        roi = _crop(img, slot_status_roi) if slot_status_roi[2] and slot_status_roi[3] else img
        n = _ocr_digits(roi)
        LOG.i(f"[{dev_id}] OCR ระดับฐาน ปัจจุบันอ่านได้ n={n}")
        c["base_level"] = n if isinstance(n, int) else None

        # ตัดสินใจ: ไม่เจอ หรือ n<=4 -> insert; n>=5 -> skip
        if n is None or n <= 4:
            LOG.i(f"[{dev_id}] ตัดสินใจ INSERT (n={n})")
            c["stage"] = "insert"
            c["last_action_ts"] = time.time()
        else:
            LOG.i(f"[{dev_id}] ข้ามไอเทมนี้ (n={n} >= 5) → ไปชิ้นถัดไป")
            c["item_idx"] += 1
            if c["item_idx"] % len(items) == 0:
                # สไลด์ถาด (ถ้ามี)
                LOG.i(f"[{dev_id}] เลื่อนถาด x={swipe_cfg['x']} y={swipe_cfg['y']} dy={swipe_cfg['dy']} ms={swipe_cfg['ms']}")
                adb.swipe(swipe_cfg["x"], swipe_cfg["y"], dy=swipe_cfg["dy"], ms=swipe_cfg["ms"])
            c["stage"] = "pick"
        return {}

    if c["stage"] == "insert":
        idx = c["item_idx"] % len(items)
        ix,iy = items[idx]
        adb.tap(ix, iy)
        time.sleep(float(os.getenv("PRE_INSERT_DELAY", "0.8")))  # << ใส่คืนจากเวิร์กเกอร์เดิม
        sx,sy = slot_center
        adb.tap(sx, sy)
        c["stage"] = "upgrade"
        c["successes"] = 0
        c["last_action_ts"] = time.time()
        return {}

    if c["stage"] == "upgrade":
        # ตรวจว่าถึงเป้าหมายหรือยัง
        base = c["base_level"] or 0
        cur  = base + c["successes"]
        LOG.i(f"[{dev_id}] UPGRADE base={base} succ={c['successes']} cur={cur} / target=+{target}")

        if cur >= target:
            # สำเร็จครบเป้า -> นับ 1 ชิ้น และไปชิ้นถัดไป
            LOG.i(f"[{dev_id}] ถึงเป้าหมาย +{target} แล้ว → นับสำเร็จ 1 ชิ้น และไปชิ้นถัดไป")
            c["item_idx"] += 1
            c["stage"] = "pick"
            return {"done_item": True}

        # ยังไม่ถึงเป้า -> คลิกอัปเกรด
        ux,uy = upgrade_btn
        LOG.i(f"[{dev_id}] แตะปุ่มอัปเกรด @({ux},{uy})")
        adb.tap(ux, uy)
        out["upgrade_clicks"] = out.get("upgrade_clicks", 0) + 1

        # รอเอฟเฟกต์นิ่ง
        time.sleep(0.30)

        # จับ overlay แล้ววิเคราะห์: โทนสี -> OCR
        t0 = time.time()
        img = adb.screencap()
        cap_ms = int((time.time()-t0)*1000)
        LOG.i(f"[{dev_id}] จับภาพ overlay_abs (ใช้เวลา {cap_ms}ms)")
        ovr = _crop(img, overlay_abs) if overlay_abs[2] and overlay_abs[3] else img

        # quick tone
        tone = _tone_hot_or_cold(ovr)
        if tone:
            LOG.i(f"[{dev_id}] โทนสีสรุปเบื้องต้น: {tone}")
        verdict = None
        if tone == "success":
            verdict = "success"
        elif tone == "fail":
            verdict = "fail"
        else:
            # ลอง OCR คำไทย
            sf = _ocr_th_success_fail(ovr)
            if sf in ("success", "fail"):
                verdict = sf

        if verdict == "success":
            c["successes"] += 1
            out["success_clicks"] = out.get("success_clicks", 0) + 1
            LOG.i(f"[{dev_id}] ผลลัพธ์: สำเร็จ (+1) → success={c['successes']}")
            return out

        if verdict == "fail":
            # ตรวจว่าถือว่า "แตก" ไหม: แตกตั้งแต่ current>=BREAK_MIN
            cur = base + c["successes"]  # level ก่อนพยายาม
            attempt_level = cur + 1
            LOG.i(f"[{dev_id}] ผลลัพธ์: ล้มเหลว (กำลังพยายามไป +{attempt_level})")
            if cur >= _BREAK_LEVEL_MIN:
                # บันทึก summary รายวัน ตามนิยาม: แตกตอนพยายามไป level X -> นับ X
                _summary().add_break(controller.id, attempt_level)
                LOG.i(f"[{dev_id}] บันทึกสถิติแตกที่ระดับ +{attempt_level} (>= min { _BREAK_LEVEL_MIN }) และไปชิ้นถัดไป")
                # ไปชิ้นถัดไป
                c["item_idx"] += 1
                c["stage"] = "pick"
                out["break_at_level"] = attempt_level
                return out
            else:
                out["fail_nonbreak"] = True
                LOG.i(f"[{dev_id}] ล้มเหลว (ยังไม่ถึงเกณฑ์นับแตก min={_BREAK_LEVEL_MIN}) → จะลองใหม่ชิ้นเดิม")
                # ยังอยู่ stage upgrade ต่อ (อาจลองใหม่ชิ้นเดิม)
                return out

        LOG.i(f"[{dev_id}] ยังสรุปผลไม่ได้ (tone/OCR ไม่ชัด) → รออีกนิดแล้วตรวจซ้ำ")
        time.sleep(0.15)
        return out

    # fallback
    LOG.w(f"[{dev_id}] พบ stage ไม่รู้จัก: {c['stage']} → รีเซ็ตเป็น pick")
    c["stage"] = "pick"
    return {}

def worker_loop(ctrl, step_fn, sleep_sec: float = 0.15):
    """
    ลูปหลักของอุปกรณ์: เรียก step_fn(ctrl) ซ้ำๆ จนกว่าจะถูก stop/pause
    ใส่ heartbeat และจับ exception เพื่อไม่ให้ thread ตายเงียบ
    """
    LOG.i(f"[{ctrl.id}] loop start")
    try:
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

            # heartbeat ทุก ~3 วินาที
            if int(ctrl.last_tick) % 3 == 0:
                LOG.i(f"[{ctrl.id}] heartbeat items={getattr(ctrl,'items_upgraded_done',0)} target=+{getattr(ctrl,'target_level',0)}")

            time.sleep(sleep_sec)
    finally:
        LOG.i(f"[{ctrl.id}] loop exit (state={ctrl.state})")
