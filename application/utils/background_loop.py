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

import sentry_sdk

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

#: A tick still running after this is cancelled and counted as a failure, so
#: one ``await`` that never returns can't freeze the worker for good. Workers
#: whose ticks are legitimately long pass their own ``tick_timeout``.
TICK_TIMEOUT_SECONDS = 300.0

#: How long the watchdog lets a worker run past what its interval (or its tick
#: timeout, while a tick is running) allows before calling it stalled.
STALL_GRACE_SECONDS = 60.0

_RESTART_FIRST_SECONDS = 5.0
_RESTART_MAX_SECONDS = 60.0

_loops: 'weakref.WeakSet[BackgroundLoop]' = weakref.WeakSet()


def worker_stats() -> List[Dict[str, object]]:
    """Timing of every live worker loop, for the ``runtime_stats`` MCP tool."""
    return sorted((loop.stats() for loop in list(_loops)), key=lambda s: str(s['name']))


def live_loops() -> List['BackgroundLoop']:
    return sorted(list(_loops), key=lambda loop: loop.name)


def being_cancelled() -> bool:
    """Whether someone asked the *current* task to stop.

    Tells a real ``stop()`` apart from a ``CancelledError`` that leaked out of
    the work itself (a library cancelling its own inner future), which
    ``except Exception`` doesn't catch and which used to end a worker silently.
    """
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0


def report_worker_problem(name: str, message: str, *args: object, logger: Optional[logging.Logger] = None,
                          exc_info: bool = False) -> None:
    """Log a worker fault as an ERROR, grouped in Sentry by worker name.

    Without its own fingerprint every worker's fault lands in one issue, since
    Sentry groups a log record by its message template.
    """
    with sentry_sdk.new_scope() as scope:
        scope.fingerprint = ['background-worker-fault', name]
        scope.set_tag('worker', name)
        (logger or _default_logger).error(message, *args, exc_info=exc_info)


def restart_delay(consecutive: int) -> float:
    return min(_RESTART_FIRST_SECONDS * 2 ** max(consecutive - 1, 0), _RESTART_MAX_SECONDS)


