from dataclasses import dataclass
from app import config as C

@dataclass
class Geo:
    slot_center: tuple|None = None  # (cx,cy)
    slot_roi: tuple|None = None     # (x,y,w,h)

def slot_center_from_wh(w:int, h:int):
    cx = int(round(w*(C.UPGRADE_SLOT_DX/100.0)))
    cy = int(round(h*(C.UPGRADE_SLOT_DY/100.0)))
    return cx, cy

def slot_roi_from_center(cx:int, cy:int, w:int, h:int):
    # ถ้ามี override ขนาด ROI ให้ใช้ของ override
    rw = C.SLOT_ROI_W if C.USE_ABS_SLOT_CENTER else C.UPGRADE_SLOT_ROI_W
    rh = C.SLOT_ROI_H if C.USE_ABS_SLOT_CENTER else C.UPGRADE_SLOT_ROI_H
    x1 = max(0, cx - rw//2); y1 = max(0, cy - rh//2)
    x2 = min(w, x1 + rw);    y2 = min(h, y1 + rh)
    return (x1, y1, x2-x1, y2-y1)

def ensure_geo(geo: Geo, img):
    H,W = img.shape[:2]
    if C.USE_ABS_SLOT_CENTER:
        cx, cy = C.SLOT_CENTER_X, C.SLOT_CENTER_Y
    else:
        cx, cy = slot_center_from_wh(W, H)

    if geo.slot_center is None:
        geo.slot_center = (cx, cy)
    if geo.slot_roi is None:
        geo.slot_roi = slot_roi_from_center(cx, cy, W, H)
    return geo
