"""Centralized in-process event system.

Publishers::

    from application.events import event_bus, Event, EventType
    event_bus.publish(Event.create(EventType.MATCH_CREATED, {...}, actor))

Subscribers register with ``event_bus.subscribe_sync`` (inline, non-blocking) or
``event_bus.subscribe_async`` (I/O, runs on the dispatch worker). See
``application/events/bus.py`` for the full contract.

``match_live`` is the older, narrower sibling kept alongside it: a match-only
``(match_id, change_type)`` signal that ``theme/realtime.py`` subscribes to per
browser client to refresh open views. Reach for ``event_bus`` for domain events
(webhooks, telemetry, anything with an ``EventType``); reach for ``match_live``
only to nudge the UI. Import it explicitly — it is deliberately not re-exported
here, so the two are never confused at the call site.
"""

from application.events import bus as event_bus
from application.events import dispatch_queue, webhook_queue
from application.events.event import Event
from application.events.event_types import EventType

__all__ = ['Event', 'EventType', 'dispatch_queue', 'event_bus', 'webhook_queue']
