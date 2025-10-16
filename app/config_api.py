# app/config_api.py
from flask import Blueprint, jsonify, request
import base64, io, time, os, json, cv2
from app import config as C
from app import cv_utils as CV
from app import controller as CTRL
from app import rtlog as LOG

bp_api = Blueprint("config_api", __name__, url_prefix="/api")

# ---------- Config CRUD ----------
@bp_api.get("/config")
def api_config():
    cfg = C.export_schema()
    return jsonify(cfg)

@bp_api.post("/save-config")
def api_save():
    try:
        data = request.get_json(force=True)
        path = C.CONFIG_FILE
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        C.reload()
        LOG.tee("CONFIG: saved & reloaded")
        return jsonify({"ok": True, "path": path})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

@bp_api.post("/clear-config")
def api_clear():
    try:
        path = C.CONFIG_FILE
        if os.path.isfile(path):
            os.remove(path)
        C.reload()
        LOG.tee("CONFIG: cleared & reloaded (defaults)")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

# ---------- Screenshot ----------
@bp_api.get("/shot")
def api_shot():
    img = CV.screencap_bgr()
    h, w = img.shape[:2]
    _, buf = cv2.imencode(".png", img)
    b64 = base64.b64encode(buf.tobytes()).decode("ascii")
    return jsonify({"w": w, "h": h, "image_b64": b64})

@bp_api.post("/snap")
def api_snap():
    try:
        img = CV.screencap_bgr()
        os.makedirs(C.DEBUG_DIR, exist_ok=True)
        path = time.strftime(f"{C.DEBUG_DIR}/snap_%Y%m%d-%H%M%S.png")
        cv2.imwrite(path, img)
        LOG.tee(f"SNAP saved: {path}")
        return jsonify({"ok": True, "path": path})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

# ---------- Bot control ----------
@bp_api.post("/start")
def api_start():
    ok, msg = CTRL.start()
    return jsonify({"ok": ok, "message": msg, "status": CTRL.status()})

@bp_api.post("/stop")
def api_stop():
    ok, msg = CTRL.stop()
    return jsonify({"ok": ok, "message": msg, "status": CTRL.status()})

@bp_api.post("/pause")
def api_pause():
    ok, msg = CTRL.pause_resume()
    return jsonify({"ok": ok, "message": msg, "status": CTRL.status()})

@bp_api.get("/status")
def api_status():
    return jsonify(CTRL.status())

# ---------- Logs ----------
@bp_api.get("/logs")
def api_logs():
    return jsonify({"text": LOG.get_text(400)})
