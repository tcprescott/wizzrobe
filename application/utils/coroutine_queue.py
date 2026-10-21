"""Serial coroutine worker queue.

A single-consumer FIFO of coroutines drained in order by one background worker
task. A coroutine that raises is logged (traceback preserved so it reaches logs +
Sentry) and swallowed, and one that runs past :data:`ITEM_TIMEOUT_SECONDS` is
cancelled, so one bad item can neither kill the worker nor hold up everything
queued behind it. A worker task that ends anyway is reported and restarted. Layer-neutral: both the
notification pipeline (``services.discord_queue``) and the event-dispatch pipeline
(``events.dispatch_queue``) own an instance — one implementation, two queues.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
import weakref
from collections.abc import Coroutine
from types import ModuleType
from typing import Optional

from application.utils.background_loop import (
    STALL_GRACE_SECONDS,
    being_cancelled,
    report_worker_problem,
    restart_delay,
)
from application.utils.sentry import sentry_tags

_default_logger = logging.getLogger(__name__)

_DRAIN_SECONDS = 5
_DRAIN_POLL_SECONDS = 0.05

#: Longest one queued call may run. Well above a webhook's worst case (three
#: 10s attempts plus backoff) and a DM held up by a Discord rate limit.
ITEM_TIMEOUT_SECONDS = 300.0

_queues: 'weakref.WeakSet[CoroutineQueue]' = weakref.WeakSet()


def queue_depths() -> dict[str, int]:
    """Items waiting in each live queue, for ``runtime_stats`` and Sentry Metrics."""
    return {q.name: q.qsize() for q in sorted(list(_queues), key=lambda q: q.name)}


def live_queues() -> list['CoroutineQueue']:
    return sorted(list(_queues), key=lambda q: q.name)


def queue_worker_stats() -> list[dict[str, object]]:
    """Health of every live queue's worker, for the ``runtime_stats`` MCP tool."""
    return [q.stats() for q in live_queues()]


