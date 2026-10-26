"""A point-in-time picture of this process's load, for diagnosing a slow app.

Read by :class:`~application.services.runtime_stats_service.RuntimeStatsService`
and served through the super-admin ``runtime_stats`` MCP tool, so a Claude
session can see live load without shell access to the host. Every probe is
best-effort: a figure the platform cannot provide comes back ``None`` rather
than failing the whole snapshot.
"""

from __future__ import annotations

import asyncio
import os
import re
import resource
import sys
import time
from typing import Any, Dict, Optional

from application.utils import gc_monitor, live_audience
from application.utils.background_loop import worker_stats
from application.utils.coroutine_queue import queue_depths, queue_worker_stats
from application.utils.loop_watchdog import watchdog

_PROCESS_STARTED = time.time()

_http_in_flight = 0


def http_request_started() -> None:
    global _http_in_flight
    _http_in_flight += 1


def http_request_finished() -> None:
    global _http_in_flight
    _http_in_flight = max(0, _http_in_flight - 1)


def _rss_mb() -> Optional[float]:
    try:
        with open('/proc/self/status') as status:
            for line in status:
                if line.startswith('VmRSS:'):
                    return round(int(line.split()[1]) / 1024, 1)
    except OSError:
        pass
    try:
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(peak / (1024 * 1024 if sys.platform == 'darwin' else 1024), 1)
    except Exception:
        return None


def _clients() -> Dict[str, Optional[int]]:
    try:
        from nicegui import Client
        instances = list(Client.instances.values())
    except Exception:
        return {'total': None, 'connected': None}
    return {
        'total': len(instances),
        'connected': sum(1 for c in instances if c.has_socket_connection),
    }


_COMMUNITY_PATH_RE = re.compile(r'^/t/([a-z0-9][a-z0-9-]*)(?:/|$)')


def _online_users() -> Dict[str, Any]:
    """Distinct signed-in people with a live tab, overall and per community.

    A tab's community is the ``/t/<slug>`` it was opened on; one person with
    tabs in two communities counts once in each and once in the total.
    """
    try:
        from nicegui import Client, app
        stores = app.storage._users
        instances = list(Client.instances.values())
    except Exception:
        return {'total': None, 'by_community': {}}
    everyone: set = set()
    by_community: Dict[str, set] = {}
    for client in instances:
        try:
            if not client.has_socket_connection:
                continue
            request = client.request
            store: Dict[str, Any] = stores.get(str(request.session.get('id'))) or {}
            ident = store.get('user_id') or store.get('discord_id')
            if not ident:
                continue
            everyone.add(ident)
            match = _COMMUNITY_PATH_RE.match(request.url.path)
            if match:
                by_community.setdefault(match.group(1), set()).add(ident)
        except Exception:
            continue
    return {
        'total': len(everyone),
        'by_community': {slug: len(ids) for slug, ids in sorted(by_community.items())},
    }


def _db_pool() -> Dict[str, Optional[int]]:
    try:
        from tortoise import Tortoise
        pool = getattr(Tortoise.get_connection('default'), '_pool', None)
        if pool is None:
            return {'size': None, 'idle': None, 'max': None}
        return {'size': pool.get_size(), 'idle': pool.get_idle_size(), 'max': pool.get_max_size()}
    except Exception:
        return {'size': None, 'idle': None, 'max': None}


def _watch_pages() -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for tenant_id in live_audience.tenant_ids():
        snap = live_audience.snapshot(tenant_id)
        out[str(tenant_id)] = {
            'live_viewers': snap.live_viewers,
            'static_readers': snap.static_readers,
            'arrivals_last_minute': snap.arrivals_last_minute,
            'static_mode_tripped': snap.tripped,
        }
    return out


def collect() -> Dict[str, Any]:
    try:
        tasks: Optional[int] = len(asyncio.all_tasks())
    except RuntimeError:
        tasks = None
    return {
        'collected_at': time.time(),
        'uptime_seconds': round(time.time() - _PROCESS_STARTED),
        'release': os.environ.get('SENTRY_RELEASE') or os.environ.get('GIT_SHA') or None,
        'rss_mb': _rss_mb(),
        'asyncio_tasks': tasks,
        'clients': _clients(),
        'online_users': _online_users(),
        'http_in_flight': _http_in_flight,
        'queues': queue_depths(),
        'db_pool': _db_pool(),
        'event_loop': watchdog.snapshot(),
        'gc': gc_monitor.snapshot(),
        'workers': worker_stats(),
        'queue_workers': queue_worker_stats(),
        'watch_pages_by_tenant_id': _watch_pages(),
    }
