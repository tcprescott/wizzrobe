"""In-process pub/sub for live match-change notifications.

Services publish a match change here after committing it; the presentation layer
(see ``theme/realtime.py``) subscribes per browser client to push updates into
open views. This module deliberately has **no** NiceGUI import so it stays usable
from the service layer without violating the three-layer boundary.

Subscriber callbacks are plain callables ``(match_id, change_type) -> None`` and
must be fast and non-blocking — they should only *schedule* work, never await it.
``publish`` swallows per-subscriber exceptions so one bad subscriber can't break
the others or the mutating service call.
"""

import itertools
import logging
from typing import Callable, Dict

logger = logging.getLogger(__name__)

CHANGED = 'changed'
CREATED = 'created'
DELETED = 'deleted'

Subscriber = Callable[[int, str], None]

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


def publish(match_id: int, change_type: str = CHANGED) -> None:
    """Notify all subscribers that ``match_id`` changed. Never raises."""
    for callback in list(_subscribers.values()):
        try:
            callback(match_id, change_type)
        except Exception:  # pragma: no cover - defensive
            logger.exception("match_live subscriber error for match %s", match_id)
