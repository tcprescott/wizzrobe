"""Cloud Tasks follow-ups for workers that wait on something specific.

A seed roll waits on one provider task and a data export on one queued row.
Polling them from a minute-long Scheduler burst keeps an instance awake while
nothing is happening, so with ``CLOUD_TASKS_LOCATION`` set (and
``WORKER_MODE=scheduled``) each piece of work enqueues its own check instead:

- the service that starts the work calls :func:`enqueue` with the worker's key
  and a small payload;
- Cloud Tasks POSTs that payload to ``/internal/tasks/<key>``
  (``api/worker_ticks.py``) at the scheduled time;
- the worker's ``task_handler`` (named on its :class:`WorkerSpec`) does one
  step and returns the delay before the next one, or ``None`` when done.

The worker's Scheduler job stays as a slow safety net
(:data:`SAFETY_NET_MINUTES`): it sweeps stale rows and picks up anything a
failed :func:`enqueue` left behind, so a Cloud Tasks outage slows work down
rather than losing it. With Cloud Tasks off this module does nothing and the
workers poll as before.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

TASK_PATH_PREFIX = '/internal/tasks'

SAFETY_NET_MINUTES = 5


def _location() -> str:
    return (os.environ.get('CLOUD_TASKS_LOCATION') or '').strip().strip('/')


def enabled() -> bool:
    from application.worker_registry import WorkerMode, worker_mode
    return worker_mode() is WorkerMode.SCHEDULED and bool(_location())


def queue_name(key: str) -> str:
    """The queue a worker's tasks go on: ``<CLOUD_TASKS_QUEUE_PREFIX><key>``, hyphenated."""
    prefix = (os.environ.get('CLOUD_TASKS_QUEUE_PREFIX') or 'wizzrobe-').strip()
    return f'{prefix}{key}'.replace('_', '-')


def queue_path(key: str) -> str:
    return f'{_location()}/queues/{queue_name(key)}'


async def enqueue(key: str, payload: Optional[Dict[str, Any]] = None, *, delay_seconds: float = 0.0) -> bool:
    """Schedule a step of worker ``key``'s work; ``False`` if nothing was queued.

    Never raises: a failure is logged, and the worker's safety-net tick does the
    work a few minutes late.
    """
    if not enabled():
        return False
    from application.utils.clients import cloud_tasks_client

    audience = (os.environ.get('WORKER_TICK_AUDIENCE') or '').strip()
    account = (os.environ.get('WORKER_TICK_SERVICE_ACCOUNT') or '').strip()
    if not audience or not account:
        logger.error('CLOUD_TASKS_LOCATION needs WORKER_TICK_AUDIENCE and WORKER_TICK_SERVICE_ACCOUNT')
        return False
    body = cloud_tasks_client.task_body(
        url=f'{audience.rstrip("/")}{TASK_PATH_PREFIX}/{key}',
        payload=payload or {}, delay_seconds=delay_seconds,
        service_account=account, audience=audience,
    )
    try:
        await cloud_tasks_client.create_task(queue_path(key), body)
    except Exception:
        logger.warning('could not enqueue a %s task; the safety-net tick will pick it up', key, exc_info=True)
        return False
    return True
