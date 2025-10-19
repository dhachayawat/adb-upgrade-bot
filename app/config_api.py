# app/config_api.py
from __future__ import annotations
from flask import Blueprint, jsonify, request, send_file
import cv2, numpy as np
import tempfile, os

from typing import Optional

from .config_store import ConfigStore, Device
from .core.manager import DeviceManager
from .summary_store import SummaryStore
from . import rtlog as LOG

from .core.adb_adapter import ADBAdapter, ADBError

import json
from pathlib import Path

bp_api = Blueprint("config_api", __name__, url_prefix="/api")

# สร้าง singletons แบบง่าย (จริง ๆ อาจย้ายไป main.py แล้ว inject เข้ามา)
_cfg_store: Optional[ConfigStore] = None
_mgr: Optional[DeviceManager] = None
_summary: Optional[SummaryStore] = None

def init_api(step_fn=None) -> None:
    global _cfg_store, _mgr, _summary
    if _cfg_store is None:
        _cfg_store = ConfigStore()
    if _summary is None:
        _summary = SummaryStore()
    if _mgr is None:
        _mgr = DeviceManager(_cfg_store, step_fn=step_fn)
    else:
        # ถ้ามี manager อยู่แล้ว แต่เราส่ง step_fn ใหม่มา → อัปเดตให้ทุก controller
        if step_fn is not None:
            try:
                _mgr.set_step_fn(step_fn)
            except Exception:
                pass

# ---------- Devices ----------
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

# ใน app/config_api.py เพิ่ม:
@bp_api.post("/devices/<device_id>/step-once")
def api_device_step_once(device_id: str):
    init_api()
    ctrl = _mgr.get(device_id)
    if not ctrl:
        return jsonify({"error": "not found"}), 404
    try:
        from app.worker_step import worker_step
        res = worker_step(ctrl) or {}
        return jsonify({"ok": True, "out": res})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp_api.get("/devices/<device_id>/shot")
def api_device_shot(device_id: str):
    init_api()
    ctrl = _mgr.get(device_id)
    if not ctrl:
        return jsonify({"error": "not found"}), 404

    try:
        adb = ADBAdapter(ctrl.device)
        img = adb.screencap()
        ok, buf = cv2.imencode(".png", img)
        if not ok:
            return jsonify({"error": "encode failed"}), 500
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
        try:
            tmp.write(buf.tobytes()); tmp.flush(); tmp.close()
            return send_file(tmp.name, mimetype="image/png", as_attachment=False)
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

    # ── (ออปชัน แต่แนะนำ) ตรวจ ADB ก่อนเริ่ม เพื่อให้ error ชัดเจน ──
    preflight = request.args.get("preflight", "1")  # ปิดได้ด้วย ?preflight=0
    if preflight == "1":
        try:
            adb = ADBAdapter(ctrl.device)   # ใช้ตัวเดียวกับ /shot
            adb.ensure_connected()
        except Exception as e:
            return jsonify({
                "error": "adb",
                "message": str(e),
                "status": ctrl.status() if hasattr(ctrl, "status") else {}
            }), 502

    try:
        ok = _mgr.start(device_id)  # ให้ DeviceManager จัดการ state/thread
        return jsonify({
            "ok": bool(ok),
            "status": ctrl.status() if hasattr(ctrl, "status") else {}
        })
    except RuntimeError as e:
        # กรณี state ไม่เหมาะสม เช่น กำลังรันอยู่แล้ว
        return jsonify({
            "error": "conflict",
            "message": str(e),
            "status": ctrl.status() if hasattr(ctrl, "status") else {}
        }), 409
    except ValueError as e:
        # bad input หรือ config ไม่ครบ
        return jsonify({"error": "bad_request", "message": str(e)}), 400
    except Exception as e:
        # กันตก
        return jsonify({"error": "internal", "message": str(e)}), 500


