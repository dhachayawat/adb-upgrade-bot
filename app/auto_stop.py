# app/auto_stop.py
import os
import time
from typing import Optional, Dict, Any, Tuple
import logging
from .notifier import notify

logger = logging.getLogger("adb-upgrade-bot")

# กันสแปมแจ้งเตือนรายการเดิม โครงสร้าง: {dedup_key: expire_ts}
# ถ้าระบบคุณมี Redis ให้แทนด้วย Redis ก็ได้
_DEDUP: Dict[str, float] = {}

DEDUP_TTL_SEC = float(os.getenv("NOTIFY_DEDUP_TTL", "300"))  # 5 นาทีต่อเหตุเดียวกัน
MAX_SLOTS = int(os.getenv("MAX_ITEM_SLOTS", "12"))

def _should_dedup(key: str) -> bool:
    now = time.time()
    exp = _DEDUP.get(key, 0)
    if now < exp:
        return True
    _DEDUP[key] = now + DEDUP_TTL_SEC
    return False

def auto_stop_and_notify(
    *,
    item_id: str,
    found: bool,
    insert_ok: bool,
    slots_count: int,
    user_label: Optional[str] = None,
    extra_meta: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    รวมเงื่อนไข:
    - not found OR insert failed OR slots_count > MAX_SLOTS -> ของหมด + แจ้งเตือน
    return:
    {
      "out_of_stock": bool,
      "reason": "not_found|insert_failed|exceed_slots|ok",
      "notified": {"sent": int, "channels": {...}} | None
    }
    """
    # ตัดสิน reason
    reason = "ok"
    if not found:
        reason = "not_found"
    elif not insert_ok:
        reason = "insert_failed"
    elif slots_count > MAX_SLOTS:
        reason = "exceed_slots"

    out = {"out_of_stock": False, "reason": reason, "notified": None}

    if reason == "ok":
        return out

    out["out_of_stock"] = True

    # เตรียมข้อความ
    owner = user_label or "ผู้ใช้"
    reason_map = {
        "not_found": "ไม่พบสินค้าในระบบ",
        "insert_failed": "ใส่สินค้าไม่สำเร็จ",
        "exceed_slots": f"จำนวนช่องเกิน {MAX_SLOTS} ช่อง"
    }
    reason_th = reason_map.get(reason, reason)

    title = "⚠️ แจ้งเตือน: สินค้าหมด/ปิดการใส่อัตโนมัติ"
    msg = (
        f"{owner} | Item: {item_id}\n"
        f"สถานะ: {reason_th}\n"
        f"ระบบทำการปิดการใส่อัตโนมัติ (auto stop) แล้ว"
    )

    # กันสแปม (เหตุเดิม ๆ ภายใน DEDUP_TTL_SEC)
    dedup_key = f"{item_id}:{reason}"
    if _should_dedup(dedup_key):
        logger.info("Skip notify (dedup %ss): %s", DEDUP_TTL_SEC, dedup_key)
        return out

    # แนบ meta เพิ่มเติม
    meta = {
        "item_id": item_id,
        "reason": reason,
        "slots_count": slots_count,
        "max_slots": MAX_SLOTS,
    }
    if extra_meta:
        meta.update(extra_meta)

    # ส่งแจ้งเตือน (ส่งได้หลายช่องทางพร้อมกัน ตาม ENV)
    result = notify(title=title, message=msg, meta=meta)
    out["notified"] = result

    # log ไว้ตรวจสอบ
    logger.info("AUTO STOP for item=%s reason=%s slots=%s notify=%s",
                item_id, reason, slots_count, result)

    return out
