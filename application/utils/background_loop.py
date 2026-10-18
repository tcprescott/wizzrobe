"""Reusable background-worker loop skeleton.

Every periodic worker (racetime room auto-open, SpeedGaming sync, Discord event
reconcile, service-health monitor, volunteer reminders) hand-rolls the same
``_loop``/``start``/``stop`` + module-global ``_task`` scaffold: a loop that awaits
a ``tick`` coroutine, logs and swallows any failure so it never dies, sleeps a
fixed interval, and can be started once and cancelled cleanly.

:class:`BackgroundLoop` captures that skeleton; a worker keeps a module-level
instance and delegates its ``start``/``stop`` to it. :func:`for_each_tenant_scoped`
captures the companion per-item pattern — iterate a batch, run each item's work
inside its own ``tenant_scope`` with per-item error isolation — so a tick that
fans out over tenant-scoped rows stays a few lines.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import time
import weakref
from typing import Awaitable, Callable, Dict, Iterable, List, Optional, TypeVar

from application.tenant_context import tenant_scope
from application.timezone_context import tz_scope
from application.utils import sentry_metrics
from application.utils.sentry import sentry_tags

_default_logger = logging.getLogger(__name__)

T = TypeVar('T')

TickFn = Callable[[], Awaitable[None]]

#: A tick longer than this is logged at WARNING: it delays the worker's next
#: run, and any synchronous stretch inside it delays every user.
SLOW_TICK_SECONDS = 30.0

_loops: 'weakref.WeakSet[BackgroundLoop]' = weakref.WeakSet()


def worker_stats() -> List[Dict[str, object]]:
    """Timing of every live worker loop, for the ``runtime_stats`` MCP tool."""
    return sorted((loop.stats() for loop in list(_loops)), key=lambda s: str(s['name']))


class BackgroundLoop:
    """A single periodic worker task: run ``tick`` every ``interval`` seconds.

    ``start`` is idempotent (a second call while running is a no-op). A failing
    ``tick`` is logged (``"{name} tick failed: ..."``) and the loop continues.
    ``stop`` cancels the task and clears it, and is safe to call when never
    started.
    """

    def __init__(
        self,
        tick: TickFn,
        *,
        interval: float,
        name: str,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._tick = tick
        self._interval = interval
        self.name = name
        self._logger = logger or _default_logger
        self._task: Optional[asyncio.Task] = None
        self._last_started: Optional[float] = None
        self._last_seconds: Optional[float] = None
        self._max_seconds = 0.0
        self._ticks = 0
        self._failures = 0
        self._last_failed_at: Optional[float] = None
        _loops.add(self)

    async def _loop(self) -> None:
        while True:
            with sentry_tags(worker=self.name):
                self._last_started = time.time()
                began = time.monotonic()
                try:
                    await self._run_tick()
                except Exception as e:  # never let the loop die
                    self._failures += 1
                    sentry_metrics.count('worker.tick_failures', attributes={'worker': self.name})
                    self._last_failed_at = time.time()
                    self._logger.exception('%s tick failed: %s', self.name, e)
                finally:
                    elapsed = time.monotonic() - began
                    self._ticks += 1
                    self._last_seconds = elapsed
                    self._max_seconds = max(self._max_seconds, elapsed)
                    sentry_metrics.distribution(
                        'worker.tick_duration', elapsed, unit='second', attributes={'worker': self.name})
                if elapsed >= SLOW_TICK_SECONDS:
                    self._logger.warning('%s tick took %.1fs', self.name, elapsed)
            await asyncio.sleep(self._interval)

    async def _run_tick(self) -> None:
        # A fresh copy of the loop's context per tick. The loop is one task for
        # the life of the process, so a per-request cache a tick sets (the live
        # feature-flag set) would otherwise be read on every later tick, and a
        # flag toggled after boot would never reach the worker.
        async def tick() -> None:
            await self._tick()

        await asyncio.create_task(tick(), context=contextvars.copy_context())

    def stats(self) -> Dict[str, object]:
        return {
            'name': self.name,
            'running': self._task is not None and not self._task.done(),
            'interval_seconds': self._interval,
            'ticks': self._ticks,
            'failures': self._failures,
            'last_started_at': self._last_started,
            'last_tick_seconds': None if self._last_seconds is None else round(self._last_seconds, 3),
            'max_tick_seconds': round(self._max_seconds, 3),
            'last_failed_at': self._last_failed_at,
        }

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_event_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None


def run_worker_loop(
    tick: TickFn,
    interval: float,
    name: str,
    logger: Optional[logging.Logger] = None,
) -> BackgroundLoop:
    """Construct a :class:`BackgroundLoop` (not yet started).

    Convenience factory so a worker can write
    ``_loop = run_worker_loop(_tick, TICK_SECONDS, 'my worker', logger)`` and
    delegate its ``start``/``stop`` to it.
    """
    return BackgroundLoop(tick, interval=interval, name=name, logger=logger)


async def for_each_tenant_scoped(
    items: Iterable[T],
    handle: Callable[[T], Awaitable[None]],
    *,
    tenant_id_of: Callable[[T], Optional[int]],
    logger: Optional[logging.Logger] = None,
    describe: Callable[[T], object] = repr,
) -> None:
    """Run ``handle(item)`` for each item inside its own ``tenant_scope``.

    Items whose ``tenant_id_of`` is ``None``, or whose community a platform admin
    has deactivated, are skipped; a per-item failure is
    logged (with ``describe(item)`` for context) and isolated so the rest of the
    batch still runs. This is the fan-out shape shared by the tenant-scoped ticks.

    Binds the community's display timezone as well as its tenant, so a worker
    that formats a time gets that community's clock rather than the fallback.
    """
    from application.repositories.tenant_repository import TenantRepository
    from application.services.timezone_service import TimezoneService

    log = logger or _default_logger
    batch = [(item, tenant_id_of(item)) for item in items]
    active = await TenantRepository.active_ids(t for _, t in batch if t is not None)
    for item, tenant_id in batch:
        if tenant_id is None or tenant_id not in active:
            continue
        # The tenant is bound around the except too, so the failure is logged
        # (and reaches Sentry) tagged with the community it happened in.
        with tenant_scope(tenant_id), sentry_tags(work_item=describe(item)):
            try:
                # The community's own clock, bound alongside its tenant: a worker has
                # no viewer, so anything it renders locally (a reminder DM, an export)
                # would otherwise fall back to the default zone and tell every
                # community the same wrong time.
                tz = await TimezoneService.tenant_timezone_name(tenant_id)
                with tz_scope(tz):
                    await handle(item)
            except Exception:
                log.exception(
                    'scoped work failed for %s (tenant %s)', describe(item), tenant_id,
                )
