"""进程内指标：接口调用、场景运行、干预次数。"""
from __future__ import annotations

import threading
import time


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, int] = {}
        self._started = time.time()

    def incr(self, key: str, n: int = 1) -> None:
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + n

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "uptime_sec": round(time.time() - self._started, 1),
                "started_at": self._started,
                "counters": dict(sorted(self._counters.items())),
            }


METRICS = Metrics()
