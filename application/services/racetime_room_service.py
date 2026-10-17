"""Racetime Room Service — record lookup for room→tenant routing.

Rooms are created and their status updated by the PR 4/6 lifecycle; this PR
provides the record and the **unscoped by-slug lookup** the inbound-event router
needs. A racetime event carries only the room slug (no tenant), so
:meth:`get_by_slug` resolves the room — and therefore its tenant — before any
tenant context exists, mirroring how an ``ApiToken`` hash routes back to a
tenant.
"""

from datetime import datetime, timezone
from typing import List, Optional

from application.repositories import RacetimeRoomRepository
from application.repositories._tenant import scoped
from application.services.auth_service import AuthService
from models import Match, RaceRoomStatus, RacetimeRoom, User


class RacetimeRoomService:
    """Lookups for :class:`~models.RacetimeRoom` records."""

    def __init__(self) -> None:
        self.repository = RacetimeRoomRepository()

    async def get_by_slug(self, slug: str) -> Optional[RacetimeRoom]:
        """Resolve a room by its globally-unique slug — unscoped, for routing."""
        return await self.repository.get_by_slug((slug or '').strip())

    async def get_for_match(self, match: Match) -> Optional[RacetimeRoom]:
        return await self.repository.get_by_match(match)

    async def list_open_rooms(self) -> List[RacetimeRoom]:
        """Not-yet-terminal rooms across all tenants (unscoped, for re-adoption)."""
        return await self.repository.list_open_all()

    async def list_open_rooms_for_current_tenant(self, actor: Optional[User]) -> List[RacetimeRoom]:
        """Not-yet-terminal rooms for the current tenant: staff or a sync admin,
        who manage them on Admin → Racetime."""
        await AuthService.ensure(
            await AuthService.can_manage_sync(actor),
            'You do not have permission to list racetime rooms.',
        )
        return await scoped(
            RacetimeRoom.filter(
                status__in=[RaceRoomStatus.OPEN, RaceRoomStatus.IN_PROGRESS],
            )
        ).prefetch_related('tenant').order_by('id')

    async def set_status_as(
        self, room: RacetimeRoom, status: RaceRoomStatus, *, actor: Optional[User],
    ) -> RacetimeRoom:
        """A person correcting a room's cached status: staff or a sync admin."""
        await AuthService.ensure(
            await AuthService.can_manage_sync(actor),
            'You do not have permission to change a racetime room.',
        )
        return await self.set_status(room, status)

    async def set_status(self, room: RacetimeRoom, status: RaceRoomStatus) -> RacetimeRoom:
        """Write a room's cached lifecycle status (tenant-scoped).

        The connection handler resolves ``room`` via the unscoped by-slug lookup
        and re-establishes its tenant scope before calling this, so the write is
        correctly attributed. Stamps ``opened_at`` the first time a room reaches
        ``IN_PROGRESS`` if it was not already set.
        """
        fields = {'status': status}
        if status == RaceRoomStatus.IN_PROGRESS and room.opened_at is None:
            fields['opened_at'] = datetime.now(timezone.utc)
        return await self.repository.update(room, **fields)
