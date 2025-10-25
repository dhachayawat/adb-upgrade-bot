# app/config_api.py
from __future__ import annotations
from flask import Blueprint, jsonify, request, send_file
import cv2, numpy as np
import tempfile, os
import time
from typing import Optional
import json
from pathlib import Path

from .config_store import ConfigStore, Device
from .summary_store import SummaryStore
from .core.adb_adapter import ADBAdapter, ADBError
from . import rtlog as LOG

RUN_MODE = os.getenv("RUN_MODE", "thread").lower()
if RUN_MODE == "mp":
    from .core.mp_manager import MPDeviceManager as DeviceManager
else:
    from .core.manager import DeviceManager

bp_api = Blueprint("config_api", __name__, url_prefix="/api")

_cfg_store: Optional[ConfigStore] = None
_mgr: Optional[DeviceManager] = None
_summary: Optional[SummaryStore] = None
_weblog_q = None

def init_api(step_fn=None) -> None:
    global _cfg_store, _mgr, _summary, _weblog_q
    if _cfg_store is None:
        _cfg_store = ConfigStore()
    if _summary is None:
        _summary = SummaryStore()

    if _mgr is None:
        if RUN_MODE == "mp":
            _mgr = DeviceManager(_cfg_store)
            if _weblog_q is None:
                try:
                    _weblog_q = LOG.start_mp_bridge()
                except Exception as e:
                    LOG.w(f"[config_api] start_mp_bridge failed: {e}")
                    _weblog_q = None
            try:
                if hasattr(_mgr, "set_weblog_queue"):
                    _mgr.set_weblog_queue(_weblog_q)
            except Exception as e:
                LOG.w(f"[config_api] set_weblog_queue failed: {e}")
        else:
            _mgr = DeviceManager(_cfg_store, step_fn=step_fn)
    else:
        if RUN_MODE != "mp" and step_fn is not None:
            try:
                _mgr.set_step_fn(step_fn)
            except Exception:
                pass
        if RUN_MODE == "mp" and hasattr(_mgr, "set_weblog_queue"):
            try:
                if _weblog_q is None:
                    _weblog_q = LOG.start_mp_bridge()
                _mgr.set_weblog_queue(_weblog_q)
            except Exception as e:
                LOG.w(f"[config_api] set_weblog_queue (post-init) failed: {e}")

@bp_api.get("/devices")
def api_devices():
    init_api()
    return jsonify(_mgr.list_devices())

@bp_api.get("/devices/<device_id>/status")
def api_device_status(device_id: str):
    init_api()
    ctrl = _mgr.get(device_id)
    if not ctrl:
        return jsonify({"error": "not found"}), 404
    return jsonify(ctrl.status())

@bp_api.post("/devices/<device_id>/step-once")
def api_device_step_once(device_id: str):
    init_api()
    if RUN_MODE == "mp":
        return jsonify({"error": "unsupported in RUN_MODE=mp"}), 409
    ctrl = _mgr.get(device_id)
    if not ctrl:
        return jsonify({"error": "not found"}), 404
    try:
        from app.worker_step import worker_step
        res = worker_step(ctrl) or {}
        return jsonify({"ok": True, "out": res})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp_api.post("/devices/<device_id>/tap")
def api_device_tap(device_id: str):
    init_api()
    ctrl = _mgr.get(device_id)
    if not ctrl:
        return jsonify({"error":"not found"}), 404
    body = request.get_json(silent=True) or {}
    x = int(body.get("x", 0)); y = int(body.get("y", 0))
    try:
        dev_serial = getattr(ctrl, "device", device_id)
        adb = ADBAdapter(dev_serial)
        adb.ensure_connected()
        if hasattr(adb, "stay_awake"):
            adb.stay_awake()
        adb.tap(x, y)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@bp_api.get("/devices/<device_id>/shot")
