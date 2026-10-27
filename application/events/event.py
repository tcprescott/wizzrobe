"""The :class:`Event` value object carried through the event bus.

Events are immutable snapshots of something that happened, published by services
*after* they commit. ``actor`` identity is snapshotted (never held as an ORM
reference) so an event can safely cross the async dispatch boundary — mirrors the
enrichment :class:`~application.services.audit_service.AuditService` does for
audit rows.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from application.event_context import get_current_event_id
from application.events.event_types import EventType
from application.tenant_context import get_current_tenant_id


def _event_of(event_type: str, payload: dict[str, Any]) -> Optional[int]:
    if not EventType.is_event_owned(event_type):
        return None
    if event_type.startswith('event.'):
        return payload.get('event_id')
    return get_current_event_id()


@dataclass(frozen=True)
class Event:
    event_type: str
    payload: dict[str, Any]
    actor_id: Optional[int] = None
    actor_username: Optional[str] = None
    # The tenant this event belongs to, snapshotted at publish time. None for a
    # platform-level event (published with no tenant context).
    tenant_id: Optional[int] = None
    # The community event (weekend) it happened in: the one the request or
    # worker was working in, or the event an ``event.*`` message is about.
    # None for community-wide messages. The wire carries it as ``event``.
    event_id: Optional[int] = None
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def create(cls, event_type: str, payload: dict[str, Any], actor: Any = None) -> "Event":
        """Build an event, snapshotting the actor's id/username and the ambient
        tenant so async subscribers (which run later, outside the request) can
        deliver/record it under the right tenant."""
        return cls(
            event_type=event_type,
            payload=payload,
            actor_id=getattr(actor, 'id', None),
            actor_username=getattr(actor, 'username', None),
            tenant_id=get_current_tenant_id(),
            event_id=_event_of(event_type, payload),
        )

    def to_wire(self, event_ref: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """The canonical JSON-serialisable shape delivered to webhook subscribers.

        ``event_ref`` is ``{id, slug, name}`` of :attr:`event_id`, resolved by the
        caller because this runs without the database; None for a community-wide
        message.
        """
        return {
            'event_type': self.event_type,
            'occurred_at': self.occurred_at.isoformat(),
            'tenant_id': self.tenant_id,
            'event': event_ref,
            'actor_id': self.actor_id,
            'actor_username': self.actor_username,
            'data': self.payload,
        }
