# app/main_mp.py
from __future__ import annotations
import os

# บังคับโหมด multiprocessing เสมอในไฟล์นี้
os.environ.setdefault("RUN_MODE", "mp")

import multiprocessing as mp

# ตั้ง start method ให้ปลอดภัยกับ OpenCV/Tesseract/Flask
MP_START_METHOD = os.getenv("MP_START_METHOD", "spawn").lower()
try:
    mp.set_start_method(MP_START_METHOD, force=True)
except RuntimeError:
    # ถูกตั้งไปแล้ว (กรณี reload) ก็ปล่อยผ่าน
    pass
try:
    mp.freeze_support()  # เผื่อรันบน Windows (นอก Docker)
except Exception:
    pass

from app.config_api import init_api
from app.config_ui import create_app
from app.worker_step import worker_step
from . import rtlog as LOG

def main():
    # init_api จะเลือกใช้ MPDeviceManager ตาม RUN_MODE=mp อัตโนมัติ
    # step_fn ถูกเมินในโหมด mp แต่ใส่ไว้ไม่เป็นไร
    init_api(step_fn=worker_step)

    app = create_app()

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8765"))

    print(f"[main_mp] starting flask on {host}:{port} (RUN_MODE=mp, start_method={MP_START_METHOD})", flush=True)
    LOG.i("=== BOOT OK (main_mp) ===")
    app.run(host=host, port=port, threaded=True, use_reloader=False)

if __name__ == "__main__":
    main()