@bp_api.post("/devices/<device_id>/pause")
def api_device_pause(device_id: str):
    init_api()
    ctrl = _mgr.get(device_id)
    if not ctrl:
        return jsonify({"error": "not found"}), 404
    try:
        ok = _mgr.pause(device_id)
        return jsonify({
            "ok": bool(ok),
            "status": ctrl.status() if hasattr(ctrl, "status") else {}
        })
    except RuntimeError as e:
        # เช่น state ไม่เหมาะสม (ไม่ได้ running)
        return jsonify({
            "error": "conflict",
            "message": str(e),
            "status": ctrl.status() if hasattr(ctrl, "status") else {}
        }), 409
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
        return jsonify({
            "ok": bool(ok),
            "status": ctrl.status() if hasattr(ctrl, "status") else {}
        })
    except RuntimeError as e:
        # เช่น state ไม่เหมาะสม (ไม่ได้ paused)
        return jsonify({
            "error": "conflict",
            "message": str(e),
            "status": ctrl.status() if hasattr(ctrl, "status") else {}
        }), 409
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
        return jsonify({
            "ok": bool(ok),
            "status": ctrl.status() if hasattr(ctrl, "status") else {}
        })
    except RuntimeError as e:
        # เช่น กำลังหยุดอยู่แล้ว หรือยังไม่ start
        return jsonify({
            "error": "conflict",
            "message": str(e),
            "status": ctrl.status() if hasattr(ctrl, "status") else {}
        }), 409
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

# Manage devices list (เพิ่ม/แก้/ลบ)
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

    # พยายามดึง log รายอุปกรณ์จาก controller ถ้ามี
    try:
        # ถ้ามีเมธอดเฉพาะอุปกรณ์
        if hasattr(ctrl, "log_tail"):
            text = ctrl.log_tail(lines)
            return jsonify({"text": text})
        # หรือถ้ามีบัฟเฟอร์ข้อความใน ctrl
        if hasattr(ctrl, "rtlog_text"):
            t = ctrl.rtlog_text()  # สมมติเป็นฟังก์ชันคืน string
            # ตัดบรรทัดท้าย ๆ ตาม lines
            rows = t.splitlines()[-lines:]
            return jsonify({"text": "\n".join(rows)})
    except Exception:
        pass

    # Fallback: ใช้ global log แล้วกรองด้วย device_id ถ้าอยาก (ที่ง่ายสุดคืนทั้งก้อน)
    text = LOG.get_text(lines)  # ถ้าต้องการกรองด้วย device_id ต้องปรับที่ที่เขียน log ให้มี prefix
    return jsonify({"text": text})

# ---------- Dashboard (รวม) ----------
@bp_api.get("/dashboard")
def api_dashboard():
    init_api()
    return jsonify(_mgr.dashboard())

# ---------- Summary (รายวัน, 7 วัน) ----------
@bp_api.get("/summary")
def api_summary():
    init_api()
    days = int(request.args.get("days", "7"))
    return jsonify({"days": _summary.get_days(days)})

# ---------- Logs (tail) ----------
@bp_api.get("/logs")
def api_logs():
    # (global logs; สำหรับ per-device อาจทำภายหลัง)
    return jsonify({"text": LOG.get_text(400)})

@bp_api.post("/devices/<device_id>/snap")
def api_device_snap(device_id: str):
    init_api()
    ctrl = _mgr.get(device_id)
    if not ctrl:
        return jsonify({"error":"not found"}), 404

    from datetime import datetime
    try:
        adb = ADBAdapter(ctrl.device)
        img = adb.screencap()
        ok, buf = cv2.imencode(".png", img)
        if not ok:
            return jsonify({"error":"encode failed"}), 500
        # เซฟลงดิสก์ในคอนเทนเนอร์
        cache_dir = os.getenv("CACHE_DIR", "/app/cache")
        os.makedirs(cache_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        fname = f"snap-{device_id}-{ts}.png"
        fpath = os.path.join(cache_dir, fname)
        with open(fpath, "wb") as f:
            f.write(buf.tobytes())
        # ส่ง path กลับ
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
        adb = ADBAdapter(ctrl.device)
        adb.ensure_connected()
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
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(path)

@bp_api.get("/config")
def api_config_get():
    # อ่านไฟล์คอนฟิกก้อนเดียว (shared สำหรับทุก device resolution เดียวกัน)
    p = _cfg_path()
    return jsonify(_read_json(p))

@bp_api.post("/save-config")
def api_config_save():
    body = request.get_json(silent=True) or {}
    # (ถ้าต้อง validation เพิ่ม ค่อยเติมภายหลัง)
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
