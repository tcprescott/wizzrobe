"""
Volunteer Hours Service - Business Logic Layer

Totals the time a volunteer actually served, against the per-tenant comp tiers
(``KEY_VOLUNTEER_COMP_TIERS``). SGL comps a badge at 8 hours with more at 12 and
16; before this the coordinator added the hours up by eye from a spreadsheet.

This module only reads. There is no ``AuditActions`` entry and no ``EventType``
here on purpose — nothing is written, so there is nothing to audit or publish.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, List, Optional, Sequence, Tuple

from application.feature_flags import requires_feature
from application.repositories import (
    UserRepository,
    VolunteerAssignmentRepository,
)
from application.services.system_config_service import SystemConfigService
from application.services.timezone_service import TimezoneService
from application.services.volunteer.volunteer_pool_service import VolunteerPoolService
from application.utils.timezone import local_day_bounds
from models import FeatureFlag, User


# ponytail: per-user fold in Python over one event's assignments. Move to a
# SQL range aggregate if a tenant ever runs thousands of them.
def merged_hours(windows: Sequence[Tuple[datetime, datetime]]) -> float:
    """Total hours covered by ``windows``, counting overlap once.

    A coordinator can hand-assign overlapping shifts, so 08:00-12:00 plus
    10:00-14:00 is six hours served, not eight. Touching windows
    (``a.end == b.start``) merge into one continuous block, which matters
    because the shift generator produces back-to-back blocks by default.

    Zero-length and inverted windows are dropped rather than subtracting.
    """
    ordered = sorted(
        ((start, end) for start, end in windows if end > start),
        key=lambda w: w[0],
    )
    total = 0.0
    open_window: Optional[Tuple[datetime, datetime]] = None
    for start, end in ordered:
        if open_window is None:
            open_window = (start, end)
            continue
        open_start, open_end = open_window
        if start <= open_end:
            open_window = (open_start, max(open_end, end))
            continue
        total += (open_end - open_start).total_seconds()
        open_window = (start, end)
    if open_window is not None:
        total += (open_window[1] - open_window[0]).total_seconds()
    return total / 3600.0


@dataclass(frozen=True)
class HoursSummary:
    """One volunteer's served hours, and where that lands against the tiers."""

    user_id: int
    user_name: str
    hours: float
    tier_cleared: Optional[float]
    next_tier: Optional[float]

    @property
    def hours_to_next(self) -> Optional[float]:
        if self.next_tier is None:
            return None
        return round(max(0.0, self.next_tier - self.hours), 2)


class VolunteerHoursService:
    """Read-only totals of published volunteer time against the comp tiers."""

    def __init__(self) -> None:
        self.assignment_repository = VolunteerAssignmentRepository()
        self.pool_service = VolunteerPoolService()
        self.user_repository = UserRepository()

    @requires_feature(FeatureFlag.VOLUNTEERS)
    async def for_user(
        self, user: User, *, start: Optional[date] = None, end: Optional[date] = None,
    ) -> HoursSummary:
        """This volunteer's own total, and the gap to the next tier."""
        window = await self._window(start, end)
        tiers = await SystemConfigService.get_volunteer_comp_tiers()
        assignments = await self.assignment_repository.published_for_window(
            *window, user_id=user.id,
        )
        hours = merged_hours(self._clip(assignments, window))
        return self._summarize(user.id, user.preferred_name, hours, tiers)

    @requires_feature(FeatureFlag.VOLUNTEERS)
    async def roster(
        self, *, actor: Optional[User], start: Optional[date] = None, end: Optional[date] = None,
    ) -> List[HoursSummary]:
        """Every volunteer's total, for the coordinator (or the system actor,
        which reads it to work out check-in comps).

        Anyone who served published hours in the window is included even if
        they no longer hold a volunteer role — the hours were still served, and
        the comp is for the hours.
        """
        from application.services.auth_service import AuthService

        await AuthService.ensure(
            AuthService.is_system(actor) or await AuthService.can_manage_volunteers(actor),
            'Only volunteer coordinators can read the volunteer hours roster.',
        )
        window = await self._window(start, end)
        tiers = await SystemConfigService.get_volunteer_comp_tiers()
        assignments = await self.assignment_repository.published_for_window(*window)

        by_user: Dict[int, List[Tuple[datetime, datetime]]] = {}
        names: Dict[int, str] = {}
        for assignment in assignments:
            shift = assignment.shift
            if shift is None:
                continue
            by_user.setdefault(assignment.user_id, []).append(  # type: ignore[attr-defined]
                self._clip_one(shift.starts_at, shift.ends_at, window),
            )
            if assignment.user is not None:
                names[assignment.user_id] = assignment.user.preferred_name  # type: ignore[attr-defined]

        user_ids = set(by_user) | await self.pool_service.assignable_user_ids()
        missing = [uid for uid in user_ids if uid not in names]
        if missing:
            for uid, user in (await self.user_repository.get_by_ids(missing)).items():
                names[uid] = user.preferred_name

        summaries = [
            self._summarize(
                uid, names.get(uid, f'User {uid}'), merged_hours(by_user.get(uid, [])), tiers,
            )
            for uid in user_ids
        ]
        return sorted(summaries, key=lambda s: (-s.hours, s.user_name.lower()))

    @staticmethod
    def _summarize(
        user_id: int, user_name: str, hours: float, tiers: Sequence[float],
    ) -> HoursSummary:
        rounded = round(hours, 2)
        cleared = [t for t in tiers if rounded >= t]
        remaining = [t for t in tiers if rounded < t]
        return HoursSummary(
            user_id=user_id,
            user_name=user_name,
            hours=rounded,
            tier_cleared=max(cleared) if cleared else None,
            next_tier=min(remaining) if remaining else None,
        )

    @staticmethod
    async def _window(
        start: Optional[date], end: Optional[date],
    ) -> Tuple[datetime, datetime]:
        """UTC bounds for the hours count, defaulting to the tenant's event window.

        All time would let last year's shifts comp this year's badge, so the
        default is the configured event window rather than everything on record.
        The bounds are taken on the community's own clock: an event day is a
        property of the venue, not of whoever is reading the page.
        """
        if start is None or end is None:
            event_start, event_end = await SystemConfigService.get_event_window()
            start = start or event_start
            end = end or event_end
        tz = await TimezoneService.tenant_timezone_name()
        return local_day_bounds(start, end, tz)

    @staticmethod
    def _clip_one(
        starts_at: datetime, ends_at: datetime, window: Tuple[datetime, datetime],
    ) -> Tuple[datetime, datetime]:
        return max(starts_at, window[0]), min(ends_at, window[1])

    @classmethod
    def _clip(cls, assignments, window) -> List[Tuple[datetime, datetime]]:
        return [
            cls._clip_one(a.shift.starts_at, a.shift.ends_at, window)
            for a in assignments if a.shift is not None
        ]
