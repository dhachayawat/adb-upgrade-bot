# app/summary_store.py
from __future__ import annotations
import json, os, tempfile
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

SUMMARY_FILE = os.getenv("SUMMARY_FILE", "/app/data/summary.json")
TZ_NAME      = os.getenv("SUMMARY_TZ",   "Asia/Bangkok")
KEEP_DAYS    = int(os.getenv("SUMMARY_KEEP_DAYS", "7"))

def _atomic_write(path: str, data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as tmp:
        json.dump(data, tmp, ensure_ascii=False, indent=2)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = tmp.name
    os.replace(tmp_path, path)

def _safe_read_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def _today_str(tz: ZoneInfo) -> str:
    now = datetime.now(tz)
    return now.strftime("%Y-%m-%d")

class SummaryStore:
    """
    Rolling daily histogram of 'breaks by attempt_level' (current+1).
    Structure:
    {
      "version": "1.0",
      "tz": "Asia/Bangkok",
      "updated_at": "...",
      "days": [
        { "date": "YYYY-MM-DD", "totals": { "5": 3, ... }, "devices": { "d1": { "5": 2 }, ... } }
      ]
    }
    """
    def __init__(self,
                 path: str = SUMMARY_FILE,
                 tz_name: str = TZ_NAME,
                 keep_days: int = KEEP_DAYS) -> None:
        self.path = path
        self.tz   = ZoneInfo(tz_name)
        self.keep_days = max(1, keep_days)

    def _load(self) -> Dict[str, Any]:
        data = _safe_read_json(self.path)
        if not data:
            data = {"version": "1.0", "tz": TZ_NAME, "updated_at": None, "days": []}
        if "days" not in data or not isinstance(data["days"], list):
            data["days"] = []
        return data

    def _save(self, data: Dict[str, Any]) -> None:
        data["tz"] = TZ_NAME
        data["updated_at"] = datetime.now(self.tz).isoformat()
        # prune front if longer than keep_days
        if len(data["days"]) > self.keep_days:
            data["days"] = data["days"][-self.keep_days:]
        _atomic_write(self.path, data)

    def _ensure_today(self, data: Dict[str, Any]) -> Dict[str, Any]:
        today = _today_str(self.tz)
        if data["days"] and data["days"][-1].get("date") == today:
            return data["days"][-1]
        day = {"date": today, "totals": {}, "devices": {}}
        data["days"].append(day)
        # after append, if overflow will be pruned in _save()
        return day

    # ---- Public API ----
    def add_break(self, device_id: str, attempt_level: int) -> None:
        """
        Increment break counter for today at given attempt_level for a device.
        attempt_level = current_level + 1 (e.g., breaking from 4 -> 5 => level 5).
        """
        if not isinstance(attempt_level, int) or attempt_level < 1:
            return  # ignore invalid
        data = self._load()
        day  = self._ensure_today(data)
        key = str(attempt_level)

        # totals
        day["totals"][key] = int(day["totals"].get(key, 0)) + 1
        # per-device
        dev_map = day["devices"].setdefault(device_id, {})
        dev_map[key] = int(dev_map.get(key, 0)) + 1

        self._save(data)

    def get_days(self, n: int = 7) -> List[Dict[str, Any]]:
        data = self._load()
        days = data.get("days", [])
        n = max(1, min(n, self.keep_days))
        return days[-n:]

    def reset_today(self) -> None:
        """Clear today's entry (if exists)."""
        data = self._load()
        today = _today_str(self.tz)
        if data["days"] and data["days"][-1].get("date") == today:
            data["days"][-1] = {"date": today, "totals": {}, "devices": {}}
            self._save(data)

    def reset_all(self) -> None:
        """Clear all history."""
        data = {"version": "1.0", "tz": TZ_NAME, "updated_at": None, "days": []}
        self._save(data)
