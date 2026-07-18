"""
Wiederverwendbarer Token-Bucket-Rate-Limiter (pro Schlüssel).

Thread-safe, mit opportunistischem Pruning abgelaufener Buckets (Speicher-DoS).
Bewusst simpel + in-memory: single-instance-Deployment. Für Multi-Instance
später auf Redis o.Ä. auslagern.
"""
from __future__ import annotations

import threading
import time


class RateLimiter:
    def __init__(self, max_per_window: int, window_s: float = 60.0, prune_at: int = 5000) -> None:
        self._max = max_per_window
        self._window = window_s
        self._prune_at = prune_at
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        """True = erlaubt, False = Rate-Limit erreicht."""
        if self._max <= 0:
            return True
        now = time.monotonic()
        with self._lock:
            if len(self._buckets) > self._prune_at:
                for k in [k for k, (_, st) in self._buckets.items() if now - st >= self._window]:
                    del self._buckets[k]
            remaining, start = self._buckets.get(key, (self._max, now))
            if now - start >= self._window:
                remaining, start = self._max, now
            if remaining < 1:
                self._buckets[key] = (remaining, start)
                return False
            self._buckets[key] = (remaining - 1, start)
            return True
