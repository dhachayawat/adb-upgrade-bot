import time
from app import cv_utils as CV
from app import config as C
from app.geometry import Geo, ensure_geo
from app.adb import tap
import cv2

# รองรับหลายสถานะของปุ่ม "ใส่ลง"
TEMPLATE_NAMES = ["insert.png", "insert_alt.png"]  # ใส่เพิ่มได้ตามต้องการ

def _slot_is_filled(geo: Geo) -> bool:
    """False=ว่าง(แตก) / True=ยังมีของ"""
    img = CV.screencap_bgr(save_tag="slotchk")
    x, y, w, h = geo.slot_roi
    crop = img[y:y+h, x:x+w]
    tpl = CV.read_tpl("slot_empty.png")
    # ช่องว่างใช้เทมเพลตเดียวพอ ไม่ต้อง multi-scale
    pt, score = CV.match_center_multiscale(crop, tpl, thr=C.CONF_SLOT_EMPTY_THR, scales=(1.0,))
    return (pt is None)

def _try_press_insert_once():
    """
    จับภาพ 1 ครั้งใน ROI แล้วลองทุกเทมเพลตของ 'ใส่ลง'
    ใช้ multi-scale กันสเกลเพี้ยนเล็กน้อย เลือกอันคะแนนดีที่สุดเกิน threshold
    """
    img = CV.screencap_bgr(save_tag="insert")
    H, W = img.shape[:2]
    x1 = max(0, C.INSERT_ROI_X1);  y1 = max(0, C.INSERT_ROI_Y1)
    x2 = min(W, x1 + C.INSERT_ROI_W); y2 = min(H, y1 + C.INSERT_ROI_H)
    roi = img[y1:y2, x1:x2]

    best_pt, best_score, best_name = None, -1.0, None

    for name in TEMPLATE_NAMES:
        tpl = CV.read_tpl(name)
        if tpl is None:
            continue
        pt, score = CV.match_center_multiscale(
            roi, tpl,
            thr=C.CONF_INSERT_THR,
            scales=(0.90, 0.95, 1.00, 1.05, 1.10),
            save_debug_tag="insert"  # บันทึก heatmap ไว้ debug
        )
        if score is not None and score > best_score:
            best_pt, best_score, best_name = pt, score, name

    if best_pt:
        gx, gy = x1 + best_pt[0], y1 + best_pt[1]
        print(f"[INSERT][CV] hit ({gx},{gy}) score={best_score:.3f} tpl={best_name} ROI=({x1},{y1},{C.INSERT_ROI_W}x{C.INSERT_ROI_H})")
        tap(gx, gy)
        return True

    if best_score < 0:
        best_score = 0.0
    print(f"[INSERT][CV] miss best={best_score:.3f} ROI=({x1},{y1},{C.INSERT_ROI_W}x{C.INSERT_ROI_H})")
    return False

def add_item_via_insert(item_xy, geo: Geo):
    """
    โฟลว์:
      1) tap ไอเทม
      2) รอ PRE_INSERT_DELAY + INSERT_FIND_DELAY_SEC (ให้ UI นิ่ง/tooltip หาย)
      3) วนหา 'ใส่ลง' ด้วย CV (ทุกเทมเพลต) ภายใน INSERT_FIND_TIMEOUT_SEC
      4) ตรวจว่าช่องอัปเกรดถูกเติมจริง → สำเร็จ
    """
    ix, iy = item_xy
    print(f"== ชิ้น: tap ({ix},{iy}) ==")
    tap(ix, iy)

    # รอให้ UI นิ่งก่อนหา
    if C.PRE_INSERT_DELAY > 0:
        time.sleep(C.PRE_INSERT_DELAY)
    if C.INSERT_FIND_DELAY_SEC > 0:
        time.sleep(C.INSERT_FIND_DELAY_SEC)

    # วนหาในช่วง timeout
    deadline = time.time() + C.INSERT_FIND_TIMEOUT_SEC
    while time.time() < deadline:
        if _try_press_insert_once():
            break
        time.sleep(max(0.15, C.INSERT_FIND_DELAY_SEC))
    else:
        print("[INSERT] ❌ ไม่พบปุ่ม 'ใส่ลง' ภายในเวลาที่กำหนด")
        return False

    # ตรวจว่าช่องถูกเติมจริง
    time.sleep(0.30)
    ensure_geo(geo, CV.screencap_bgr(save_tag="post_insert"))
    filled = _slot_is_filled(geo)
    if filled:
        print("ใส่ลงสำเร็จ ✅ (ช่องมีของ)")
        return True

    print("ใส่ลงไม่สำเร็จ ❌ (ยังว่าง)")
    return False
