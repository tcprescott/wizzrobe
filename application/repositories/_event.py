"""Event-scoping helpers for the repository layer, beside ``_tenant.py``.

Event-owned rows carry both ``tenant_id`` and ``event_id``. Reads filter on
both, so a mistake in event resolution can at worst show another event of the
same community, never another community's rows:

    return await event_scoped(Match.filter(is_active=True)).order_by('scheduled_at')   # read
    return await Match.create(tenant_id=current_tenant_id(), event_id=current_event_id(), **kw)  # write

Both helpers raise when no event is in scope, like their tenant twins.
"""

from typing import Any, Optional, cast

from tortoise.models import Model

from application.event_context import (  # noqa: F401  (re-exported for repositories)
    event_scoped,
    get_current_event_id,
    live_event_scoped,
    require_event_id,
)
from application.tenant_context import require_tenant_id


def current_event_id() -> int:
    """The event to scope/stamp by, or raise if there is no event in context."""
    return require_event_id()


EVENT_PARENTS: dict[str, str] = {
    'Match': 'tournament',
    'VolunteerShift': 'position',
    'VolunteerRoleMapping': 'position',
    'DiscordRoleRule': 'tournament',
}


def tagged_event_id() -> Optional[int]:
    """The event to tag a community-owned row with (audit, telemetry, DMs…).

    Unlike :func:`current_event_id` this never raises: a row written outside
    any event (a platform action, a community-wide setting) is tagged with none.
    """
    return get_current_event_id()


async def parent_event_id(parent_model: type[Model], parent: Any = None, parent_id: Optional[int] = None) -> int:
    """The event of a parent row, from the instance when loaded, else one query.

    The lookup is tenant-scoped, so a parent id from another community resolves
    to nothing and raises rather than borrowing that community's event.
    """
    if parent is not None:
        event_id = getattr(parent, 'event_id', None)
        if event_id is not None:
            return event_id
        parent_id = parent.pk
    ids = await parent_model.filter(
        id=parent_id, tenant_id=require_tenant_id(),
    ).limit(1).values_list('event_id', flat=True)
    if not ids or ids[0] is None:
        raise ValueError(f'{parent_model.__name__} {parent_id} was not found.')
    return cast(int, ids[0])


async def stamp_event_id(model: type[Model], fields: dict[str, Any]) -> int:
    """The event a new ``model`` row belongs to, given the fields it is created with.

    Its parent's event when the model follows one (``EVENT_PARENTS``) and the
    parent is named, else the ambient event (raising when there is none).
    """
    parent = EVENT_PARENTS.get(model.__name__)
    if parent is not None:
        instance, parent_id = fields.get(parent), fields.get(f'{parent}_id')
        if instance is not None or parent_id is not None:
            parent_model = model._meta.fields_map[parent].related_model  # type: ignore[attr-defined]
            return await parent_event_id(parent_model, instance, parent_id)
    return current_event_id()
