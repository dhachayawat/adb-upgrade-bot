# app/main.py
import threading
from app import config as C
from app.templates import ensure as ensure_templates
from app import controller as CTRL
from app.config_ui import run as run_ui
from app import cv_utils as CV
import time, os, cv2

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
    os.makedirs(C.CACHE_DIR, exist_ok=True)
    img = CV.screencap_bgr(save_tag=tag)
    ts = time.strftime("%Y%m%d-%H%M%S")
    out_path = os.path.join(C.CACHE_DIR, f"snap_{tag}_{ts}.png")
    cv2.imwrite(out_path, img)
    print(f"[SNAP] saved -> {out_path} ({img.shape[1]}x{img.shape[0]})", flush=True)

def start_ui():
    t = threading.Thread(target=run_ui, kwargs={"host":"0.0.0.0","port":8765}, daemon=True)
    t.start()
    return t

def main():
    ensure_templates(C.TEMPLATES_DIR)
    ui_thread = start_ui()
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
            print("Restarting Web UI ...")
            ui_thread = start_ui()
        elif cmd == "q":
            CTRL.stop()
            print("Bye", flush=True)
            break
        else:
            print("Unknown command. Use 1/0/3/4/8/q", flush=True)

if __name__ == "__main__":
    main()