def api_device_shot(device_id: str):
    init_api()
    ctrl = _mgr.get(device_id)
    if not ctrl:
        return jsonify({"error": "not found"}), 404
    save_q = request.args.get("save", "0")
    want_save = (os.getenv("DEBUG", "0") == "1" or os.getenv("SAVE_SCREENCAP", "0") == "1" or save_q == "1")
    try:
        dev_serial = getattr(ctrl, "device", device_id)
        adb = ADBAdapter(dev_serial)
        adb.ensure_connected()
        img = adb.screencap()
        ok, buf = cv2.imencode(".png", img)
        if not ok:
            return jsonify({"error": "encode failed"}), 500
        saved_path = None
        if want_save:
            try:
                base_dir = os.getenv("DEBUG_DIR") or os.getenv("CACHE_DIR", "/app/cache")
                os.makedirs(base_dir, exist_ok=True)
                ts = time.strftime("%Y%m%d-%H%M%S")
                prefix = os.getenv("SCREENCAP_PREFIX", "shot")
                fname = f"{prefix}-{device_id}-{ts}.png"
                fpath = os.path.join(base_dir, fname)
                with open(fpath, "wb") as f:
                    f.write(buf.tobytes())
                saved_path = fpath
            except Exception as e:
                LOG.w(f"[{device_id}] บันทึกสกรีนช็อตไม่สำเร็จ: {e}")
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
        try:
            tmp.write(buf.tobytes()); tmp.flush(); tmp.close()
            resp = send_file(tmp.name, mimetype="image/png", as_attachment=False)
            if saved_path:
                resp.headers["X-Saved-Path"] = saved_path
            return resp
        finally:
            try: os.remove(tmp.name)
            except: pass
    except ADBError as e:
        return jsonify({"error": f"adb: {str(e)}"}), 502
    except Exception as e:
        return jsonify({"error": f"internal: {str(e)}"}), 500

@bp_api.post("/devices/<device_id>/start")
def api_device_start(device_id: str):
    init_api()
    ctrl = _mgr.get(device_id)
    if not ctrl:
        return jsonify({"error": "not found"}), 404
    try:
        dev_serial = getattr(ctrl, "device", device_id)
        adb = ADBAdapter(dev_serial); adb.ensure_connected()
    except Exception as e:
        return jsonify({"error": "adb", "message": str(e), "status": ctrl.status()}), 502
    try:
        ok = _mgr.start(device_id)
        time.sleep(0.1)
        alive = bool(getattr(_mgr, "is_alive", None) and _mgr.is_alive(device_id)) \
                or bool(getattr(ctrl, "thread", None) and ctrl.thread.is_alive())
        return jsonify({"ok": bool(ok), "alive": alive, "status": ctrl.status()})
    except RuntimeError as e:
        return jsonify({"error": "conflict", "message": str(e), "status": ctrl.status()}), 409
    except Exception as e:
        import traceback, io
        tb = io.StringIO(); traceback.print_exc(file=tb)
        return jsonify({"error": "internal", "message": str(e), "traceback": tb.getvalue(), "status": ctrl.status()}), 500

@bp_api.post("/devices/<device_id>/pause")
def api_device_pause(device_id: str):
    init_api()
    ctrl = _mgr.get(device_id)
    if not ctrl:
        return jsonify({"error": "not found"}), 404
    try:
        ok = _mgr.pause(device_id)
        return jsonify({"ok": bool(ok), "status": ctrl.status()})
    except RuntimeError as e:
        return jsonify({"error": "conflict", "message": str(e), "status": ctrl.status()}), 409
    except Exception as e:
        return jsonify({"error": "internal", "message": str(e)}), 500

@bp_api.post("/devices/<device_id>/resume")
def api_device_resume(device_id: str):
    init_api()
    ctrl = _mgr.get(device_id)
    if not ctrl:
        return jsonify({"error": "not found"}), 404
    try:
        ok = _mgr.resume(device_id)
        return jsonify({"ok": bool(ok), "status": ctrl.status()})
    except RuntimeError as e:
        return jsonify({"error": "conflict", "message": str(e), "status": ctrl.status()}), 409
    except Exception as e:
        return jsonify({"error": "internal", "message": str(e)}), 500

@bp_api.post("/devices/<device_id>/stop")
def api_device_stop(device_id: str):
    init_api()
    ctrl = _mgr.get(device_id)
    if not ctrl:
        return jsonify({"error": "not found"}), 404
    try:
        ok = _mgr.stop(device_id)
        return jsonify({"ok": bool(ok), "status": ctrl.status()})
    except RuntimeError as e:
        return jsonify({"error": "conflict", "message": str(e), "status": ctrl.status()}), 409
    except Exception as e:
        return jsonify({"error": "internal", "message": str(e)}), 500

