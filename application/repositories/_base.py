"""Generic tenant-scoped CRUD base for the repository layer.

Most repositories persist a single tenant-scoped model with the same four
shapes: a tenant-stamped ``create``, a scoped ``get_by_id``, a setattr-loop
``update``, and a ``delete``. Those live here once, mirroring the ``Generic[T]``
pattern already proven in ``_crew_repository.py``. A concrete repository binds
``model`` and inherits whatever it does not need to specialise; it overrides any
method whose query differs — an extra ``prefetch_related``, a different lookup
shape, or (for a deliberately *global* model like ``User``, ``Tenant``,
``RacetimeBot`` or ``FeatureFlagGroup``) an unscoped ``create``/``get_by_id`` so
no tenant filter is ever applied to a table that has no tenant column.

Reads stay tenant-scoped and writes stay tenant-stamped exactly as before via the
``_tenant`` helpers; this is pure de-duplication, not a behaviour change.

Methods are ``classmethod``s so a subclass may be used both as a class of static
lookups (``TournamentRepository.update(...)``) and as an instance
(``PresetRepository().update(...)``) — the two calling conventions the existing
repositories use interchangeably.
"""

from typing import Any, Generic, Optional, Type, TypeVar

from tortoise.models import Model

from application.repositories._event import stamp_event_id
from application.repositories._save import save_fields
from application.repositories._tenant import current_tenant_id

T = TypeVar("T", bound=Model)


class TenantScopedRepository(Generic[T]):
    """Generic CRUD for a tenant-scoped model. Subclasses set ``model``."""

    model: Type[T]

    @classmethod
    async def create(cls, **fields: Any) -> T:
        return await cls.model.create(tenant_id=current_tenant_id(), **fields)

    @classmethod
    async def get_by_id(cls, obj_id: int) -> Optional[T]:
        return await cls.model.get_or_none(id=obj_id, tenant_id=current_tenant_id())

    @classmethod
    async def update(cls, obj: T, **fields: Any) -> T:
        # Write only the named columns: a full save would revert columns others changed since the load.
        for key, value in fields.items():
            setattr(obj, key, value)
        if fields:
            await save_fields(obj, *fields)
        return obj

    @classmethod
    async def delete(cls, obj: T) -> None:
        await obj.delete()


class EventScopedRepository(TenantScopedRepository[T]):
    """:class:`TenantScopedRepository` for a model an event owns.

    ``create`` stamps the event as well as the tenant: the parent's event for a
    model listed in ``EVENT_PARENTS`` (a match takes its tournament's), else the
    ambient one. ``get_by_id`` stays tenant-scoped only, because a row decides
    its own event.
    """

    @classmethod
    async def create(cls, **fields: Any) -> T:
        if 'event' not in fields and 'event_id' not in fields:
            fields['event_id'] = await stamp_event_id(cls.model, fields)
        return await cls.model.create(tenant_id=current_tenant_id(), **fields)
