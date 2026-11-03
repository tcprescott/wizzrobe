"""Active signed-in users, sent to Sentry Metrics every five minutes.

Counts distinct people with any engagement-telemetry row (a page view, a click,
a domain event they caused) in the last 15 minutes, hour and 24 hours, overall
and per community. Read from the database, so the figures survive a restart.
The live "online now" count comes from open tabs instead, in
:mod:`application.utils.metrics_sampler`.

Only runs when Sentry metrics are on and telemetry is recording.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict

from application.repositories.telemetry_repository import TelemetryRepository
from application.repositories.tenant_repository import TenantRepository
from application.utils import sentry_metrics
from application.utils.background_loop import run_worker_loop
from application.utils.environment import telemetry_enabled

logger = logging.getLogger(__name__)

TICK_SECONDS = 300.0

WINDOWS: Dict[str, timedelta] = {
    '15m': timedelta(minutes=15),
    '1h': timedelta(hours=1),
    '24h': timedelta(hours=24),
}


async def emit_active_users(now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    slugs = {t.id: t.slug for t in await TenantRepository.list_all()}
    for label, span in WINDOWS.items():
        total, by_tenant = await TelemetryRepository.distinct_users_since(now - span)
        sentry_metrics.gauge(f'users.active_{label}', total)
        for tenant_id, n in by_tenant.items():
            slug = slugs.get(tenant_id)
            if slug:
                sentry_metrics.gauge(
                    f'users.active_{label}_by_community', n, attributes={'community': slug})


async def _tick() -> None:
    if not telemetry_enabled():
        return
    await emit_active_users()


_loop = run_worker_loop(_tick, TICK_SECONDS, 'active users metrics', logger)


def start() -> None:
    if sentry_metrics.enabled():
        _loop.start()


async def stop() -> None:
    await _loop.stop()