@bp_api.put("/devices/<device_id>/target-level")
def api_device_target_level(device_id: str):
    init_api()
    body = request.get_json(silent=True) or {}
    tl = body.get("target_level")
    persist = bool(body.get("persist", False))
    if not isinstance(tl, int):
        return jsonify({"error": "target_level must be int"}), 400
    try:
        _mgr.set_target_level(device_id, tl, persist=persist)
        return jsonify({"ok": True, "target_level": tl, "persisted": persist})
    except KeyError:
        return jsonify({"error": "not found"}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

@bp_api.post("/devices")
def api_devices_upsert():
    init_api()
    body = request.get_json(silent=True) or {}
    try:
        dev = Device(
            id=str(body["id"]),
            name=str(body["name"]),
            device=str(body["device"]),
            target_level=int(body.get("target_level", 5)),
        )
        _mgr.upsert_device(dev)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@bp_api.delete("/devices/<device_id>")
def api_devices_delete(device_id: str):
    init_api()
    try:
        ok = _mgr.delete_device(device_id)
        return jsonify({"ok": ok})
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 409

@bp_api.get("/devices/<device_id>/logs")
def api_device_logs(device_id: str):
    init_api()
    lines = int(request.args.get("lines", "400"))
    ctrl = _mgr.get(device_id)
    if not ctrl:
        return jsonify({"error":"not found"}), 404

    # 1) ลองอ่านจากไฟล์ต่อ-device ก่อน
    try:
        from . import rtlog as LOG
        txt = LOG.get_device_text(device_id, lines=lines)
        if txt.strip():
            return jsonify({"text": txt})
    except Exception:
        pass

    # 2) พยายามดึง log จาก controller ถ้ามี
    try:
        if hasattr(ctrl, "log_tail"):
            text = ctrl.log_tail(lines)
            return jsonify({"text": text})
        if hasattr(ctrl, "rtlog_text"):
            t = ctrl.rtlog_text()
            rows = t.splitlines()[-lines:]
            return jsonify({"text": "\n".join(rows)})
    except Exception:
        pass

    # 3) Fallback: global text buffer
    text = LOG.get_text(lines)
    return jsonify({"text": text})


@bp_api.get("/devices/<device_id>/weblogs")
def api_device_weblogs(device_id: str):
    """
    คืน rich logs (HTML) สำหรับอุปกรณ์ที่ระบุ
    รูปแบบ: [{ts, dev, html, lvl}, ...]
    - มี fallback: ถ้ากรองแล้วว่าง จะคืนทั้งบัฟเฟอร์เพื่อช่วยดีบัก
    """
    init_api()
    lines = int(request.args.get("lines", "400") or 400)
    try:
        data = LOG.web_dump(lines=lines, device=device_id)
        if not data:
            # fallback: ไม่กรอง — เพื่อให้เห็นว่าจริง ๆ มีล็อกไหม
            data = LOG.web_dump(lines=lines, device=None)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp_api.delete("/devices/<device_id>/weblogs")
def api_device_weblogs_clear(device_id: str):
    init_api()
    try:
        removed = LOG.web_clear(device=device_id)
        return jsonify({"ok": True, "removed": removed})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp_api.get("/dashboard")
def api_dashboard():
    init_api()
    return jsonify(_mgr.dashboard())

@bp_api.get("/summary")
def api_summary():
    init_api()
    days = int(request.args.get("days", "7"))
    return jsonify({"days": _summary.get_days(days)})

@bp_api.get("/logs")
def api_logs():
    return jsonify({"text": LOG.get_text(400)})

@bp_api.post("/devices/<device_id>/snap")
def api_device_snap(device_id: str):
    init_api()
    ctrl = _mgr.get(device_id)
    if not ctrl:
        return jsonify({"error":"not found"}), 404
    from datetime import datetime
    try:
        dev_serial = getattr(ctrl, "device", device_id)
        adb = ADBAdapter(dev_serial)
        adb.ensure_connected()
        img = adb.screencap()
        ok, buf = cv2.imencode(".png", img)
        if not ok:
            return jsonify({"error":"encode failed"}), 500
        cache_dir = os.getenv("CACHE_DIR", "/app/cache")
        os.makedirs(cache_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        fname = f"snap-{device_id}-{ts}.png"
        fpath = os.path.join(cache_dir, fname)
        with open(fpath, "wb") as f:
            f.write(buf.tobytes())
        return jsonify({"ok": True, "path": fpath, "filename": fname})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp_api.post("/devices/<device_id>/reconnect")
def api_device_reconnect(device_id: str):
    init_api()
    ctrl = _mgr.get(device_id)
    if not ctrl:
        return jsonify({"error":"not found"}), 404
    try:
        dev_serial = getattr(ctrl, "device", device_id)
        adb = ADBAdapter(dev_serial); adb.ensure_connected()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 502

def _cfg_path() -> Path:
    return Path(os.getenv("CONFIG_DEFAULTS_FILE", "/app/data/config/config.json"))

def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2); f.write("\n")
    tmp.replace(path)

@bp_api.get("/config")
def api_config_get():
    p = _cfg_path()
    return jsonify(_read_json(p))

@bp_api.post("/save-config")
def api_config_save():
    body = request.get_json(silent=True) or {}
    try:
        _write_json_atomic(_cfg_path(), body)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@bp_api.post("/clear-config")
def api_config_clear():
    try:
        _write_json_atomic(_cfg_path(), {})
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
