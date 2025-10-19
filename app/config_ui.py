# app/config_ui.py
from __future__ import annotations
import os
from flask import Flask, render_template, send_from_directory, abort

from app.config_api import bp_api

# ใช้โฟลเดอร์เทมเพลตตามที่คุณกำหนด
TEMPLATES_DIR = "/app/templates/html"

def create_app() -> Flask:
    app = Flask(__name__, template_folder=TEMPLATES_DIR)
    app.register_blueprint(bp_api)

    @app.get("/")
    def index():
        return render_template("overview.html", title="Overview", active="overview")

    @app.get("/overview")
    def overview():
        return render_template("overview.html", title="Overview", active="overview")

    @app.get("/devices/<device_id>")
    def device_detail(device_id: str):
        return render_template("device_detail.html", title=f"Device {device_id}",
                               active="overview", device_id=device_id)
    
    # เพิ่มด้านล่าง device_detail route เดิม
    @app.get("/devices/<device_id>/edit")
    def device_edit(device_id: str):
        return render_template("device_edit.html",
                            title=f"Edit {device_id}", active="overview",
                            device_id=device_id)

    @app.get("/summary")
    def summary():
        return render_template("summary.html", title="Summary", active="summary")

    @app.get("/manage-devices")
    def manage_devices():
        return render_template("manage_devices.html", title="Manage Devices", active="manage")

    # Legacy integration (เหมือนเดิม)
    @app.get("/legacy")
    def legacy():
        return render_template(
            "base.html",
            title="Legacy Config",
            active="legacy",
            content="""
<div class="card shadow-sm">
  <div class="card-body">
    <div class="d-flex justify-content-between align-items-center">
      <h5 class="mb-0">Legacy Config Screen</h5>
      <a class="btn btn-outline-secondary btn-sm" href="/legacy/raw" target="_blank">Open in new tab</a>
    </div>
    <div class="ratio ratio-16x9 mt-3">
      <iframe src="/legacy/raw" style="border:1px solid #eee; border-radius:.5rem;"></iframe>
    </div>
    <div class="form-text mt-2">วางไฟล์ legacy ที่ <code>/app/templates/html/legacy/index.html</code></div>
  </div>
</div>
""",
        )

    @app.get("/legacy/raw")
    def legacy_raw():
        legacy_dir = os.path.join(TEMPLATES_DIR, "legacy")
        legacy_path = os.path.join(legacy_dir, "index.html")
        if os.path.exists(legacy_path):
            return send_from_directory(legacy_dir, "index.html")
        abort(404, "legacy/index.html not found. Put your old UI at /app/templates/html/legacy/index.html")

    @app.get("/config")
    def config_screen():
        return render_template("config_screen.html", title="Config Screen", active="manage")


    return app
