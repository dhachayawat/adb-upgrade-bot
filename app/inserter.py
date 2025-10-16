# app/inserter.py
import time
from app import config as C
from app import adb as ADB
from app import cv_utils as CV
from app import rtlog as LOG
import cv2

def _roi(img, r):
    x1,y1,w,h = r["x1"], r["y1"], r["w"], r["h"]
    return img[y1:y1+h, x1:x1+w], (x1,y1)

def click_insert_via_cv(wait_pre: float = 0.8) -> bool:
    """
    รอเล็กน้อย -> จับภาพ -> หา template 'insert' หรือ 'insert_alt' ใน INSERT ROI -> tap center
    """
    time.sleep(wait_pre)
    img = CV.screencap_bgr()
    roi, (ox,oy) = _roi(img, {"x1":C.INSERT_ROI_X1,"y1":C.INSERT_ROI_Y1,"w":C.INSERT_ROI_W,"h":C.INSERT_ROI_H})

    # ลอง main ก่อน
    pt, sc = CV.match_center_multiscale(
        roi,
        CV.read_tpl("insert.png"),
        thr=C.CONF_INSERT_THR,
        scales=(0.9, 1.0, 1.1),
        save_debug_tag=None
    )
    if not pt:
        # ลอง alt
        pt, sc = CV.match_center_multiscale(
            roi,
            CV.read_tpl("insert_alt.png"),
            thr=C.CONF_INSERT_THR,
            scales=(0.9, 1.0, 1.1),
            save_debug_tag=None
        )
    if not pt:
        LOG.tee("WARN: หา/กดปุ่ม 'ใส่ลง' ไม่เจอใน ROI")
        return False

    gx, gy = ox + pt[0], oy + pt[1]
    LOG.tee(f"[INSERT] click at ({gx},{gy})")
    ADB.tap(gx, gy)
    return True
