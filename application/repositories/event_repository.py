"""Event Repository: data access for :class:`~models.Event`.

Events are tenant-scoped like everything a community owns: reads go through
``scoped(...)``. Some lookups take the tenant explicitly because they run before
or outside any tenant context: the page middleware resolving the event a URL
names, and creating a community's first event from ``/platform``.
"""

from collections import defaultdict
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Set

from tortoise.functions import Count
from tortoise.models import Model

from application.event_writes import event_id_path
from application.repositories._base import TenantScopedRepository
from application.repositories._tenant import current_tenant_id, scoped
from models import (
    AccommodationRequest,
    EmergencyContact,
    Event,
    EventStatus,
    Match,
    ProviderTask,
    RaceRoomStatus,
    RacetimeRoom,
    Tournament,
    VolunteerShift,
)


class EventRepository(TenantScopedRepository[Event]):
    model = Event

    @staticmethod
    async def list_all() -> List[Event]:
        return await scoped(Event.all()).order_by('-is_default', '-starts_on', 'name')

    @staticmethod
    async def list_for_tenant(tenant_id: int) -> List[Event]:
        """Every event of ``tenant_id``. Explicit tenant: the page middleware resolves events before any scope."""
        return await Event.filter(tenant_id=tenant_id).order_by('id')

    @staticmethod
    async def get_by_slug(slug: str) -> Optional[Event]:
        return await scoped(Event.filter(slug=slug)).first()

    @staticmethod
    async def get_in_tenant(event_id: int) -> Optional[Event]:
        return await scoped(Event.filter(id=event_id)).first()

    @staticmethod
    async def slug_taken(slug: str, *, exclude_id: Optional[int] = None) -> bool:
        qs = scoped(Event.filter(slug=slug))
        if exclude_id is not None:
            qs = qs.filter(id__not=exclude_id)
        return await qs.exists()

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

    @classmethod
    async def create(cls, **fields: Any) -> Event:
        return await Event.create(tenant_id=current_tenant_id(), **fields)

    @staticmethod
    async def clear_default() -> None:
        await scoped(Event.filter(is_default=True)).update(is_default=False)

    @staticmethod
    async def tournament_counts() -> Dict[int, int]:
        """Tournaments per event of this community. Cross-event: the Events list counts every event."""
        rows = await scoped(Tournament.all()).annotate(n=Count('id')).group_by('event_id').values_list('event_id', 'n')
        return {event_id: n for event_id, n in rows}

    @staticmethod
    async def match_counts() -> Dict[int, int]:
        """Matches per event of this community. Cross-event, like tournament_counts."""
        rows = await scoped(Match.all()).annotate(n=Count('id')).group_by('event_id').values_list('event_id', 'n')
        return {event_id: n for event_id, n in rows}

    @staticmethod
    async def match_event_id(match_id: int) -> Optional[int]:
        ids = await scoped(Match.filter(id=match_id)).values_list('event_id', flat=True)
        return ids[0] if ids else None  # type: ignore[return-value]

    @staticmethod
    async def row_event_id(model: type[Model], row_id: int) -> Optional[int]:
        """The event an event-owned row belongs to, None when there's no such row.

        cross-event: the row decides its own event. A model with a ``tenant``
        is filtered to the community here; the rest are checked by the caller,
        which looks the event up in the community.
        """
        path = event_id_path(model)
        if path is None:
            return None
        query = model.filter(id=row_id)
        if 'tenant' in model._meta.fk_fields:
            query = scoped(query)
        ids = await query.limit(1).values_list(path, flat=True)
        return ids[0] if ids else None  # type: ignore[return-value]

    @staticmethod
    async def has_matches(event_id: int) -> bool:
        return await scoped(Match.filter(event_id=event_id)).exists()

    @staticmethod
    async def open_matches(event_id: int) -> int:
        """Matches not yet finished: live ones and those still to be played."""
        return await scoped(Match.filter(event_id=event_id, finished_at__isnull=True)).count()

    @staticmethod
    async def future_shifts(event_id: int, now: datetime) -> int:
        return await scoped(VolunteerShift.filter(event_id=event_id, ends_at__gt=now)).count()

    @staticmethod
    async def open_race_rooms(event_id: int) -> int:
        return await scoped(RacetimeRoom.filter(
            match__event_id=event_id, status__in=(RaceRoomStatus.OPEN, RaceRoomStatus.IN_PROGRESS),
        )).count()

    @staticmethod
    async def list_open_race_rooms(event_id: int) -> List[RacetimeRoom]:
        return await scoped(RacetimeRoom.filter(
            match__event_id=event_id, status__in=(RaceRoomStatus.OPEN, RaceRoomStatus.IN_PROGRESS),
        ))

    @staticmethod
    async def abandon_provider_tasks(event_id: int, now: datetime) -> int:
        """Stop waiting on an event's unfinished seed rolls (archiving, inside archive_maintenance)."""
        from application.event_writes import guarded_update
        from models import ProviderTaskStatus

        # An UPDATE can't join, so find the rows first.
        ids = await scoped(ProviderTask.filter(
            match__event_id=event_id, status__in=(ProviderTaskStatus.QUEUED, ProviderTaskStatus.RUNNING),
        )).values_list('id', flat=True)
        if not ids:
            return 0
        return await guarded_update(
            scoped(ProviderTask.filter(id__in=list(ids))), status=ProviderTaskStatus.ABANDONED, completed_at=now,
        )

    @staticmethod
    async def due_for_private_data_purge(archived_before: datetime) -> List[Event]:
        """Archived events of every community whose private data is due to go.

        Cross-tenant (tenant-scope and event-scope: exempt — the retention job sweeps every
        community, then purges each inside its own tenant_scope).
        """
        return await Event.filter(
            status=EventStatus.ARCHIVED, archived_at__lte=archived_before, pii_purged_at__isnull=True,
        ).order_by('id')

    @staticmethod
    async def purge_private_data(event: Event, now: datetime) -> tuple[int, int]:
        """Delete an archived event's emergency contacts and accommodation requests, and say so on it.

        Runs inside ``archive_maintenance`` (the rows belong to an archived event).
        """
        from application.event_writes import guarded_delete

        contacts = await guarded_delete(EmergencyContact.filter(tenant_id=event.tenant_id, event_id=event.id))
        requests = await guarded_delete(AccommodationRequest.filter(tenant_id=event.tenant_id, event_id=event.id))
        event.pii_purged_at = now
        await event.save(update_fields=['pii_purged_at'])
        return contacts, requests
