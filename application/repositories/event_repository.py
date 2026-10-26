"""Event Repository: data access for :class:`~models.Event`.

Events are tenant-scoped like everything a community owns: reads go through
``scoped(...)``. Two lookups take the tenant explicitly because they run before
or outside any tenant context: the default-event resolution the page middleware
does right after resolving the tenant, and creating a community's first event
from ``/platform``.
"""

from collections import defaultdict
from datetime import date
from typing import Dict, Iterable, List, Optional, Set

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
    async def live_ids(event_ids: Iterable[int]) -> Set[int]:
        """Which of ``event_ids`` are live. Cross-tenant: a worker filters a batch."""
        wanted = set(event_ids)
        if not wanted:
            return set()
        rows = await Event.filter(id__in=wanted, status=EventStatus.LIVE).values_list('id', flat=True)
        return set(rows)  # type: ignore[arg-type]

    @staticmethod
    async def live_ids_by_tenant(tenant_ids: Iterable[int]) -> Dict[int, List[int]]:
        """Each community's live event ids, oldest first. Cross-tenant: worker fan-out."""
        wanted = set(tenant_ids)
        out: Dict[int, List[int]] = defaultdict(list)
        if not wanted:
            return out
        rows = await Event.filter(tenant_id__in=wanted, status=EventStatus.LIVE).order_by('id').values_list(
            'tenant_id', 'id',
        )
        for tenant_id, event_id in rows:
            out[tenant_id].append(event_id)
        return out

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
