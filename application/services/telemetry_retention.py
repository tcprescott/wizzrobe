"""Delete engagement telemetry older than 13 months, once a day.

Telemetry grows by about 1.5M rows a year for an active community and nothing
else ever removes it, so every report over it slows down year on year. Thirteen
months keeps this year's event and last year's side by side for planning
(Thomas's pick, 2026-10-18).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from application.repositories.telemetry_repository import TelemetryRepository
from application.utils.background_loop import run_worker_loop

logger = logging.getLogger(__name__)

TICK_SECONDS = 24 * 3600.0
RETENTION = timedelta(days=396)


async def prune(now: Optional[datetime] = None) -> int:
    """Delete rows older than :data:`RETENTION`; returns how many went."""
    now = now or datetime.now(timezone.utc)
    deleted = await TelemetryRepository.delete_before(now - RETENTION)
    if deleted:
        logger.info('telemetry retention: deleted %d rows older than %s', deleted, (now - RETENTION).date())
    return deleted


async def _tick() -> None:
    await prune()


# A first prune after a long gap deletes months of rows in one statement.
_loop = run_worker_loop(_tick, TICK_SECONDS, 'telemetry retention', logger, tick_timeout=1800)


def start() -> None:
    _loop.start()


async def stop() -> None:
    await _loop.stop()
