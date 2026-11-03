"""A per-key sliding-window counter for "at most N of these per hour" rules.

The app runs single-worker, so an in-process window is enough; it resets on a
restart, which only ever loosens a limit for one window.
"""

import time
from collections import defaultdict, deque
from typing import Deque, Dict, Hashable


class SlotLimiter:
    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: Dict[Hashable, Deque[float]] = defaultdict(deque)

    def take(self, key: Hashable) -> bool:
        """Count one use for ``key``; False (and nothing counted) when it is over the limit."""
        now = time.monotonic()
        hits = self._hits[key]
        while hits and hits[0] < now - self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True

    def reset(self) -> None:
        self._hits.clear()
