import os, time, cv2
from app import config as C
from app import cv_utils as CV
from app.geometry import Geo, ensure_geo

def _ensure_dir(p): os.makedirs(p, exist_ok=True)
def cross(img,x,y,c=(0,255,0),r=8,t=2):
    x=int(x); y=int(y); cv2.line(img,(x-r,y),(x+r,y),c,t,cv2.LINE_AA); cv2.line(img,(x,y-r),(x,y+r),c,t,cv2.LINE_AA)
def label(img, text, x, y, fg=(255,255,255), bg=(0,0,0)):
    x=int(x); y=int(y); fs=0.5; th=1; pad=4
    (tw, tht), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, fs, th)
    cv2.rectangle(img,(x,y-tht-2*pad),(x+tw+2*pad,y),bg,-1)
    cv2.putText(img,text,(x+pad,y-pad),cv2.FONT_HERSHEY_SIMPLEX,fs,fg,th,cv2.LINE_AA)

def calibrate_once(save_name="calib_preview.png", tag="calib"):
    out_dir = C.DEBUG_DIR; _ensure_dir(out_dir)
    save_to = os.path.join(out_dir, save_name)
    img = CV.screencap_bgr(save_tag=tag); H,W = img.shape[:2]
    geo = Geo(); ensure_geo(geo, img)
    out = img.copy()

    # slot center & roi
    cx, cy = geo.slot_center; rx, ry, rw, rh = geo.slot_roi
    cross(out, cx, cy, (0,255,255),12,2); cv2.rectangle(out,(rx,ry),(rx+rw,ry+rh),(0,255,255),2)
    label(out, f"slot=({cx},{cy}) roi={rw}x{rh}", rx, max(ry-6,6), (0,0,0),(0,255,255))

    # insert ROI (CV) + base
    x1,y1,w,h = C.INSERT_ROI_X1, C.INSERT_ROI_Y1, C.INSERT_ROI_W, C.INSERT_ROI_H
    cv2.rectangle(out,(x1,y1),(x1+w,y1+h),(255,0,200),2)
    label(out, f"insert_roi=({x1},{y1},{w}x{h})", x1, max(y1-6,6),(255,255,255),(150,0,120))
    cross(out, C.INSERT_BASE_X, C.INSERT_BASE_Y,(255,0,255),12,2)
    label(out, f"insert_base=({C.INSERT_BASE_X},{C.INSERT_BASE_Y})", C.INSERT_BASE_X+10, C.INSERT_BASE_Y-10,(255,0,255),(30,0,40))

    # upgrade btn
    cross(out, C.UPGRADE_BTN_X, C.UPGRADE_BTN_Y,(0,128,255),12,2)
    label(out, f"upgrade=({C.UPGRADE_BTN_X},{C.UPGRADE_BTN_Y})", C.UPGRADE_BTN_X+10, C.UPGRADE_BTN_Y-10,(0,128,255),(10,40,60))

    # items
    for i,(px,py) in enumerate(C.ITEM_POSITIONS,1):
        cross(out, px, py,(0,255,0),10,2)
        label(out, f"#{i}({px},{py})", px+8, py-8,(0,60,0),(150,255,150))

    # overlay ROI abs/center
    if C.OVERLAY_USE_ABS and C.OVERLAY_W>0 and C.OVERLAY_H>0:
        ox1,oy1,ow,oh = C.OVERLAY_X1, C.OVERLAY_Y1, C.OVERLAY_W, C.OVERLAY_H
        cv2.rectangle(out,(ox1,oy1),(ox1+ow,oy1+oh),(0,200,255),2)
        label(out, f"overlay_abs=({ox1},{oy1},{ow}x{oh})", ox1, max(oy1-6,6),(0,0,0),(0,200,255))
    else:
        ow,oh = C.FAIL_ROI_W, C.FAIL_ROI_H
        ox1 = max(0, cx-ow//2); oy1 = max(0, cy-oh//2)
        cv2.rectangle(out,(ox1,oy1),(ox1+ow,oy1+oh),(255,255,0),2)
        label(out, f"overlay_roi={ow}x{oh}", ox1, max(oy1-6,6),(0,0,0),(255,255,0))

    # swipe
    sx,sy,dy,ms = C.TRAY_SWIPE_X, C.TRAY_SWIPE_Y, C.TRAY_SWIPE_DY, C.TRAY_SWIPE_MS
    cv2.arrowedLine(out,(sx,sy),(sx,sy+dy),(255,140,0),3,cv2.LINE_AA, tipLength=0.15)
    label(out, f"swipe ({sx},{sy})->({sx},{sy+dy}) {ms}ms", min(sx,sx)+6, min(sy,sy+dy)-8,(0,0,0),(255,180,50))

    label(out, f"screen={W}x{H}", 8, 20,(255,255,255),(50,50,50))
    label(out, time.strftime("%Y-%m-%d %H:%M:%S"), 8, 44,(255,255,255),(50,50,50))
    os.makedirs(out_dir, exist_ok=True); cv2.imwrite(save_to, out)
    print(f"[calibrate] saved -> {save_to} ({W}x{H})")

def main(): calibrate_once()
if __name__=="__main__": main()
