# app/notifier.py
import os
import json
import time
import logging
from typing import Optional, Dict, Any
import requests

logger = logging.getLogger("adb-upgrade-bot")

# ===== ช่องทางที่รองรับผ่าน ENV =====
# TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
# LINE_NOTIFY_TOKEN
# WEBHOOK_URL  (รับ JSON POST)
#
# หมายเหตุ:
# - ใส่ ENV อันไหนไว้ ก็จะส่งทางนั้นด้วย (ส่งได้หลายทางพร้อมกัน)
# - ถ้าไม่ใส้อะไรเลย ฟังก์ชันจะไม่ raise error แต่จะคืนผลว่าส่ง 0 ช่องทาง

DEFAULT_TIMEOUT = 6  # seconds

def _send_telegram(text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=DEFAULT_TIMEOUT)
        ok = r.status_code == 200 and r.json().get("ok", False)
        if not ok:
            logger.warning("Telegram notify failed: %s", r.text[:300])
        return ok
    except Exception as e:
        logger.exception("Telegram notify exception: %s", e)
        return False

def _send_line_notify(text: str) -> bool:
    token = os.getenv("LINE_NOTIFY_TOKEN")
    if not token:
        return False
    url = "https://notify-api.line.me/api/notify"
    headers = {"Authorization": f"Bearer {token}"}
    data = {"message": text}
    try:
        r = requests.post(url, headers=headers, data=data, timeout=DEFAULT_TIMEOUT)
        ok = r.status_code == 200
        if not ok:
            logger.warning("LINE Notify failed: %s", r.text[:300])
        return ok
    except Exception as e:
        logger.exception("LINE Notify exception: %s", e)
        return False

def _send_webhook(payload: Dict[str, Any]) -> bool:
    url = os.getenv("WEBHOOK_URL")
    if not url:
        return False
    try:
        r = requests.post(url, json=payload, timeout=DEFAULT_TIMEOUT)
        ok = 200 <= r.status_code < 300
        if not ok:
            logger.warning("Webhook notify failed: %s", r.text[:300])
        return ok
    except Exception as e:
        logger.exception("Webhook notify exception: %s", e)
        return False

# -------------- public API --------------
def notify(title: str, message: str, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    ส่งแจ้งเตือนหลายช่องทางตาม ENV ที่กำหนด
    return: {"sent": int, "channels": {"telegram": bool, "line": bool, "webhook": bool}}
    """
    meta = meta or {}
    text = f"{title}\n{message}"
    payload = {"title": title, "message": message, "meta": meta, "ts": int(time.time())}

    sent_tg = _send_telegram(text)
    sent_line = _send_line_notify(text)
    sent_webhook = _send_webhook(payload)

    sent_count = sum([sent_tg, sent_line, sent_webhook])

    return {
        "sent": sent_count,
        "channels": {
            "telegram": sent_tg,
            "line": sent_line,
            "webhook": sent_webhook,
        }
    }
