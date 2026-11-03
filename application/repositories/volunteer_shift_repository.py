"""
VolunteerShift Repository - Data Access Layer

Position-scoped, time-windowed shifts.
"""

from datetime import datetime
from typing import List, Optional

from application.event_writes import guarded_delete
from application.repositories._base import EventScopedRepository
from application.repositories._event import event_scoped, parent_event_id
from application.repositories._tenant import current_tenant_id
from models import VolunteerPosition, VolunteerShift

_PREFETCH = ('position', 'assignments', 'assignments__user')


class VolunteerShiftRepository(EventScopedRepository[VolunteerShift]):
    """Repository for volunteer shifts. A shift belongs to its position's event."""

    model = VolunteerShift

    @staticmethod
    async def get_by_id(shift_id: int) -> Optional[VolunteerShift]:
        return await VolunteerShift.get_or_none(id=shift_id, tenant_id=current_tenant_id()).prefetch_related(*_PREFETCH)

    @staticmethod
    async def list_for_window(start: datetime, end: datetime) -> List[VolunteerShift]:
        """The current event's shifts overlapping [start, end], by position then start."""
        return await (
            event_scoped(VolunteerShift.filter(starts_at__lt=end, ends_at__gt=start))
            .order_by('position__display_order', 'position__name', 'starts_at')
            .prefetch_related(*_PREFETCH)
        )

    @staticmethod
    async def list_for_position_window(
        position_id: int, start: datetime, end: datetime,
    ) -> List[VolunteerShift]:
        return await (
            event_scoped(VolunteerShift.filter(
                position_id=position_id, starts_at__lt=end, ends_at__gt=start,
            ))
            .order_by('starts_at')
            .prefetch_related(*_PREFETCH)
        )

    @staticmethod
    async def create(  # type: ignore[override]
        position_id: int,
        starts_at: datetime,
        ends_at: datetime,
        label: Optional[str] = None,
        slots_needed: int = 1,
        notes: Optional[str] = None,
    ) -> VolunteerShift:
        return await VolunteerShift.create(
            tenant_id=current_tenant_id(),
            event_id=await parent_event_id(VolunteerPosition, parent_id=position_id),
            position_id=position_id,
            starts_at=starts_at,
            ends_at=ends_at,
            label=label,
            slots_needed=slots_needed,
            notes=notes,
        )

    @staticmethod
    async def delete_all() -> int:
        """Delete every shift of the current event."""
        return await guarded_delete(event_scoped(VolunteerShift.all()))