class BackgroundLoop:
    """A single periodic worker task: run ``tick`` every ``interval`` seconds.

    ``start`` is idempotent (a second call while running is a no-op). A failing
    ``tick`` is logged (``"{name} tick failed: ..."``) and the loop continues;
    one that runs past ``tick_timeout`` is cancelled and reported. If the loop
    task ends any other way than ``stop``, it is reported and restarted after a
    backoff. ``stop`` cancels the task and clears it, and is safe to call when
    never started.
    """

    def __init__(
        self,
        tick: TickFn,
        *,
        interval: float,
        name: str,
        logger: Optional[logging.Logger] = None,
        tick_timeout: Optional[float] = None,
    ) -> None:
        self._tick = tick
        self._interval = interval
        self._tick_timeout = tick_timeout or TICK_TIMEOUT_SECONDS
        self.name = name
        self._logger = logger or _default_logger
        self._task: Optional[asyncio.Task] = None
        self._last_started: Optional[float] = None
        self._last_seconds: Optional[float] = None
        self._max_seconds = 0.0
        self._ticks = 0
        self._failures = 0
        self._last_failed_at: Optional[float] = None
        self._started = False
        self._started_mono: Optional[float] = None
        self._tick_began: Optional[float] = None
        self._last_finished: Optional[float] = None
        self._timeouts = 0
        self._restarts = 0
        self._consecutive_restarts = 0
        self._restart_handle: Optional[asyncio.TimerHandle] = None
        _loops.add(self)

    async def _loop(self) -> None:
        while True:
            with sentry_tags(worker=self.name):
                self._last_started = time.time()
                began = time.monotonic()
                self._tick_began = began
                ok = False
                try:
                    async with asyncio.timeout(self._tick_timeout):
                        await self._run_tick()
                    ok = True
                except TimeoutError:
                    self._record_failure()
                    self._timeouts += 1
                    report_worker_problem(
                        self.name, '%s tick ran past %.0fs and was cancelled',
                        self.name, self._tick_timeout, logger=self._logger)
                except asyncio.CancelledError:
                    if being_cancelled():
                        raise
                    self._record_failure()
                    report_worker_problem(
                        self.name, '%s tick was cancelled from inside; carrying on',
                        self.name, logger=self._logger, exc_info=True)
                except Exception as e:  # never let the loop die
                    self._record_failure()
                    self._logger.exception('%s tick failed: %s', self.name, e)
                finally:
                    elapsed = time.monotonic() - began
                    self._tick_began = None
                    self._last_finished = time.monotonic()
                    self._ticks += 1
                    self._last_seconds = elapsed
                    self._max_seconds = max(self._max_seconds, elapsed)
                    sentry_metrics.distribution(
                        'worker.tick_duration', elapsed, unit='second', attributes={'worker': self.name})
                if ok:
                    self._consecutive_restarts = 0
                if elapsed >= SLOW_TICK_SECONDS:
                    self._logger.warning('%s tick took %.1fs', self.name, elapsed)
            await asyncio.sleep(self._interval)

    def _record_failure(self) -> None:
        self._failures += 1
        sentry_metrics.count('worker.tick_failures', attributes={'worker': self.name})
        self._last_failed_at = time.time()

    async def _run_tick(self) -> None:
        # A fresh copy of the loop's context per tick. The loop is one task for
        # the life of the process, so a per-request cache a tick sets (the live
        # feature-flag set) would otherwise be read on every later tick, and a
        # flag toggled after boot would never reach the worker.
        async def tick() -> None:
            await self._tick()

        await asyncio.create_task(tick(), context=contextvars.copy_context())

    def stats(self) -> Dict[str, object]:
        running_for = None if self._tick_began is None else time.monotonic() - self._tick_began
        return {
            'name': self.name,
            'enabled': self._started,
            'running': self._task is not None and not self._task.done(),
            'stalled': self.stall_reason() is not None,
            'interval_seconds': self._interval,
            'tick_timeout_seconds': self._tick_timeout,
            'ticks': self._ticks,
            'failures': self._failures,
            'timeouts': self._timeouts,
            'restarts': self._restarts,
            'last_started_at': self._last_started,
            'last_tick_seconds': None if self._last_seconds is None else round(self._last_seconds, 3),
            'tick_running_seconds': None if running_for is None else round(running_for, 1),
            'max_tick_seconds': round(self._max_seconds, 3),
            'last_failed_at': self._last_failed_at,
        }

    def stall_reason(self, now: Optional[float] = None) -> Optional[str]:
        """Why this worker looks stuck or dead, or ``None`` while it's healthy.

        A stopped or never-started worker (switched off by config) is not
        stalled. A dead loop restarts by itself, so it only counts once the
        restart is overdue too.
        """
        if not self._started or self._started_mono is None:
            return None
        now = time.monotonic() if now is None else now
        if self._tick_began is not None:
            running = now - self._tick_began
            if running > self._tick_timeout + STALL_GRACE_SECONDS:
                return f'tick has been running for {running:.0f}s and did not stop at its {self._tick_timeout:.0f}s limit'
            return None
        since = self._last_finished if self._last_finished is not None else self._started_mono
        quiet = now - since
        allowed = self._interval + STALL_GRACE_SECONDS
        if self._task is None or self._task.done():
            allowed = max(allowed, _RESTART_MAX_SECONDS + STALL_GRACE_SECONDS)
            if quiet > allowed:
                return f'loop is not running and has not ticked for {quiet:.0f}s'
            return None
        if quiet > allowed:
            return f'no tick for {quiet:.0f}s (runs every {self._interval:.0f}s)'
        return None

    def start(self) -> None:
        if self._task is None:
            self._started = True
            self._started_mono = time.monotonic()
            self._spawn()

    def _spawn(self) -> None:
        self._restart_handle = None
        self._task = asyncio.get_event_loop().create_task(self._loop(), name=f'worker: {self.name}')
        self._task.add_done_callback(self._on_done)

    def _on_done(self, task: asyncio.Task) -> None:
        if not self._started or task is not self._task:
            return
        exc = None if task.cancelled() else task.exception()
        self._restarts += 1
        self._consecutive_restarts += 1
        delay = restart_delay(self._consecutive_restarts)
        report_worker_problem(
            self.name, '%s loop stopped unexpectedly (%s); restarting in %.0fs',
            self.name, 'cancelled' if exc is None else repr(exc), delay, logger=self._logger)
        self._restart_handle = task.get_loop().call_later(delay, self._respawn)

    def _respawn(self) -> None:
        if self._started and (self._task is None or self._task.done()):
            self._spawn()

    async def stop(self) -> None:
        self._started = False
        if self._restart_handle is not None:
            self._restart_handle.cancel()
            self._restart_handle = None
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
    tick_timeout: Optional[float] = None,
) -> BackgroundLoop:
    """Construct a :class:`BackgroundLoop` (not yet started).

    Convenience factory so a worker can write
    ``_loop = run_worker_loop(_tick, TICK_SECONDS, 'my worker', logger)`` and
    delegate its ``start``/``stop`` to it.
    """
    return BackgroundLoop(tick, interval=interval, name=name, logger=logger, tick_timeout=tick_timeout)


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
    # Once per community, not per item: a reminder worker hands this hundreds of
    # rows from one community, and re-reading the tenant for each was most of
    # its tick.
    zones: Dict[int, str] = {}
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
                if tenant_id not in zones:
                    zones[tenant_id] = await TimezoneService.tenant_timezone_name(tenant_id)
                with tz_scope(zones[tenant_id]):
                    await handle(item)
            except Exception:
                log.exception(
                    'scoped work failed for %s (tenant %s)', describe(item), tenant_id,
                )
