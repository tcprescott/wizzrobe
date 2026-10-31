"""Rate Limit Repository — shared per-minute request counters.

``RateLimitWindow`` is **global** (no tenant column): keys are IPs, token hashes
and OAuth apps. PostgreSQL only; the single-process limiter never touches it.
"""

from typing import Dict, List

from tortoise import connections

from models import RateLimitWindow


class RateLimitRepository:
    """Atomic counters over :class:`~models.RateLimitWindow`."""

    @staticmethod
    async def hit(keys: List[str], window_start: int) -> Dict[str, int]:
        """Count one request against each key in ``window_start``; return each key's new total."""
        rows = await connections.get('default').execute_query_dict(
            'INSERT INTO ratelimitwindow (key, window_start, hits) '
            'SELECT k, $2, 1 FROM unnest($1::text[]) AS k '
            'ON CONFLICT (key, window_start) DO UPDATE SET hits = ratelimitwindow.hits + 1 '
            'RETURNING key, hits',
            [keys, window_start],
        )
        return {row['key']: row['hits'] for row in rows}

    @staticmethod
    async def delete_before(window_start: int) -> int:
        return await RateLimitWindow.filter(window_start__lt=window_start).delete()
