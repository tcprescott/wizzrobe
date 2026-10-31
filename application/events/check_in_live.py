"""In-process pub/sub that keeps open check-in desks in step with each other.

The check-in counterpart of :mod:`application.events.match_live`: services
publish after committing a roster change, and ``theme/realtime.py`` subscribes
each open desk page so a check-in made on one phone shows on every other phone
working the same event. No NiceGUI import, so the service layer can publish.

Subscribers are plain callables ``(event_id, entrant_id, change_type) -> None``
that must only *schedule* work. ``entrant_id`` is ``None`` for ``ROSTER``, the
one signal a sync sends instead of one per row. ``publish`` swallows each
subscriber's exceptions so one bad desk cannot break the mutating call.

This is for UI refresh only. Domain events go on ``event_bus``.
"""

import itertools
import logging
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)

CHANGED = 'changed'
CREATED = 'created'
DELETED = 'deleted'
ROSTER = 'roster'

Subscriber = Callable[[int, Optional[int], str], None]

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


def publish(event_id: int, entrant_id: Optional[int] = None, change_type: str = CHANGED) -> None:
    """Tell every open desk that ``entrant_id`` on ``event_id`` changed. Never raises."""
    for callback in list(_subscribers.values()):
        try:
            callback(event_id, entrant_id, change_type)
        except Exception:  # pragma: no cover - defensive
            logger.exception('check_in_live subscriber error for event %s', event_id)