class CoroutineQueue:
    """Owns a queue + its lone worker task.

    ``name`` shapes the worker-error log line (``"{name} worker error"``).
    ``drop_warning`` is the ``stop()`` warning template (one ``%d`` for the count);
    it defaults to a generic message. Pass the owning module's ``logger`` so log
    records are attributed to that module.
    """

    def __init__(
        self,
        name: str,
        *,
        logger: Optional[logging.Logger] = None,
        drop_warning: Optional[str] = None,
    ) -> None:
        self.name = name
        self._logger = logger or _default_logger
        self._drop_warning = drop_warning or (
            f"{name} stopping with %d item(s) still queued — they will not run"
        )
        self._queue: asyncio.Queue[Coroutine] = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None
        self._busy = False
        self._item_began: Optional[float] = None
        self._started = False
        self._timeouts = 0
        self._restarts = 0
        self._consecutive_restarts = 0
        self._restart_handle: Optional[asyncio.TimerHandle] = None
        _queues.add(self)

    def qsize(self) -> int:
        return self._queue.qsize()

    async def _worker(self) -> None:
        while True:
            coro = await self._queue.get()
            self._busy = True
            self._item_began = time.monotonic()
            label = coroutine_label(coro)
            with sentry_tags(queue=self.name, queued_call=label):
                try:
                    async with asyncio.timeout(ITEM_TIMEOUT_SECONDS):
                        await coro
                    self._consecutive_restarts = 0
                except TimeoutError:
                    self._timeouts += 1
                    report_worker_problem(
                        self.name, '%s: %s ran past %.0fs and was cancelled',
                        self.name, label, ITEM_TIMEOUT_SECONDS, logger=self._logger)
                except asyncio.CancelledError:
                    if being_cancelled():
                        raise
                    report_worker_problem(
                        self.name, '%s: %s was cancelled from inside; moving on',
                        self.name, label, logger=self._logger, exc_info=True)
                except Exception:
                    self._logger.exception("%s worker error in %s", self.name, label)
                finally:
                    self._busy = False
                    self._item_began = None
                    self._queue.task_done()

    def start(self) -> None:
        self._started = True
        self._spawn()

    def _spawn(self) -> None:
        self._restart_handle = None
        self._worker_task = asyncio.get_event_loop().create_task(self._worker(), name=f'queue: {self.name}')
        self._worker_task.add_done_callback(self._on_done)

    def _on_done(self, task: asyncio.Task) -> None:
        if not self._started or task is not self._worker_task:
            return
        exc = None if task.cancelled() else task.exception()
        self._busy = False
        self._item_began = None
        self._restarts += 1
        self._consecutive_restarts += 1
        delay = restart_delay(self._consecutive_restarts)
        report_worker_problem(
            self.name, '%s worker stopped unexpectedly (%s); restarting in %.0fs',
            self.name, 'cancelled' if exc is None else repr(exc), delay, logger=self._logger)
        self._restart_handle = task.get_loop().call_later(delay, self._respawn)

    def _respawn(self) -> None:
        if self._started and (self._worker_task is None or self._worker_task.done()):
            self._spawn()

    def stall_reason(self, now: Optional[float] = None) -> Optional[str]:
        """Why this queue looks stuck, or ``None`` while it's draining normally."""
        if not self._started:
            return None
        now = time.monotonic() if now is None else now
        if self._item_began is not None:
            running = now - self._item_began
            if running > ITEM_TIMEOUT_SECONDS + STALL_GRACE_SECONDS:
                return f'one call has been running for {running:.0f}s with {self.qsize()} waiting behind it'
            return None
        if (self._worker_task is None or self._worker_task.done()) and self._restart_handle is None:
            return f'worker is not running with {self.qsize()} waiting'
        return None

    def stats(self) -> dict[str, object]:
        running_for = None if self._item_began is None else time.monotonic() - self._item_began
        return {
            'name': self.name,
            'running': self._worker_task is not None and not self._worker_task.done(),
            'stalled': self.stall_reason() is not None,
            'depth': self.qsize(),
            'item_running_seconds': None if running_for is None else round(running_for, 1),
            'timeouts': self._timeouts,
            'restarts': self._restarts,
        }

    async def stop(self) -> None:
        self._started = False
        if self._restart_handle is not None:
            self._restart_handle.cancel()
            self._restart_handle = None
        if self._worker_task is None:
            return
        # A short drain so a routine restart delivers what was already queued
        # (the DM a click just sent) rather than discarding it.
        # Polled rather than ``Queue.join()``: the queue outlives an event loop
        # in tests, and join's internal Event is bound to the first loop.
        for _ in range(int(_DRAIN_SECONDS / _DRAIN_POLL_SECONDS)):
            if not (self._queue.qsize() or self._busy):
                break
            await asyncio.sleep(_DRAIN_POLL_SECONDS)
        pending = self._queue.qsize()
        if pending:
            self._logger.warning(self._drop_warning, pending)
        self._worker_task.cancel()
        try:
            await self._worker_task
        except asyncio.CancelledError:
            pass
        self._worker_task = None
        self.discard_pending()

    def enqueue(self, coro: Coroutine) -> None:
        self._queue.put_nowait(coro)

    def discard_pending(self) -> int:
        """Close every queued coroutine without running it, and say how many.

        For a process that queued work it will never run: a script (the dev
        seed starts brackets, which queue matchup DMs, with no worker running)
        or ``stop()``. Left alone, each dropped coroutine is a ``RuntimeWarning:
        coroutine ... was never awaited`` at interpreter exit, which reads as a
        bug rather than as a send that deliberately did not happen.

        An un-started wrapper (``discord_queue._run_in_tenant_scope``) holds the
        real send as an argument, which closing the wrapper does not reach, so
        un-started coroutines among its arguments are closed too.
        """
        dropped = 0
        while True:
            try:
                coro = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return dropped
            frame = getattr(coro, 'cr_frame', None)
            inner = [v for v in (frame.f_locals.values() if frame else ())
                     if isinstance(v, Coroutine)]
            coro.close()
            for arg in inner:
                arg.close()
            self._queue.task_done()
            dropped += 1


def coroutine_label(coro: Coroutine) -> str:
    """Name a queued coroutine by what it will run, not by its wrapper.

    Both queues wrap the real call (``_run_in_tenant_scope(send_dm(...))``), so
    the wrapper's own name says nothing; the un-started wrapper still holds the
    inner coroutine in its frame locals, which is the name worth reporting.
    """
    name = getattr(coro, '__qualname__', type(coro).__name__)
    frame = getattr(coro, 'cr_frame', None)
    if frame is not None:
        for value in frame.f_locals.values():
            if isinstance(value, Coroutine):
                return f"{name}({getattr(value, '__qualname__', type(value).__name__)})"
    return name


def bind_module_state(module_name: str, queue: CoroutineQueue) -> None:
    """Expose ``queue``'s ``_queue``/``_worker_task`` as live module globals.

    Both queue modules used to hand-roll module-level ``_queue`` and
    ``_worker_task`` globals; callers and tests reach into them by name. After the
    extraction those live on the ``CoroutineQueue`` instance, so this forwards
    reads and writes of those two names on the module to the instance, keeping the
    pre-extraction surface intact.
    """
    module = sys.modules[module_name]
    proxied = ('_queue', '_worker_task')

    class _BoundModule(ModuleType):
        def __getattr__(self, name: str):
            if name in proxied:
                return getattr(queue, name)
            raise AttributeError(name)

        def __setattr__(self, name: str, value) -> None:
            if name in proxied:
                setattr(queue, name, value)
            else:
                super().__setattr__(name, value)

    module.__class__ = _BoundModule
