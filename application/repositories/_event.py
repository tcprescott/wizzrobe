"""Event-scoping helpers for the repository layer, beside ``_tenant.py``.

Event-owned rows carry both ``tenant_id`` and ``event_id``. Reads filter on
both, so a mistake in event resolution can at worst show another event of the
same community, never another community's rows:

    return await event_scoped(Match.filter(is_active=True)).order_by('scheduled_at')   # read
    return await Match.create(tenant_id=current_tenant_id(), event_id=current_event_id(), **kw)  # write

Both helpers raise when no event is in scope, like their tenant twins.
"""

from typing import Optional

from tortoise.queryset import QuerySet

from application.event_context import require_event_id
from application.tenant_context import require_tenant_id


def current_event_id() -> int:
    """The event to scope/stamp by, or raise if there is no event in context."""
    return require_event_id()


def event_scoped(qs: QuerySet, event_id: Optional[int] = None) -> QuerySet:
    """Constrain a queryset to the current community and one of its events.

    ``event_id`` defaults to the ambient event; pass it explicitly when the
    caller already holds the row's event (the row decides its own event).
    """
    eid = event_id if event_id is not None else require_event_id()
    return qs.filter(tenant_id=require_tenant_id(), event_id=eid)
