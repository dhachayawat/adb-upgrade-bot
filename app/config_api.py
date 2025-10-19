# app/config_api.py
import base64
import io
import json
from flask import Blueprint, jsonify, request

from app import config as C
from app import controller as CTRL
from app import cv_utils as CV
from app import adb as ADB
from app import rtlog as LOG
from app import state as ST
import cv2

bp_api = Blueprint("api", __name__)

@bp_api.get("/api/status")
def api_status():
    # สถานะการทำงาน และสถานะไอเทมปัจจุบัน (สำหรับ UI)
    cur = ST.snapshot()
    return jsonify({
        "ok": True,
        "running": CTRL.is_running(),
        "paused": CTRL.is_paused(),
        "current": cur,
    })

@bp_api.get("/api/logs")
def api_logs():
    text = LOG.read_all() or ""
    return jsonify({"ok": True, "text": text})

@bp_api.get("/api/config")
def api_get_config():
    return jsonify(C.to_dict())

@bp_api.post("/api/save-config")
def api_save_config():
    try:
        data = request.get_json(force=True)
        # เขียนไฟล์คอนฟิกตาม path
        path = C.CONFIG_FILE
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "device": data.get("device", C.DEVICE),
                "slot_center": data.get("slot_center", [C.SLOT_CENTER_X, C.SLOT_CENTER_Y]),
                "slot_status": data.get("slot_status", [C.SLOT_STATUS_X, C.SLOT_STATUS_Y]),
                "slot_roi": data.get("slot_roi", [C.SLOT_ROI_W, C.SLOT_ROI_H]),
                "slot_status_roi": data.get("slot_status_roi", [C.SLOT_STATUS_ROI_W, C.SLOT_STATUS_ROI_H]),
                "overlay_abs": data.get("overlay_abs", {"x1":C.OVERLAY_X1,"y1":C.OVERLAY_Y1,"w":C.OVERLAY_W,"h":C.OVERLAY_H}),
                "insert_roi":  data.get("insert_roi",  {"x1":C.INSERT_ROI_X1,"y1":C.INSERT_ROI_Y1,"w":C.INSERT_ROI_W,"h":C.INSERT_ROI_H}),
                "upgrade_btn": data.get("upgrade_btn", [C.UPGRADE_BTN_X, C.UPGRADE_BTN_Y]),
                "items": data.get("items", [list(p) for p in C.ITEM_POSITIONS]),
                "swipe": data.get("swipe", {"x":C.TRAY_SWIPE_X,"y":C.TRAY_SWIPE_Y,"dy":C.TRAY_SWIPE_DY,"ms":C.TRAY_SWIPE_MS}),
            }, f, ensure_ascii=False, indent=2)
        # reload คอนฟิก
        C.reload()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@bp_api.post("/api/clear-config")
def api_clear_config():
    try:
        with open(C.CONFIG_FILE, "w", encoding="utf-8") as f:
            f.write("{}")
        C.reload()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@bp_api.get("/api/shot")
def api_shot():
    img = CV.screencap_bgr()
    _, buf = cv2.imencode(".png", img)
    b64 = base64.b64encode(buf.tobytes()).decode("ascii")
    h, w = img.shape[:2]
    return jsonify({"ok": True, "image_b64": b64, "w": w, "h": h})

@bp_api.post("/api/snap")
def api_snap():
    try:
        img = CV.screencap_bgr(save_tag="config")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@bp_api.post("/api/start")
def api_bot_start():
    ok, msg = CTRL.start()
    return jsonify({"ok": ok, "message": msg})

@bp_api.post("/api/stop")
def api_bot_stop():
    ok, msg = CTRL.stop()
    return jsonify({"ok": ok, "message": msg})

@bp_api.post("/api/pause")
def api_bot_pause():
    ok, msg = CTRL.pause()
    return jsonify({"ok": ok, "message": msg})

@bp_api.post("/api/resume")
def api_bot_resume():
    ok, msg = CTRL.resume()
    return jsonify({"ok": ok, "message": msg})
