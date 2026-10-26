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

An event never crosses a community wall: the binding sites resolve the event
with the tenant in hand (``EventService.default_event_id(tenant_id)``,
``Event.get(slug=…, tenant_id=…)``), and repositories filter on tenant and
event together.
"""

from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Iterator, Optional

from application.tenant_context import client_stash

_event_id_var: ContextVar[Optional[int]] = ContextVar('wizzrobe_event_id', default=None)


def set_event_id(event_id: Optional[int]) -> Token:
    """Set the ambient event id, returning a token to pass to :func:`reset_event_id`."""
    return _event_id_var.set(event_id)


def reset_event_id(token: Token) -> None:
    """Restore the event id the matching :func:`set_event_id` replaced."""
    _event_id_var.reset(token)


def _client_stash_event_id() -> Optional[int]:
    stash = client_stash()
    return stash.get('event_id') if stash is not None else None


def get_current_event_id() -> Optional[int]:
    """Resolve the current event id, or None: contextvar first, then client stash."""
    eid = _event_id_var.get()
    if eid is not None:
        return eid
    return _client_stash_event_id()


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
def event_scope(event_id: Optional[int]) -> Iterator[None]:
    """Bind ``event_id`` as the ambient event for the duration of the block.

    Safe inside ``async`` code for the same reason as ``tenant_scope``: the
    contextvar propagates across every ``await`` in the block.
    """
    token = _event_id_var.set(event_id)
    try:
        yield
    finally:
        _event_id_var.reset(token)


def stash_client_event_id(event_id: Optional[int]) -> None:
    """Persist the event id onto the current NiceGUI client (page-build time).

    Read back by :func:`get_current_event_id` when a websocket UI handler runs
    with the contextvar unset. No-op outside a page (tests, plain HTTP).
    """
    if event_id is None:
        return
    stash = client_stash()
    if stash is None:
        return
    try:
        stash['event_id'] = event_id
    except Exception:
        pass
