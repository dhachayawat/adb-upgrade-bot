# app/worker.py
# ลูปต่อไอเทม: แตะ -> OCR [n] ก่อนใส่ลง -> ตัดสินใจใส่ลง/ข้าม -> หลังใส่ลงรอ 0.5s -> อัปเกรดตาม need_success
import time
from app import config as C
from app import adb as ADB
from app.inserter import click_insert_via_cv
from app.upgrader import read_bracket_level_from_status, upgrade_count_successes
from app import rtlog as LOG
from app import state as ST

def _pause_wait(pause_ev, stop_ev):
    while pause_ev.is_set() and not stop_ev.is_set():
        time.sleep(0.1)

def _need_success_for(level: int | None) -> int:
    lv = int(level or 0)
    if lv >= 5: return 0
    if lv <= 0: return 5
    return 5 - lv  # +1→4, +2→3, +3→2, +4→1

def worker_loop(stop_ev, pause_ev):
    LOG.tee("เริ่มทำงาน Worker (อ่านระดับก่อนใส่ลง แล้วนับสำเร็จตาม need ผ่าน overlay)")
    batch_idx = 0
    while not stop_ev.is_set():
        _pause_wait(pause_ev, stop_ev)
        batch_idx += 1
        LOG.tee(f"=== รอบที่ {batch_idx}: เริ่ม 6 ชิ้น ===")

        for i, (ix, iy) in enumerate(C.ITEM_POSITIONS, start=1):
            if stop_ev.is_set():
                break
            _pause_wait(pause_ev, stop_ev)

            LOG.tee(f"== ชิ้นที่ {i}: แตะตำแหน่งไอเทม ({ix},{iy}) ==")
            ADB.tap(ix, iy)

            # 1) อ่านระดับในวงเล็บ [n] ก่อนใส่ลง — อ่านครั้งเดียวเท่านั้น
            pre_lvl = read_bracket_level_from_status(retry=3)
            need = _need_success_for(pre_lvl)

            # ส่งสถานะขึ้น UI (ยืนยันว่าจะใช้อ่านครั้งเดียว)
            ST.set_current(
                item_index=i,
                pre_level=(pre_lvl or 0),
                need_success=need,
                done_success=0,
                last_msg=f"ระดับเริ่มต้น +{pre_lvl or 0} → ต้องการสำเร็จอีก {need} ครั้ง (นับจาก overlay)"
            )

            # 2) ตัดสินใจ: ถ้าเริ่มต้น >=5 → ข้าม
            if (pre_lvl or 0) >= 5:
                LOG.tee(f"[ก่อนใส่ลง] ระดับในวงเล็บ = {pre_lvl} (≥5) → ข้ามไปชิ้นถัดไป")
                ST.set_current(last_msg="ข้าม: ระดับเริ่ม ≥ +5")
                continue

            LOG.tee(f"[ก่อนใส่ลง] ระดับในวงเล็บ = {pre_lvl} → ใส่ลง แล้วอัปเกรดตาม need={need} (ไม่นอ่านซ้ำระหว่างอัป)")
            ok = click_insert_via_cv(wait_pre=0.8)
            if not ok:
                LOG.tee("คำเตือน: ใส่ลงไม่สำเร็จ → ข้ามชิ้นนี้")
                ST.set_current(last_msg="ใส่ลงไม่สำเร็จ → ข้าม")
                continue

            LOG.tee("ใส่ลงสำเร็จ → หน่วง 0.2 วินาทีก่อนเริ่มอัปเกรด (overlay counter)")
            time.sleep(0.2)

            # 3) อัปเกรดตาม need_success โดย "ไม่นอ่านระดับซ้ำ" ระหว่างอัปเกรด
            try:
                # ถ้า upgrader รองรับ recheck=False ให้ส่งไปเลย
                result = upgrade_count_successes(pre_lvl or 0, need, recheck=False)
            except TypeError:
                # เวอร์ชันเดิมไม่รองรับพารามิเตอร์นี้
                result = upgrade_count_successes(pre_lvl or 0, need)

            # ทำให้คืนค่าปลอดภัย/สม่ำเสมอ
            done = False
            end_lvl = pre_lvl or 0
            reason = "unknown"
            done_success = 0

            if result is None:
                reason = "upgrade_count_successes returned None"
            elif isinstance(result, tuple):
                if len(result) == 4:
                    done, end_lvl, reason, done_success = result
                elif len(result) == 3:
                    # บางเวอร์ชันคืนแค่ 3 ค่า: (done, end_lvl, reason)
                    _done, _end_lvl, _reason = result
                    done, end_lvl, reason = _done, (_end_lvl or (pre_lvl or 0)), (_reason or reason)
                    # ประมาณจำนวนสำเร็จจากต่างระดับ เพื่อใช้กับ overlay-flow
                    done_success = max(0, (end_lvl or 0) - (pre_lvl or 0))
                else:
                    reason = f"unexpected return length: {len(result)}"
            else:
                reason = f"unexpected return type: {type(result).__name__}"

            # คำนวณ end ที่จะรายงานบน UI จาก overlay-flow
            end_est = (pre_lvl or 0) + (done_success or 0)

            LOG.tee(
                f"[ผลชิ้นที่ {i}] สรุปจาก overlay: done={done}, end≈+{end_est}, "
                f"need={need}, สำเร็จไป={done_success}, เหตุผล='{reason}'"
            )
            ST.set_current(
                done_success=done_success,
                last_msg=f"จบชิ้น {i}: end≈+{end_est}, เหตุผล: {reason}"
            )

        # ครบ 6 ชิ้นแล้ว: เลื่อนถาด
        if stop_ev.is_set():
            break
        LOG.tee(
            f"[เลื่อนถาด] x={C.TRAY_SWIPE_X}, y={C.TRAY_SWIPE_Y}, "
            f"dY={C.TRAY_SWIPE_DY}, ms={C.TRAY_SWIPE_MS}"
        )
        ADB.swipe(
            C.TRAY_SWIPE_X, C.TRAY_SWIPE_Y,
            C.TRAY_SWIPE_X, C.TRAY_SWIPE_Y + C.TRAY_SWIPE_DY,
            C.TRAY_SWIPE_MS
        )
        time.sleep(0.3)

    LOG.tee("หยุดทำงาน Worker แล้ว")
    ST.clear()

