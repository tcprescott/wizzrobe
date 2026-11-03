"""Shared API rate-limit counters for running more than one instance.

With ``MULTI_INSTANCE`` set, ``api/rate_limit.py`` counts requests here instead
of in process memory, so one client gets one budget however many instances
serve it. Windows are fixed minutes rather than the in-memory limiter's sliding
minute, and a refused request still counts, so a client hammering past its
limit stays refused until the window turns over.
"""

import random
import time
from typing import Dict, List, Optional

from tortoise import connections

from application.repositories import RateLimitRepository
from application.utils.environment import env_flag

WINDOW_SECONDS = 60
_PRUNE_ONE_IN = 200


def shared_counters_enabled() -> bool:
    if not env_flag('MULTI_INSTANCE'):
        return False
    try:
        return connections.get('default').capabilities.dialect == 'postgres'
    except Exception:
        return False


class RateLimitService:
    """Counts requests per key per fixed minute, across every instance."""

    def __init__(self) -> None:
        self.repository = RateLimitRepository()

    async def over_limit(self, keys: List[str], limits: Dict[str, int], now: Optional[float] = None) -> Optional[int]:
        """Count one request for every key; return seconds until retry if any key passed its limit, else ``None``."""
        now = time.time() if now is None else now
        window = int(now // WINDOW_SECONDS)
        counts = await self.repository.hit(keys, window)
        if random.randrange(_PRUNE_ONE_IN) == 0:
            await self.repository.delete_before(window - 1)
        if any(counts.get(key, 0) > limits[key] for key in keys):
            return max(1, int((window + 1) * WINDOW_SECONDS - now) + 1)
        return None
