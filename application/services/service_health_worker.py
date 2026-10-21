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

from application.utils.background_loop import run_worker_loop

logger = logging.getLogger(__name__)

# Probes reach external hosts, so a gentle cadence is plenty — an outage surfaces
# within a couple of minutes, and the board can always be force-refreshed.
TICK_SECONDS = 120


async def _tick() -> None:
    from application.services.service_health_service import ServiceHealthService
    results = await ServiceHealthService().refresh()
    unhealthy = [r for r in results if r.status.value in ('down', 'credential_warning', 'degraded')]
    if unhealthy:
        logger.info('Service health: %s dependency/dependencies need attention: %s',
                    len(unhealthy), ', '.join(f'{r.label}={r.status.value}' for r in unhealthy))


_loop = run_worker_loop(_tick, TICK_SECONDS, 'Service health', logger)


def start() -> None:
    _loop.start()


async def stop() -> None:
    await _loop.stop()
