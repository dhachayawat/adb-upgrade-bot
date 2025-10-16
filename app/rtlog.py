# app/rtlog.py
import threading
from collections import deque
from datetime import datetime

_LOCK = threading.Lock()
_BUF  = deque(maxlen=2000)  # เก็บล่าสุด ~2000 บรรทัด

def ts():
    return datetime.now().strftime("%H:%M:%S")

def add(line: str):
    with _LOCK:
        for ln in (line if isinstance(line, list) else [line]):
            _BUF.append(f"[{ts()}] {ln}")

def get_text(n: int | None = None) -> str:
    with _LOCK:
        if n is None or n >= len(_BUF):
            return "\n".join(_BUF)
        return "\n".join(list(_BUF)[-n:])

def tee(msg: str, print_also: bool = True):
    if print_also:
        print(msg, flush=True)
    add(msg)
