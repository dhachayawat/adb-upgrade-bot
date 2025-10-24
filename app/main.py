# app/main.py
from __future__ import annotations
import os

# เลือกโหมดรัน: thread (เดิม) หรือ mp (โปรเซสแยกต่อดีไวซ์)
RUN_MODE = os.getenv("RUN_MODE", "thread").lower()

# ตั้งค่า start method ของ multiprocessing ให้ปลอดภัยกับ Flask/OpenCV
# - "spawn" ปลอดภัยสุด (แนะนำค่า default)
# - เปลี่ยนได้ด้วย ENV: MP_START_METHOD=[spawn|fork|forkserver]
if RUN_MODE == "mp":
    import multiprocessing as mp
    _mp_method = os.getenv("MP_START_METHOD", "spawn").lower()
    try:
        mp.set_start_method(_mp_method, force=True)
    except RuntimeError:
        # ถูกตั้งค่าไปแล้ว (เช่น หลัง reload) ก็ข้ามได้
        pass
    # Windows ต้องมี freeze_support (เผื่อรันนอก Docker)
    try:
        mp.freeze_support()
    except Exception:
        pass

from app.config_api import init_api         # <-- ไม่ต้อง import bp_api
from app.config_ui import create_app
from app.worker_step import worker_step
from . import rtlog as LOG


def main():
    # ผูก API กับ step_fn (ให้ DeviceManager ใช้)
    # หมายเหตุ: ถ้า RUN_MODE=mp โค้ดใน init_api จะเมิน step_fn ให้อยู่แล้ว
    init_api(step_fn=worker_step)

    # สร้าง Flask app (create_app() จะ register bp_api ให้แล้ว)
    app = create_app()

    # ค่าพอร์ต/โฮสต์จาก env (รองรับ docker-compose)
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8765"))

    # รันเว็บเซิร์ฟเวอร์
    print(f"[main] starting flask on {host}:{port}", flush=True)
    LOG.i("=== BOOT OK / logger stdout test ===")
    print("=== PRINT to stdout test ===", flush=True)
    # threaded=True ใช้ได้ทั้งโหมด thread และ mp (mp ใช้โปรเซสลูกสำหรับ worker)
    app.run(host=host, port=port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
