# app/rtlog.py
import logging, sys, os
from logging.handlers import RotatingFileHandler
from collections import deque
from threading import Lock

# --- config ---
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_DIR   = os.getenv("LOG_DIR", "/app/logs")
os.makedirs(LOG_DIR, exist_ok=True)

# --- root logger -> stdout + file ---
_logger = logging.getLogger("adb-upgrade-bot")
_logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
_logger.propagate = False  # กันซ้ำ

# clear old handlers (กรณี reload)
for h in list(_logger.handlers):
    _logger.removeHandler(h)

fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

# stdout
sh = logging.StreamHandler(sys.stdout)
sh.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
sh.setFormatter(fmt)
_logger.addHandler(sh)

# rotating file
fh = RotatingFileHandler(os.path.join(LOG_DIR, "app.log"), maxBytes=5_000_000, backupCount=3, encoding="utf-8")
fh.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
fh.setFormatter(fmt)
_logger.addHandler(fh)

# --- simple API ---
def i(msg): _logger.info(msg)
def w(msg): _logger.warning(msg)
def e(msg): _logger.error(msg)

# API สำหรับ /api/logs (เก็บท้าย ๆ ไว้ในหน่วยความจำ)
_buf = deque(maxlen=4000)
_lock = Lock()

class _BufHandler(logging.Handler):
    def emit(self, record):
        try:
            line = fmt.format(record)
            with _lock:
                _buf.append(line)
        except Exception:
            pass

_logger.addHandler(_BufHandler())

def get_text(lines: int = 400) -> str:
    with _lock:
        return "\n".join(list(_buf)[-max(0, lines):])
