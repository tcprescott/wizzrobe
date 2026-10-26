"""
VolunteerPosition Repository - Data Access Layer

Coordinator-defined volunteer positions/jobs.
"""

from typing import List, Optional

from application.repositories._base import EventScopedRepository
from application.repositories._event import current_event_id, event_scoped
from application.repositories._tenant import current_tenant_id
from models import VolunteerPosition


class VolunteerPositionRepository(EventScopedRepository[VolunteerPosition]):
    """Repository for the current event's volunteer positions."""

    model = VolunteerPosition

    @staticmethod
    async def list_all() -> List[VolunteerPosition]:
        return await event_scoped(VolunteerPosition.all()).order_by('display_order', 'name')

    @staticmethod
    async def list_active() -> List[VolunteerPosition]:
        return await event_scoped(VolunteerPosition.filter(is_active=True)).order_by('display_order', 'name')

    @staticmethod
    async def name_taken(name: str, exclude_id: Optional[int] = None) -> bool:
        """Whether another of the current event's positions already has this name."""
        query = event_scoped(VolunteerPosition.filter(name=name))
        if exclude_id is not None:
            query = query.exclude(id=exclude_id)
        return await query.exists()

    @staticmethod
    async def create(
        name: str,
        description: Optional[str] = None,
        color: Optional[str] = None,
        display_order: int = 0,
        is_active: bool = True,
        shift_length_minutes: Optional[int] = None,
        stagger_minutes: Optional[int] = None,
        staff_alerts: bool = False,
    ) -> VolunteerPosition:
        return await VolunteerPosition.create(
            tenant_id=current_tenant_id(),
            event_id=current_event_id(),
            name=name,
            description=description,
            color=color,
            display_order=display_order,
            is_active=is_active,
            shift_length_minutes=shift_length_minutes,
            stagger_minutes=stagger_minutes,
            staff_alerts=staff_alerts,
        )

