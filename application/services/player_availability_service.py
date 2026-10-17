"""
Player Availability Service - Business Logic Layer

Self-service availability for any logged-in player. Unlike volunteer availability,
this sits behind no role gate — any authenticated user may say when they cannot play.

**Player availability is opt-out.** A player who has said nothing is available for
the whole event; the windows they save are the times they *cannot* play (plus, as
a bonus signal, the times they would prefer). This is the one place the two
availability subsystems differ in meaning: volunteer windows are opt-in, so a
volunteer who has said nothing is not offered a shift. Everything unspecified
resolves through the ``default=AVAILABLE`` argument to
:mod:`application.services.availability_windows`.
"""

from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

from tortoise.transactions import in_transaction

from application.repositories.player_availability_repository import PlayerAvailabilityRepository
from application.services import availability_windows
from application.services.audit_service import AuditActions, AuditService
from models import PlayerAvailability, User, VolunteerAvailabilityStatus

#: Availability of a stretch nobody has said anything about. Players are
#: available by default, so the absence of a window is a yes, not a shrug.
DEFAULT_STATUS = VolunteerAvailabilityStatus.AVAILABLE


class PlayerAvailabilityService:
    """Player-declared availability windows, read opt-out."""

    def __init__(self) -> None:
        self.repository = PlayerAvailabilityRepository()
        self.audit_service = AuditService()

    async def availability_for(self, user: User) -> List[PlayerAvailability]:
        return await self.repository.list_for_user(user)

    @staticmethod
    async def _ensure_can_read(actor: Optional[User], user_ids: Sequence[int]) -> None:
        """Your own windows, or anyone's for staff."""
        from application.services.auth_service import AuthService

        if actor is not None and all(uid == actor.id for uid in user_ids):
            return
        await AuthService.ensure(
            await AuthService.is_staff(actor),
            "Staff access required to view other players' availability",
        )

    async def availability_of(self, target: User, *, actor: Optional[User]) -> List[PlayerAvailability]:
        """``target``'s windows, read by ``actor``: themselves or staff."""
        await self._ensure_can_read(actor, [target.id])
        return await self.repository.list_for_user(target)

    async def set_windows(
        self,
        user: User,
        windows: Sequence[Tuple[datetime, datetime, VolunteerAvailabilityStatus, Optional[str]]],
    ) -> List[PlayerAvailability]:
        """Replace the user's availability with the supplied windows (self-service).

        An ``AVAILABLE`` window is dropped rather than stored: opt-out already
        makes every unspoken-for hour available, so keeping one would be a row
        that reads back as a declaration while changing no answer. The rule
        lives here so the API, MCP and the editor all agree on what a saved set
        contains.
        """
        for starts_at, ends_at, _status, _note in windows:
            if ends_at <= starts_at:
                raise ValueError("End time needs to be after the start time for each window.")
        windows = [w for w in windows if w[2] != DEFAULT_STATUS]

        async with in_transaction():
            await self.repository.delete_for_user(user)
            created: List[PlayerAvailability] = []
            for starts_at, ends_at, status, note in windows:
                created.append(
                    await self.repository.create(
                        user=user, starts_at=starts_at, ends_at=ends_at,
                        status=status, note=note,
                    )
                )
            await self.audit_service.write_log(
                user, AuditActions.PLAYER_AVAILABILITY_UPDATED,
                {'window_count': len(created)},
            )
        return created

    async def clear(self, user: User) -> None:
        await self.repository.delete_for_user(user)
        await self.audit_service.write_log(
            user, AuditActions.PLAYER_AVAILABILITY_UPDATED, {'window_count': 0},
        )

    async def availability_map(
        self, user_ids: List[int], start: datetime, end: datetime,
    ) -> Dict[int, List[PlayerAvailability]]:
        """Map user_id -> availability windows overlapping [start, end]."""
        rows = await self.repository.for_users_overlapping(user_ids, start, end)
        return availability_windows.group_by_user(rows)

    async def availability_map_as(
        self, user_ids: List[int], start: datetime, end: datetime, *, actor: Optional[User],
    ) -> Dict[int, List[PlayerAvailability]]:
        """:meth:`availability_map` for a person asking: their own, or anyone's for staff."""
        await self._ensure_can_read(actor, user_ids)
        return await self.availability_map(user_ids, start, end)

    @staticmethod
    def covers(
        windows: Sequence[PlayerAvailability], start: datetime, end: datetime,
    ) -> Optional[VolunteerAvailabilityStatus]:
        """Return the availability signal for a time window.

        An overlapping UNAVAILABLE window wins outright; PREFERRED beats
        AVAILABLE. A range no window touches is AVAILABLE — players block out
        the times they cannot play rather than declaring the ones they can.
        """
        return availability_windows.covers(windows, start, end, DEFAULT_STATUS)

    @staticmethod
    def effective_segments(
        windows: Sequence[PlayerAvailability], start: datetime, end: datetime,
    ) -> List[Tuple[datetime, datetime, Optional[VolunteerAvailabilityStatus]]]:
        """Split ``[start, end]`` into maximal segments of constant availability.

        Overlapping windows are resolved by :meth:`covers` precedence
        (unavailable > preferred > available); everything they leave untouched
        is AVAILABLE. Adjacent segments of equal status are merged so the
        result is the minimal set of contiguous spans.
        """
        return availability_windows.effective_segments(
            windows, start, end, DEFAULT_STATUS,
        )
