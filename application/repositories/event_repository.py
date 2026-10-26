"""Event Repository: data access for :class:`~models.Event`.

Events are tenant-scoped like everything a community owns: reads go through
``scoped(...)``. Two lookups take the tenant explicitly because they run before
or outside any tenant context: the default-event resolution the page middleware
does right after resolving the tenant, and creating a community's first event
from ``/platform``.
"""

from datetime import date
from typing import List, Optional

from application.repositories._base import TenantScopedRepository
from application.repositories._tenant import scoped
from models import Event, EventStatus


class EventRepository(TenantScopedRepository[Event]):
    model = Event

    @staticmethod
    async def list_all() -> List[Event]:
        return await scoped(Event.all()).order_by('-is_default', '-starts_on', 'name')

    @staticmethod
    async def get_by_slug(slug: str) -> Optional[Event]:
        return await scoped(Event.filter(slug=slug)).first()

    @staticmethod
    async def default_id_for_tenant(tenant_id: int) -> Optional[int]:
        ids = await Event.filter(tenant_id=tenant_id, is_default=True).limit(1).values_list(
            'id', flat=True,
        )
        return ids[0] if ids else None  # type: ignore[return-value]

    @staticmethod
    async def default_for_tenant(tenant_id: int) -> Optional[Event]:
        return await Event.get_or_none(tenant_id=tenant_id, is_default=True)

    @staticmethod
    async def count_for_tenant(tenant_id: int) -> int:
        return await Event.filter(tenant_id=tenant_id).count()

    @staticmethod
    async def create_for_tenant(
        tenant_id: int,
        *,
        name: str,
        slug: str,
        status: EventStatus,
        is_default: bool,
        starts_on: Optional[date] = None,
        ends_on: Optional[date] = None,
    ) -> Event:
        return await Event.create(
            tenant_id=tenant_id, name=name, slug=slug, status=status,
            is_default=is_default, starts_on=starts_on, ends_on=ends_on,
        )
