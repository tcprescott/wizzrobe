"""Request-time event context, the per-event sibling of ``tenant_context``.

A community (tenant) holds events; most of what a community runs belongs to one
of them. The event is bound once per call, next to the tenant, and read back
synchronously by the layers below: the page middleware binds the event the URL
names (the community's default event until ``/e/<slug>`` routing lands),
workers and Discord handlers bind theirs with :func:`event_scope`.

Resolution has the same two tiers as the tenant, for the same reason (NiceGUI
websocket handlers run after the request's contextvars are gone):

1. the contextvar, set by the middleware or by :func:`event_scope`; else
2. the current NiceGUI client's stash (``app.storage.client['event_id']``),
   written at page build while the middleware's contextvar was still set.

:func:`require_event_id` raises when neither is set. That raise is the safety
net: event-owned data touched with no event in scope fails loudly instead of
reading or stamping the wrong event.

:func:`event_scoped` and :func:`live_event_scoped` are the query helpers built
on it. They live here rather than in the repository layer so services and the
entry surfaces can scope a read too, as they hand-scope a tenant today.

An event never crosses a community wall. The binding sites resolve the event
with the tenant in hand (``EventService.default_event_id(tenant_id)``,
``Event.get(slug=…, tenant_id=…)``), repositories filter on tenant and event
together, and a binding remembers the community it was made in: under any other
tenant it reads as unbound, so a worker that moves on to the next community
can't carry the last one's event with it.
"""

from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Iterator, Optional, Tuple

from tortoise.queryset import QuerySet

from application.tenant_context import client_stash, get_current_tenant_id, require_tenant_id
from models.enums import EventStatus

#: ``(tenant_id, event_id)``: the event and the community it was bound in.
_event_id_var: ContextVar[Optional[Tuple[Optional[int], int]]] = ContextVar(
    'wizzrobe_event_id', default=None,
)


def _binding(event_id: Optional[int], tenant_id: Optional[int]) -> Optional[Tuple[Optional[int], int]]:
    if event_id is None:
        return None
    return (tenant_id if tenant_id is not None else get_current_tenant_id(), event_id)


def set_event_id(event_id: Optional[int], tenant_id: Optional[int] = None) -> Token:
    """Bind ``event_id`` for ``tenant_id`` (default: the current tenant).

    Returns a token to pass to :func:`reset_event_id`.
    """
    return _event_id_var.set(_binding(event_id, tenant_id))


def reset_event_id(token: Token) -> None:
    """Restore the event id the matching :func:`set_event_id` replaced."""
    _event_id_var.reset(token)


def _client_stash_event_id(tenant_id: int) -> Optional[int]:
    stash = client_stash()
    if stash is None or stash.get('event_tenant_id') != tenant_id:
        return None
    return stash.get('event_id')


def _fallback_event_id(tenant_id: int) -> Optional[int]:
    """The event to use when none is bound. Production has none.

    The test harness replaces this with the tenant's default event, the way its
    autouse fixture binds a default tenant, so the thousands of tests that build
    rows directly don't each bind an event.
    """
    return None


def get_current_event_id() -> Optional[int]:
    """Resolve the current event id, or None: contextvar first, then client stash.

    Either one counts only when it was bound for the current tenant.
    """
    tenant_id = get_current_tenant_id()
    if tenant_id is None:
        return None
    bound = _event_id_var.get()
    if bound is not None and bound[0] == tenant_id:
        return bound[1]
    stashed = _client_stash_event_id(tenant_id)
    if stashed is not None:
        return stashed
    return _fallback_event_id(tenant_id)


def require_event_id() -> int:
    """Return the current event id, raising if there is none."""
    eid = get_current_event_id()
    if eid is None:
        raise RuntimeError(
            'No event in context. An event-scoped operation ran outside any '
            'page request and without an explicit event_scope(). Wrap worker / '
            'bot / background code in `with event_scope(event_id):`.'
        )
    return eid


@contextmanager
def event_scope(event_id: Optional[int], tenant_id: Optional[int] = None) -> Iterator[None]:
    """Bind ``event_id`` as the ambient event for the duration of the block.

    It belongs to ``tenant_id``, by default the tenant current on entry, so
    bind the tenant first. Safe inside ``async`` code for the same reason as
    ``tenant_scope``: the contextvar propagates across every ``await`` in the
    block.
    """
    token = _event_id_var.set(_binding(event_id, tenant_id))
    try:
        yield
    finally:
        _event_id_var.reset(token)


def stash_client_event_id(event_id: Optional[int]) -> None:
    """Persist the event id onto the current NiceGUI client (page-build time).

    Read back by :func:`get_current_event_id` when a websocket UI handler runs
    with the contextvar unset. No-op outside a page (tests, plain HTTP).
    """
    tenant_id = get_current_tenant_id()
    if event_id is None or tenant_id is None:
        return
    stash = client_stash()
    if stash is None:
        return
    try:
        stash['event_id'] = event_id
        stash['event_tenant_id'] = tenant_id
    except Exception:
        pass


def event_scoped(qs: QuerySet, event_id: Optional[int] = None) -> QuerySet:
    """Constrain a queryset to the current community and one of its events.

    ``event_id`` defaults to the ambient event; pass it explicitly when the
    caller already holds the row's event (the row decides its own event).
    """
    eid = event_id if event_id is not None else require_event_id()
    return qs.filter(tenant_id=require_tenant_id(), event_id=eid)


def live_event_scoped(qs: QuerySet) -> QuerySet:
    """Constrain a queryset to the current community's live events, all of them.

    For personal pages (My Schedule, staff alert settings), which follow a
    person across every event that is running rather than the one in the URL.
    """
    return qs.filter(tenant_id=require_tenant_id(), event__status=EventStatus.LIVE)
