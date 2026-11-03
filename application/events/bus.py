"""In-process event bus: the single hub every notification channel hangs off.

Services :func:`publish` a domain :class:`~application.events.event.Event` after
they commit; any number of subscribers react. Two subscriber flavours:

* **sync** (:func:`subscribe_sync`) run inline during ``publish`` — they must be
  fast and non-blocking (schedule work, never await), exactly like
  ``application.events.match_live`` subscribers. This is the fast-path for UI refresh.
* **async** (:func:`subscribe_async`) do I/O (webhook POSTs, and — in a later
  phase — Discord DMs). Their coroutine is offloaded to the dispatch worker so
  ``publish`` never blocks the mutating service call.

``publish`` filters by the subscriber's ``event_types`` (``None`` = all) and
swallows every subscriber error so one bad listener can't break the others or
the caller. Deliberately free of any NiceGUI/service import so it is usable from
the service layer without crossing a layer boundary.
"""

import itertools
import logging
from typing import Awaitable, Callable, Coroutine, FrozenSet, Iterable, Optional, Tuple

from application.event_context import event_scope, get_current_event_id
from application.events import dispatch_queue
from application.events.event import Event
from application.tenant_context import tenant_scope

logger = logging.getLogger(__name__)

SyncHandler = Callable[[Event], None]
AsyncHandler = Callable[[Event], Awaitable[None]]
_Filter = Optional[FrozenSet[str]]

_sync_subscribers: dict[int, Tuple[SyncHandler, _Filter]] = {}
_async_subscribers: dict[int, Tuple[AsyncHandler, _Filter]] = {}
_token_counter = itertools.count(1)


def _as_filter(event_types: Optional[Iterable[str]]) -> _Filter:
    return frozenset(event_types) if event_types else None


def _matches(event_filter: _Filter, event_type: str) -> bool:
    return event_filter is None or event_type in event_filter


def subscribe_sync(handler: SyncHandler, event_types: Optional[Iterable[str]] = None) -> int:
    """Register an inline, non-blocking listener. Returns an unsubscribe token.

    ``event_types`` limits which events reach it (``None`` = all). The handler
    must only *schedule* work, never await.
    """
    token = next(_token_counter)
    _sync_subscribers[token] = (handler, _as_filter(event_types))
    return token


def subscribe_async(handler: AsyncHandler, event_types: Optional[Iterable[str]] = None) -> int:
    """Register an I/O listener whose coroutine runs on the dispatch worker.

    ``event_types`` limits which events reach it (``None`` = all). The handler
    may await freely; it never blocks ``publish``.
    """
    token = next(_token_counter)
    _async_subscribers[token] = (handler, _as_filter(event_types))
    return token


def unsubscribe(token: int) -> None:
    """Remove a previously registered subscriber. No-op if already gone."""
    _sync_subscribers.pop(token, None)
    _async_subscribers.pop(token, None)


def publish(event: Event) -> None:
    """Fan ``event`` out to matching subscribers. Never raises, never blocks."""
    for handler, event_filter in list(_sync_subscribers.values()):
        if not _matches(event_filter, event.event_type):
            continue
        try:
            handler(event)
        except Exception:
            logger.exception("sync event subscriber error for %s", event.event_type)

    for handler, event_filter in list(_async_subscribers.values()):
        if not _matches(event_filter, event.event_type):
            continue
        try:
            # Wrap so the event's tenant and event are active when the worker runs it later.
            dispatch_queue.enqueue(_scoped(handler(event), event.tenant_id, get_current_event_id()))
        except Exception:
            logger.exception("failed to enqueue async event subscriber for %s", event.event_type)


async def _scoped(coro: Coroutine, tenant_id: Optional[int], event_id: Optional[int] = None) -> None:
    """Await ``coro`` with the publisher's tenant and event bound.

    Entered at await-time on the dispatch worker (not at enqueue-time in the
    publisher's context), so the scope is live while the subscriber runs.
    """
    with tenant_scope(tenant_id), event_scope(event_id, tenant_id):
        await coro
