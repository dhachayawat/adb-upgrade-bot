# app/worker.py
# ลูปต่อไอเทม: แตะ -> OCR [n] ก่อนใส่ลง -> ตัดสินใจใส่ลง/ข้าม -> หลังใส่ลงรอ 0.5s -> นับ "สำเร็จ" ให้ถึง +5
import time
from app import config as C
from app import adb as ADB
from app.inserter import click_insert_via_cv
from app.upgrader import read_bracket_level_from_status, upgrade_count_successes
from app import rtlog as LOG

def _pause_wait(pause_ev, stop_ev):
    while pause_ev.is_set() and not stop_ev.is_set():
        time.sleep(0.1)

def _swipe_tray_safe():
    """
    ลากถาดตามค่าคอนฟิก (กันพลาด / มี log ชัด)
    """
    x = C.TRAY_SWIPE_X
    y = C.TRAY_SWIPE_Y            # <- ค่าใน config.py
    dy = C.TRAY_SWIPE_DY
    ms = C.TRAY_SWIPE_MS
    y2 = y + dy

    LOG.tee(f"[เลื่อนถาด] swipe ({x},{y}) -> ({x},{y2}) ms={ms}")
    try:
        ADB.swipe(x, y, x, y2, ms)
    except Exception as e:
        LOG.tee(f"[เลื่อนถาด] ล้มเหลว: {e}")

def worker_loop(stop_ev, pause_ev):
    LOG.tee("เริ่มทำงาน Worker (ลอจิกตรวจ [n] ก่อนใส่ลง)")
    batch_idx = 0
    target = getattr(C, "TARGET_LEVEL", 5)

    while not stop_ev.is_set():
        _pause_wait(pause_ev, stop_ev)
        batch_idx += 1
        LOG.tee(f"=== รอบที่ {batch_idx}: เริ่ม 6 ชิ้น ===")

        for i, (ix,iy) in enumerate(C.ITEM_POSITIONS, start=1):
            if stop_ev.is_set(): break
            _pause_wait(pause_ev, stop_ev)

            LOG.tee(f"== ชิ้นที่ {i}: แตะตำแหน่งไอเทม ({ix},{iy}) ==")
            ADB.tap(ix, iy)

            # 1) อ่านระดับในวงเล็บ [n] ก่อนใส่ลง
            pre_lvl = read_bracket_level_from_status()

            # 2) ตัดสินใจ: ไม่เจอเลข หรือ <=4 -> ใส่ลง, ถ้า >=5 -> ข้าม
            if pre_lvl is None or pre_lvl <= 4:
                LOG.tee(f"[ก่อนใส่ลง] ระดับในวงเล็บ = {pre_lvl} → ดำเนินการใส่ลง")
                ok = click_insert_via_cv(wait_pre=0.8)
                if not ok:
                    LOG.tee("คำเตือน: ใส่ลงไม่สำเร็จ → ข้ามชิ้นนี้")
                    continue

                LOG.tee("ใส่ลงสำเร็จ → หน่วง 0.5 วินาทีก่อนตรวจ/อัป")
                time.sleep(0.5)

                # 3) อัปเกรดนับ “สำเร็จ” ให้ถึงเป้า (เช่น +5 หรือ TARGET_LEVEL)
                done, end_lvl, reason = upgrade_count_successes(pre_lvl, target)
                LOG.tee(f"[ผลชิ้นที่ {i}] สรุป: สำเร็จถึงเป้า={done}, เลเวลสุดท้าย={end_lvl}, เหตุผล='{reason}'")

            else:
                LOG.tee(f"[ก่อนใส่ลง] พบระดับในวงเล็บ = {pre_lvl} (≥5) → ข้ามไปชิ้นถัดไป")

        # ครบ 6 ชิ้นแล้ว: เลื่อนถาด (แก้ชื่อคีย์พิมพ์ผิดแล้ว)
        if stop_ev.is_set(): break
        _swipe_tray_safe()
        time.sleep(0.3)

    LOG.tee("หยุดทำงาน Worker แล้ว")
