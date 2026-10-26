"""Delete an archived event's private data 30 days after it was archived, once a day.

Emergency contacts and accommodation requests are given for one event and are
needed only while it runs. Thirty days after archiving (Thomas's pick,
2026-10-26) this deletes them and stamps ``Event.pii_purged_at``, which the
archived banner reads to say they were removed. Unarchiving doesn't bring them
back. /about/privacy says so.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from application.event_writes import archive_maintenance
from application.repositories.event_repository import EventRepository
from application.tenant_context import tenant_scope
from application.utils.background_loop import run_worker_loop

logger = logging.getLogger(__name__)

TICK_SECONDS = 24 * 3600.0
PRIVATE_DATA_KEPT = timedelta(days=30)


async def purge(now: Optional[datetime] = None) -> int:
    """Purge every event due; returns how many events were purged."""
    now = now or datetime.now(timezone.utc)
    events = await EventRepository.due_for_private_data_purge(now - PRIVATE_DATA_KEPT)
    for event in events:
        with tenant_scope(event.tenant_id), archive_maintenance():
            contacts, requests = await EventRepository.purge_private_data(event, now)
        logger.info(
            'event retention: %s (event %s) lost %d emergency contacts and %d accommodation requests',
            event.name, event.id, contacts, requests,
        )
    return len(events)


async def _tick() -> None:
    await purge()


_loop = run_worker_loop(_tick, TICK_SECONDS, 'event retention', logger)


def start() -> None:
    _loop.start()


async def stop() -> None:
    await _loop.stop()
