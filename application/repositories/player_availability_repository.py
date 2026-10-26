"""
PlayerAvailability Repository - Data Access Layer

Player self-declared availability windows — the times a player has blocked out
or marked preferred. Everything they leave unsaid is available; that reading
lives in :class:`PlayerAvailabilityService`, not here.
"""

from datetime import datetime
from typing import List, Optional

from application.event_writes import guarded_delete
from application.repositories._event import current_event_id, event_scoped, live_event_scoped
from application.repositories._tenant import current_tenant_id
from models import PlayerAvailability, User, VolunteerAvailabilityStatus


class PlayerAvailabilityRepository:
    """Repository for player availability windows, which belong to one event."""

    @staticmethod
    async def get_by_id(availability_id: int) -> Optional[PlayerAvailability]:
        return await PlayerAvailability.get_or_none(id=availability_id, tenant_id=current_tenant_id()).prefetch_related('user')

    @staticmethod
    async def list_for_user(user: User) -> List[PlayerAvailability]:
        """Every live event's rows for this person (cross-event: their own free time)."""
        return await live_event_scoped(PlayerAvailability.filter(user=user)).order_by('starts_at')

    @staticmethod
    async def for_users_overlapping(
        user_ids: List[int], start: datetime, end: datetime,
    ) -> List[PlayerAvailability]:
        if not user_ids:
            return []
        return await event_scoped(PlayerAvailability.filter(
            user_id__in=user_ids, starts_at__lt=end, ends_at__gt=start,
        ))

    @staticmethod
    async def create(
        user: User,
        starts_at: datetime,
        ends_at: datetime,
        status: VolunteerAvailabilityStatus = VolunteerAvailabilityStatus.AVAILABLE,
        note: Optional[str] = None,
    ) -> PlayerAvailability:
        return await PlayerAvailability.create(
            tenant_id=current_tenant_id(),
            event_id=current_event_id(),
            user=user, starts_at=starts_at, ends_at=ends_at, status=status, note=note,
        )

    @staticmethod
    async def delete(availability: PlayerAvailability) -> None:
        await availability.delete()

    @staticmethod
    async def delete_for_user(user: User) -> int:
        return await guarded_delete(event_scoped(PlayerAvailability.filter(user=user)))
