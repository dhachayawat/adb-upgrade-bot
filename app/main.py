# app/main.py
import threading
import time, os, cv2

from app import config as C
from app.templates import ensure as ensure_templates
from app import controller as CTRL
from app.config_ui import run as run_ui
from app import cv_utils as CV

_UI_LOCK = threading.Lock()
_UI_THREAD = None

def print_menu():
    print("\n=== ADB Upgrade Bot (Web-first) ===")
    print("Web UI: http://localhost:8765  (auto started)")
    print("Optional hotkeys here:")
    print("1 = START bot")
    print("0 = STOP bot")
    print("3 = SCREENSHOT")
    print("4 = PREVIEW overlays")
    print("8 = RESTART Web UI server")
    print("q = QUIT\n", flush=True)

def capture_once(tag="manual"):
    # ใช้ DEBUG_DIR แทน CACHE_DIR (เราทำ alias ไว้แล้วแต่ระบุชัดเจนเลย)
    out_dir = C.DEBUG_DIR
    os.makedirs(out_dir, exist_ok=True)
    img = CV.screencap_bgr(save_tag=tag)
    ts = time.strftime("%Y%m%d-%H%M%S")
    out_path = os.path.join(out_dir, f"snap_{tag}_{ts}.png")
    cv2.imwrite(out_path, img)
    print(f"[SNAP] saved -> {out_path} ({img.shape[1]}x{img.shape[0]})", flush=True)

def start_ui():
    """สตาร์ท Web UI หนึ่งครั้ง ถ้ารันอยู่แล้วจะไม่เปิดซ้ำ (กันพอร์ตชน)"""
    global _UI_THREAD
    with _UI_LOCK:
        if _UI_THREAD and _UI_THREAD.is_alive():
            print("[UI] already running at http://localhost:8765", flush=True)
            return _UI_THREAD
        t = threading.Thread(
            target=run_ui,  # run_ui(app=None, host, port, debug) จะสร้าง Flask app และ .run()
            kwargs={"host": "0.0.0.0", "port": 8765, "debug": False},
            daemon=True,
        )
        t.start()
        _UI_THREAD = t
        print("[UI] started at http://localhost:8765", flush=True)
        return t

def restart_ui():
    """รีสตาร์ทแบบง่าย ๆ: ถ้ายังรันอยู่ให้แจ้ง และพยายามสตาร์ทใหม่ (ถ้าพอร์ตยังครองอยู่จะไม่เริ่มซ้ำ)"""
    print("[UI] restart requested ...", flush=True)
    # หมายเหตุ: werkzeug dev server ไม่มี API ปิดจากภายนอกง่าย ๆ
    # ที่นี่จึงทำแค่ลอง start_ui() ซึ่งมี guard ไม่ให้เปิดซ้ำ
    start_ui()

def main():
    ensure_templates(C.TEMPLATES_DIR)
    start_ui()
    print_menu()

    while True:
        try:
            cmd = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            cmd = "q"

        if cmd == "1":
            ok, msg = CTRL.start(); print(msg, flush=True)
        elif cmd == "0":
            ok, msg = CTRL.stop(); print(msg, flush=True)
        elif cmd == "3":
            capture_once("manual")
        elif cmd == "4":
            from app.calibrate import main as calib_main
            calib_main()
        elif cmd == "8":
            restart_ui()
        elif cmd == "q":
            CTRL.stop()
            print("Bye", flush=True)
            break
        else:
            print("Unknown command. Use 1/0/3/4/8/q", flush=True)

if __name__ == "__main__":
    main()
