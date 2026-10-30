"""Live bracket refresh — subscribe a bracket detail view to the event bus.

The public bracket page registers here at build time; the registration subscribes
a fast, non-blocking listener to the in-process :mod:`application.events` bus.
When a matching event fires, the listener invokes the caller's ``on_event``
callback — a *sync, non-blocking* function that schedules its own (debounced,
client-scoped) refresh. Subscriptions are released on client disconnect.

**Two event families, two ways of matching them.** ``BRACKET_*`` events carry a
``bracket_id`` and are matched exactly. ``MATCH_*`` events are what make the
bracket *live* (U2) — without them a card could hold the data and still never
repaint when its match starts or finishes — but their payloads carry no
``bracket_id``: resolving one would need a query, and this callback runs inside
``publish`` and must not block. They are matched on ``tournament_id``, which
every ``MATCH_*`` payload does carry. That is a slightly wider net — an unrelated
match in the same tournament repaints the view — and the right trade against a
query in a sync callback; the page's debounce absorbs the extra rebuilds.

Coalescing lives in the caller (the page debounces so a burst of events — e.g.
``BRACKET_MATCH_COMPLETED`` then ``BRACKET_COMPLETED`` from one report — collapses
into a single rebuild), keeping this module a thin, non-blocking bridge.
"""

from typing import Callable, Dict, List, Optional

from nicegui import app, context

from application.events import EventType, event_bus

_BRACKET_EVENTS = (
    EventType.BRACKET_MATCH_COMPLETED,
    EventType.BRACKET_ADVANCED,
    EventType.BRACKET_COMPLETED,
    EventType.BRACKET_STAGE_ADVANCED,
    EventType.BRACKET_STARTED,
    EventType.BRACKET_GAME_SCHEDULED,
    EventType.BRACKET_GAME_LINKED,
    EventType.BRACKET_GAME_UNLINKED,
    EventType.BRACKET_GAME_RELEASED,
    EventType.BRACKET_GAME_COMPLETED,
    EventType.BRACKET_GAME_CANCELLED,
)

_MATCH_EVENTS = (
    EventType.MATCH_CREATED,
    EventType.MATCH_UPDATED,
    EventType.MATCH_RESCHEDULED,
    EventType.MATCH_SEATED,
    EventType.MATCH_STARTED,
    EventType.MATCH_FINISHED,
    EventType.MATCH_CONFIRMED,
    EventType.MATCH_RESULT_RECORDED,
    EventType.MATCH_CANCELLED,
    EventType.MATCH_DELETED,
)

# client.id -> event-bus subscription tokens, released on disconnect.
_client_tokens: Dict[str, List[int]] = {}
_disconnect_installed = False


def register_bracket_view(
    bracket_id: int,
    on_event: Callable[[], None],
    *,
    tournament_id: Optional[int] = None,
) -> None:
    """Call ``on_event()`` when this bracket — or a match in it — changes.

    Must be called during page construction (a live client context). ``on_event``
    is a **sync, non-blocking** callback (it runs inside ``publish``): it should
    only *schedule* a refresh (see the page's debounced ``request_refresh``),
    never block or await.

    ``tournament_id`` enables the live match half (U2). Omit it and the view
    still repaints on ``BRACKET_*`` events exactly as before.
    """
    client_id = context.client.id
    tokens = _client_tokens.setdefault(client_id, [])

    def _on_bracket_event(event) -> None:
        payload = event.payload or {}
        # Bracket events touching this bracket — including a stage advance that
        # seeds it (to_bracket_id) or drew from it (from_bracket_id).
        related = {
            payload.get('bracket_id'),
            payload.get('to_bracket_id'),
            payload.get('from_bracket_id'),
        }
        if bracket_id not in related:
            return
        on_event()

    tokens.append(event_bus.subscribe_sync(_on_bracket_event, _BRACKET_EVENTS))

    if tournament_id is not None:
        def _on_match_event(event) -> None:
            if (event.payload or {}).get('tournament_id') != tournament_id:
                return
            on_event()

        tokens.append(event_bus.subscribe_sync(_on_match_event, _MATCH_EVENTS))

    _install_disconnect_cleanup()


def _install_disconnect_cleanup() -> None:
    global _disconnect_installed
    if _disconnect_installed:
        return
    _disconnect_installed = True
    app.on_disconnect(_on_disconnect)


def _on_disconnect(client) -> None:
    for token in _client_tokens.pop(client.id, []):
        event_bus.unsubscribe(token)
