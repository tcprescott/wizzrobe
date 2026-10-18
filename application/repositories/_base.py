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
        # Writes only the named columns. A caller often holds a row loaded long
        # before (an edit dialog's tournament), and a full save would put back
        # every column changed since then by anyone else.
        for key, value in fields.items():
            setattr(obj, key, value)
        if fields:
            fmap = obj._meta.fields_map
            columns = [getattr(fmap.get(k), 'source_field', None) or k for k in fields]
            columns += [n for n, f in fmap.items() if getattr(f, 'auto_now', False)]
            await obj.save(update_fields=columns)
        return obj

    @classmethod
    async def delete(cls, obj: T) -> None:
        await obj.delete()
