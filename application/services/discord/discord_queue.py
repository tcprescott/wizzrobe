"""Serial Discord-notification queue.

A single instance of the shared ``CoroutineQueue`` — the one chokepoint every
Discord notification flows through. ``enqueue`` puts a send coroutine on the
queue; the lone worker awaits them in order and logs (never swallows silently)
any failure so it reaches logs + Sentry.
"""

import asyncio
import logging
from collections.abc import Coroutine
from typing import Optional

from application.event_context import event_scope, get_current_event_id
from application.tenant_context import get_current_tenant_id, tenant_scope
from application.utils.coroutine_queue import CoroutineQueue, bind_module_state

logger = logging.getLogger(__name__)

_q = CoroutineQueue(
    'discord_queue',
    logger=logger,
    drop_warning='discord_queue stopping with %d item(s) still queued — they will not be sent',
)
bind_module_state(__name__, _q)


_BOOT_WAIT_SECONDS = 60


async def _wait_for_bot() -> None:
    """Hold the queue until the bot's first connect, for up to a minute.

    The gateway takes a few seconds after boot, and a DM sent before then failed
    for good: the reminders a restart had made due, and any click in that window.
    """
    from application.services.discord.discord_service import discord_bot_down
    for _ in range(_BOOT_WAIT_SECONDS):
        if not discord_bot_down():
            return
        await asyncio.sleep(1)
    logger.warning('Discord bot still not connected %ss after boot; sending anyway',
                   _BOOT_WAIT_SECONDS)


def start() -> None:
    _q.start()
    _q.enqueue(_wait_for_bot())


async def stop() -> None:
    await _q.stop()


def discard_pending() -> int:
    """Close whatever is queued without running it (see ``CoroutineQueue``)."""
    return _q.discard_pending()


async def _run_in_tenant_scope(tenant_id: int, coro: Coroutine, event_id: Optional[int] = None) -> None:
    with tenant_scope(tenant_id), event_scope(event_id, tenant_id):
        await coro


def enqueue(coro: Coroutine) -> None:
    """Enqueue a Discord-send coroutine, re-binding the caller's tenant for the worker.

    The lone worker task is created once at app startup with **no tenant in
    scope**, and a coroutine handed to ``asyncio.Queue`` does not carry the
    enqueuer's context — so any ``require_tenant_id()`` reached inside a notify
    coroutine (recipient fan-out, acknowledgment queries, …) would raise in the
    worker and be swallowed, dropping the DM. Capture the enqueuing request's
    tenant *now* (request context) and re-establish it around the coroutine when
    the worker awaits it. This is the ``volunteer_reminder._scoped_dm`` pattern
    applied once, at the queue chokepoint every notification flows through.

    The event is captured the same way, so a DM about a match is logged under
    that match's event.

    Enqueued outside any tenant (rare — a genuinely tenant-agnostic send) the
    coroutine is queued unwrapped, exactly as before.
    """
    tenant_id = get_current_tenant_id()
    if tenant_id is not None:
        _q.enqueue(_run_in_tenant_scope(tenant_id, coro, get_current_event_id()))
    else:
        _q.enqueue(coro)
