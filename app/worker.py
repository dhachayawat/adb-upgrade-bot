import threading
import time

from app import config as C
from app.geometry import Geo, ensure_geo
from app.inserter import add_item_via_insert
from app.upgrader import is_plus5_by_badge, upgrade_until_plus5_or_break
from app.adb import swipe
from app import cv_utils as CV

# ====== สถานะแชร์ให้หน้าเว็บ (controller จะอ่านไปโชว์) ======
STATE = {
    "running": False,
    "phase": "-",
    "batch": 0,
    "current_item_idx": -1,
    "counts": {"processed": 0, "+5": 0, "broken": 0, "skipped": 0, "errors": 0},
    "last_message": "",
    "logs": [],
}

def _log(msg: str):
    print(msg)
    STATE["last_message"] = msg
    STATE["logs"].append(msg)
    if len(STATE["logs"]) > 500:
        STATE["logs"] = STATE["logs"][-500:]

def worker_loop(stop_event: threading.Event):
    """
    ลอจิกใหม่:
      ต่อชิ้น i (1..6):
        1) ใส่ลง
        2) เช็ค +5
           2.1 ถ้ายังไม่ใช่ +5 → อัปเกรดต่อจน +5 หรือแตก
           2.2 ถ้าเป็น +5 → ข้าม
      เมื่อครบ 6 ชิ้นแล้ว ให้ swipe เพื่อเลื่อนแถว แล้ววนกลับชิ้นที่ 1
    """
    STATE["running"] = True
    STATE["phase"] = "run"
    STATE["logs"].clear()
    _log("== Worker started ==")

    geo = Geo()

    try:
        batch = 0
        while not stop_event.is_set():
            batch += 1
            STATE["batch"] = batch
            _log(f"=== Batch #{batch}: เริ่ม 6 ชิ้น ===")

            # บันทึกภาพแรกเพื่อ config geo จากภาพจริง
            ensure_geo(geo, CV.screencap_bgr(save_tag="batch_start"))

            for idx, (ix, iy) in enumerate(C.ITEM_POSITIONS, start=1):
                if stop_event.is_set():
                    break

                STATE["current_item_idx"] = idx
                _log(f"== ชิ้นที่ {idx}: tap ({ix},{iy}) ==")

                # 1) ใส่ลง
                ok_insert = add_item_via_insert((ix, iy), geo)
                if not ok_insert:
                    STATE["counts"]["errors"] += 1
                    _log("WARN: ใส่ลงไม่สำเร็จ → ข้ามชิ้นนี้")
                    STATE["counts"]["skipped"] += 1
                    continue

                # 2) เช็ค +5
                if is_plus5_by_badge(geo):
                    _log("SKIP: ไอเทมเป็น +5 อยู่แล้ว → ข้ามอัปเกรด")
                    STATE["counts"]["skipped"] += 1
                    STATE["counts"]["processed"] += 1
                else:
                    # 2.1 ยังไม่ใช่ +5 → อัปเกรดต่อ
                    res = upgrade_until_plus5_or_break(geo, stop_event)
                    STATE["counts"]["processed"] += 1
                    if res.get("plus5"):
                        STATE["counts"]["+5"] += 1
                        _log(f"RESULT: ได้ +5 (clicks={res.get('clicks')})")
                    elif res.get("broken"):
                        STATE["counts"]["broken"] += 1
                        _log(f"RESULT: แตก (clicks={res.get('clicks')})")
                    else:
                        _log(f"RESULT: จบด้วย limit/stop (clicks={res.get('clicks')})")

                # เมื่อถึงชิ้นที่ 6 และกำลังจะ “ข้ามไปยังชิ้นถัดไป” ให้เลื่อนแถวก่อน
                if idx == 6 and not stop_event.is_set():
                    x = int(C.TRAY_SWIPE_X)
                    y1 = int(C.TRAY_SWIPE_Y)
                    y2 = y1 + int(C.TRAY_SWIPE_DY)
                    ms = int(C.TRAY_SWIPE_MS)
                    _log(f"[TRAY] swipe ({x},{y1})->({x},{y2}) {ms}ms")
                    swipe(x, y1, x, y2, ms=ms)

            # จบรอบ 6 ชิ้นแล้ว วนต่อ (ไม่มี limit ถ้าอยากให้หยุดตาม BATCH_LOOPS ให้เพิ่มเช็คตรงนี้)
    except Exception as e:
        STATE["counts"]["errors"] += 1
        _log(f"[EXC] {e}")
    finally:
        STATE["running"] = False
        STATE["phase"] = "-"
        STATE["current_item_idx"] = -1
        _log("== Worker stopped ==")
