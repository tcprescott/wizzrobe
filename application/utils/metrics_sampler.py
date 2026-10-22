"""Send the ``runtime_stats`` snapshot to Sentry Metrics on a fixed beat.

The same figures the super-admin ``runtime_stats`` MCP tool reads live, kept as
a history in Sentry so a slow afternoon can be read back after the fact. Only
started when :func:`sentry_metrics.enabled` is true. A tick is a few dict
reads and about twenty buffered gauges; the SDK batches them into one envelope.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from application.utils import gc_monitor, runtime_stats, sentry_metrics
from application.utils.background_loop import run_worker_loop

logger = logging.getLogger(__name__)

_last_stalls: Optional[int] = None
_last_gc_pass: float = 0.0
_last_gc_seconds: Dict[str, float] = {}


def emit(snapshot: Dict[str, Any]) -> None:
    global _last_stalls, _last_gc_pass
    g = sentry_metrics.gauge

    g('process.memory_rss', snapshot.get('rss_mb'), unit='megabyte')
    g('process.asyncio_tasks', snapshot.get('asyncio_tasks'))

    loop = snapshot.get('event_loop') or {}
    g('process.cpu', loop.get('cpu_percent'), unit='percent')
    g('loop.lag_p50', loop.get('lag_ms_p50'), unit='millisecond')
    g('loop.lag_p95', loop.get('lag_ms_p95'), unit='millisecond')
    g('loop.lag_max', loop.get('lag_ms_max'), unit='millisecond')
    stalls = loop.get('stalls_since_start')
    if isinstance(stalls, int):
        if _last_stalls is not None and stalls > _last_stalls:
            sentry_metrics.count('loop.stalls', stalls - _last_stalls)
        _last_stalls = stalls

    for p in gc_monitor.passes_after(_last_gc_pass):
        sentry_metrics.distribution('gc.pause', p.seconds, unit='second',
                                    attributes={'generation': str(p.generation)})
        _last_gc_pass = p.started
    for generation, totals in ((snapshot.get('gc') or {}).get('by_generation') or {}).items():
        seconds = totals.get('total_seconds')
        if not isinstance(seconds, (int, float)):
            continue
        previous = _last_gc_seconds.get(generation)
        if previous is not None and seconds > previous:
            sentry_metrics.count('gc.seconds', round(seconds - previous, 3), unit='second',
                                 attributes={'generation': generation})
        _last_gc_seconds[generation] = seconds

    clients = snapshot.get('clients') or {}
    g('clients.open', clients.get('total'))
    g('clients.connected', clients.get('connected'))
    g('http.in_flight', snapshot.get('http_in_flight'))

    online = snapshot.get('online_users') or {}
    g('users.online', online.get('total'))
    for slug, n in (online.get('by_community') or {}).items():
        g('users.online_by_community', n, attributes={'community': slug})

    pool = snapshot.get('db_pool') or {}
    size, idle = pool.get('size'), pool.get('idle')
    g('db_pool.size', size)
    g('db_pool.max', pool.get('max'))
    if size is not None and idle is not None:
        g('db_pool.in_use', size - idle)

    for name, depth in (snapshot.get('queues') or {}).items():
        g('queue.depth', depth, attributes={'queue': name})

    for tenant_id, watch in (snapshot.get('watch_pages_by_tenant_id') or {}).items():
        attrs = {'tenant_id': tenant_id}
        g('watch.live_viewers', watch.get('live_viewers'), attributes=attrs)
        g('watch.static_readers', watch.get('static_readers'), attributes=attrs)
        g('watch.arrivals_per_minute', watch.get('arrivals_last_minute'), attributes=attrs)


async def _tick() -> None:
    emit(runtime_stats.collect())


_loop = run_worker_loop(_tick, sentry_metrics.SAMPLE_SECONDS, 'sentry metrics', logger)


def start() -> None:
    if sentry_metrics.enabled():
        _loop.start()


async def stop() -> None:
    await _loop.stop()
