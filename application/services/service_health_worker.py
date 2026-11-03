"""Platform service-health monitor worker (PR 5).

A background loop (modeled on ``discord_event_worker``) that periodically refreshes
every dependency probe, keeping the ``/platform`` health board warm and driving the
alert-on-transition path without anyone loading the page. Always started; a
dependency in mock mode (``MOCK_*``) reports ``mock`` without being contacted.

Runs with **no tenant scope** — every probe is platform-level (the racetime and
Challonge probes read across all tenants via explicitly-unscoped queries). The loop
never dies: a failing tick is logged and retried next interval.
"""

import logging
import time
from typing import Optional

from application.utils.background_loop import run_worker_loop

logger = logging.getLogger(__name__)

TICK_SECONDS = 120

# Skip boot ticks: the gateway and racetime bots aren't connected yet and would read as down.
STARTUP_GRACE_SECONDS = 60

_started_at: Optional[float] = None


async def _tick() -> None:
    if _started_at is not None and time.monotonic() - _started_at < STARTUP_GRACE_SECONDS:
        return
    from application.services.service_health_service import ServiceHealthService
    results = await ServiceHealthService().refresh()
    unhealthy = [r for r in results if r.status.value in ('down', 'credential_warning', 'degraded')]
    if unhealthy:
        logger.info('Service health: %s dependency/dependencies need attention: %s',
                    len(unhealthy), ', '.join(f'{r.label}={r.status.value}' for r in unhealthy))


_loop = run_worker_loop(_tick, TICK_SECONDS, 'Service health', logger)


def start() -> None:
    global _started_at
    _started_at = time.monotonic()
    _loop.start()


async def stop() -> None:
    await _loop.stop()
