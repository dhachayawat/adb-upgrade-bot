# app/core/device_controller.py
from __future__ import annotations
import threading, time
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Any

# A per-device worker "step" function signature (pluggable):
# It should perform one small unit of work and return an event dict, e.g.:
#   {"done_item": True}                              # finished an item to target
#   {"break_at_level": 5}                            # broke at attempt level 5
#   {"fail_nonbreak": True}                          # fail but not break
# The controller aggregates metrics and handles pause/stop/uptime.
StepFn = Callable[["DeviceController"], Dict[str, Any]]

@dataclass
class DeviceController:
    id: str
    name: str
    device: str                # host:port or usb:<serial>
    target_level: int = 5      # realtime-updatable
    # injected behaviors
    step_fn: Optional[StepFn] = None

    # runtime state
    state: str = field(default="idle")  # idle|running|paused|stopping|error
    started_at: Optional[float] = None
    paused_at: Optional[float] = None
    uptime_ms_acc: float = 0.0

    # counters
    items_upgraded_done: int = 0
    break_histogram: Dict[str, int] = field(default_factory=dict)
    fail_nonbreak: int = 0
    successes: int = 0
    fails: int = 0
    upgrade_clicks: int = 0

    # health
    last_error: Optional[str] = None
    adb_latency_ms_avg: Optional[float] = None
    cv_latency_ms_avg: Optional[float] = None

    # internal
    _th: Optional[threading.Thread] = field(default=None, init=False, repr=False)
    _stop_ev: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _pause_ev: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)
    _sleep_ms_between_steps: int = 50  # small idle to avoid busy-spin

    # ---- lifecycle ----
    def start(self) -> bool:
        with self._lock:
            if self.state in ("running", "paused"):
                return False
            self._stop_ev.clear()
            self._pause_ev.clear()
            self.started_at = time.time()
            self.paused_at = None
            self.state = "running"
            self._th = threading.Thread(target=self._loop, name=f"dev:{self.id}", daemon=True)
            self._th.start()
            return True

    def pause(self) -> bool:
        with self._lock:
            if self.state != "running":
                return False
            self._pause_ev.set()
            # accumulate uptime now
            if self.started_at:
                self.uptime_ms_acc += (time.time() - self.started_at) * 1000.0
            self.paused_at = time.time()
            self.state = "paused"
            return True

    def resume(self) -> bool:
        with self._lock:
            if self.state != "paused":
                return False
            self._pause_ev.clear()
            self.started_at = time.time()
            self.paused_at = None
            self.state = "running"
            return True

    def stop(self) -> bool:
        with self._lock:
            if self.state in ("idle", "stopping"):
                return False
            self._stop_ev.set()
            self.state = "stopping"
        # wait thread
        th = self._th
        if th and th.is_alive():
            th.join(timeout=5.0)
        with self._lock:
            if self.state != "error":
                self.state = "idle"
        return True

    def reset_counters(self) -> None:
        with self._lock:
            self.items_upgraded_done = 0
            self.break_histogram.clear()
            self.fail_nonbreak = 0
            self.successes = 0
            self.fails = 0
            self.upgrade_clicks = 0

    def set_target_level(self, level: int) -> None:
        if not isinstance(level, int) or not (1 <= level <= 10):
            raise ValueError("target_level must be int in 1..10")
        with self._lock:
            self.target_level = level

    # ---- metrics & status ----
    def _uptime_now_ms(self) -> float:
        with self._lock:
            if self.state == "running" and self.started_at:
                return self.uptime_ms_acc + (time.time() - self.started_at) * 1000.0
            return self.uptime_ms_acc

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "id": self.id,
                "name": self.name,
                "device": self.device,
                "state": self.state,
                "target_level": self.target_level,
                "uptime_min": round(self._uptime_now_ms() / 60000.0, 2),
                "items_upgraded_done": self.items_upgraded_done,
                "break_histogram": dict(self.break_histogram),
                "fail_nonbreak": self.fail_nonbreak,
                "successes": self.successes,
                "fails": self.fails,
                "upgrade_clicks": self.upgrade_clicks,
                "last_error": self.last_error,
                "adb_latency_ms_avg": self.adb_latency_ms_avg,
                "cv_latency_ms_avg": self.cv_latency_ms_avg,
            }

    # ---- internal loop ----
    def _loop(self) -> None:
        try:
            while not self._stop_ev.is_set():
                # respect pause
                if self._pause_ev.is_set():
                    time.sleep(0.05)
                    continue

                # perform one small step (pluggable)
                evt = {}
                try:
                    if self.step_fn:
                        evt = self.step_fn(self) or {}
                except Exception as e:
                    with self._lock:
                        self.last_error = f"{type(e).__name__}: {e}"
                        self.state = "error"
                    break

                # aggregate counters
                with self._lock:
                    if evt.get("done_item"):
                        self.items_upgraded_done += 1
                    if "break_at_level" in evt:
                        lv = str(int(evt["break_at_level"]))
                        self.break_histogram[lv] = int(self.break_histogram.get(lv, 0)) + 1
                        self.fails += 1
                    if evt.get("fail_nonbreak"):
                        self.fail_nonbreak += 1
                        self.fails += 1
                    if "success_clicks" in evt:
                        self.successes += int(evt["success_clicks"])
                    if "upgrade_clicks" in evt:
                        self.upgrade_clicks += int(evt["upgrade_clicks"])

                # small rest to avoid hot loop
                time.sleep(self._sleep_ms_between_steps / 1000.0)

        finally:
            # finalize state if not error
            with self._lock:
                if self.state not in ("error", "idle"):
                    # accumulate uptime if leaving running
                    if self.state == "running" and self.started_at:
                        self.uptime_ms_acc += (time.time() - self.started_at) * 1000.0
                    self.state = "idle"
