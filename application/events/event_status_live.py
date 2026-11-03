"""In-process pub/sub that tells open pages their event was archived or unarchived.

The event-status counterpart of :mod:`application.events.match_live`:
``EventService`` publishes after the status commits, and ``theme/realtime.py``
subscribes each open page so one showing that event reloads into (or out of)
its read-only look. No NiceGUI import, so the service layer can publish.

Subscribers are plain callables ``(event_id) -> None`` that must only
*schedule* work. ``publish`` swallows each subscriber's exceptions so one bad
page cannot break the archive.

This is for UI refresh only. Domain events go on ``event_bus``.
"""

import itertools
import logging
from typing import Callable, Dict

from application.events import live_relay

logger = logging.getLogger(__name__)

Subscriber = Callable[[int], None]

_subscribers: Dict[int, Subscriber] = {}
_token_counter = itertools.count(1)


def subscribe(callback: Subscriber) -> int:
    """Register a subscriber; returns a token for later ``unsubscribe``."""
    token = next(_token_counter)
    _subscribers[token] = callback
    return token


def unsubscribe(token: int) -> None:
    """Remove a previously registered subscriber. No-op if already gone."""
    _subscribers.pop(token, None)


def _deliver(event_id: int) -> None:
    """Call this process's subscribers. Never raises."""
    for callback in list(_subscribers.values()):
        try:
            callback(event_id)
        except Exception:  # pragma: no cover - defensive
            logger.exception('event_status_live subscriber error for event %s', event_id)


def publish(event_id: int) -> None:
    """Tell every open view, in this process and (with ``MULTI_INSTANCE``) the others. Never raises."""
    _deliver(event_id)
    live_relay.forward('event_status', event_id)


live_relay.register('event_status', _deliver)
