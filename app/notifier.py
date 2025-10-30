# app/notifier.py
import os, json, time, logging
from typing import Optional, Dict, Any

logger = logging.getLogger("adb-upgrade-bot")

# พยายามใช้ requests ถ้ามี; ถ้าไม่มีจะ fallback เป็น urllib
try:
    import requests
except Exception:
    requests = None

import urllib.request
import urllib.error

DEFAULT_TIMEOUT = 6  # seconds

def _post(url: str, *, json_body=None, form_data=None, headers=None, timeout=DEFAULT_TIMEOUT):
    """ส่ง POST โดยใช้ requests ถ้ามี ไม่งั้นใช้ urllib"""
    headers = headers or {}
    if requests is not None:
        if json_body is not None:
            return requests.post(url, json=json_body, headers=headers, timeout=timeout)
        else:
            return requests.post(url, data=form_data, headers=headers, timeout=timeout)
    else:
        try:
            if json_body is not None:
                data = json.dumps(json_body).encode("utf-8")
                headers = {**headers, "Content-Type": "application/json"}
            else:
                # form-encoded
                from urllib.parse import urlencode
                data = urlencode(form_data or {}).encode("utf-8")
                headers = {**headers, "Content-Type": "application/x-www-form-urlencoded"}

            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                class DummyResp:
                    status_code = resp.getcode()
                    text = resp.read().decode("utf-8", errors="ignore")
                    def json(self):
                        try:
                            return json.loads(self.text)
                        except Exception:
                            return {}
                return DummyResp()
        except urllib.error.HTTPError as e:
            class DummyResp:
                status_code = e.code
                text = e.read().decode("utf-8", errors="ignore")
                def json(self): 
                    try: return json.loads(self.text)
                    except Exception: return {}
            return DummyResp()
        except Exception as e:
            # จำลอง response ล้มเหลว
            class DummyResp:
                status_code = 599
                text = str(e)
                def json(self): return {}
            return DummyResp()

def _send_telegram(text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = _post(url, json_body={"chat_id": chat_id, "text": text})
        ok = (r.status_code == 200) and (r.json().get("ok", False) if hasattr(r, "json") else True)
        if not ok:
            logger.warning("Telegram notify failed: %s", getattr(r, "text", "")[:300])
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
    try:
        r = _post(url, form_data={"message": text}, headers=headers)
        ok = (r.status_code == 200)
        if not ok:
            logger.warning("LINE Notify failed: %s", getattr(r, "text", "")[:300])
        return ok
    except Exception as e:
        logger.exception("LINE Notify exception: %s", e)
        return False

def _send_webhook(payload: Dict[str, Any]) -> bool:
    url = os.getenv("WEBHOOK_URL")
    if not url:
        return False
    try:
        r = _post(url, json_body=payload)
        ok = 200 <= r.status_code < 300
        if not ok:
            logger.warning("Webhook notify failed: %s", getattr(r, "text", "")[:300])
        return ok
    except Exception as e:
        logger.exception("Webhook notify exception: %s", e)
        return False

def notify(title: str, message: str, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    meta = meta or {}
    text = f"{title}\n{message}"
    payload = {"title": title, "message": message, "meta": meta, "ts": int(time.time())}

    sent_tg = _send_telegram(text)
    sent_line = _send_line_notify(text)
    sent_webhook = _send_webhook(payload)

    sent_count = sum([sent_tg, sent_line, sent_webhook])
    return {"sent": sent_count, "channels": {"telegram": sent_tg, "line": sent_line, "webhook": sent_webhook}}
