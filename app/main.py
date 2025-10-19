# app/main.py
from __future__ import annotations
import os

from app.config_api import init_api         # <-- ไม่ต้อง import bp_api
from app.config_ui import create_app
from app.worker_step import worker_step

def main():
    # ผูก API กับ step_fn (ให้ DeviceManager ใช้)
    init_api(step_fn=worker_step)

    # สร้าง Flask app (create_app() จะ register bp_api ให้แล้ว)
    app = create_app()

    # ค่าพอร์ต/โฮสต์จาก env (รองรับ docker-compose)
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8765"))

    # รันเว็บเซิร์ฟเวอร์
    print(f"[main] starting flask on {host}:{port}", flush=True)
    app.run(host=host, port=port, threaded=True, use_reloader=False)

if __name__ == "__main__":
    main()
