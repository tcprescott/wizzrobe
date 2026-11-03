"""Notice a background worker or queue that has gone quiet, and say so in Sentry.

A worker that dies or hangs stops sending metrics, so Sentry sees silence
rather than a failure and nothing alerts. This loop checks every
:class:`BackgroundLoop` and :class:`CoroutineQueue` every
:data:`CHECK_SECONDS` and logs one ERROR (its own Sentry issue per worker) when
one stalls, and one WARNING when it recovers. The loops and queues heal
themselves (tick timeouts, restart on exit); this is for when that hasn't
worked.
"""

from __future__ import annotations

import logging
import time
from typing import Dict, Optional, Set

from application.utils.background_loop import live_loops, report_worker_problem, run_worker_loop
from application.utils.coroutine_queue import live_queues

logger = logging.getLogger(__name__)

CHECK_SECONDS = 30.0

_alerting: Set[str] = set()


def check(now: Optional[float] = None) -> Dict[str, str]:
    """Report newly stalled and recovered workers; returns what is stalled now."""
    now = time.monotonic() if now is None else now
    stalled: Dict[str, str] = {}
    for loop in live_loops():
        reason = loop.stall_reason(now)
        if reason is not None:
            stalled[loop.name] = reason
    for queue in live_queues():
        reason = queue.stall_reason(now)
        if reason is not None:
            stalled[queue.name] = reason
    for name, reason in stalled.items():
        if name not in _alerting:
            report_worker_problem(name, 'Background worker %s is stalled: %s', name, reason, logger=logger)
    for name in _alerting - stalled.keys():
        logger.warning('Background worker %s is running again', name)
    _alerting.clear()
    _alerting.update(stalled)
    return stalled


async def _tick() -> None:
    check()


_loop = run_worker_loop(_tick, CHECK_SECONDS, 'worker watchdog', logger)


def start() -> None:
    _loop.start()


async def stop() -> None:
    await _loop.stop()
    _alerting.clear()
