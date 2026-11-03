"""What the app's Work tab shows someone who works the event, one read per screen.

The NiceGUI Volunteer hub decides its tabs and draws the Full Schedule grid
inside the page. The app gets the same decisions as data: which areas this
person can open (with a live line for each), and the volunteer schedule for
one day already laid out in columns, in their saved column order.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import List, Mapping, Optional, Sequence, Tuple

from application.services.auth_service import AuthService
from application.services.check_in_desk_service import CheckInDeskService
from application.services.feature_flag_service import FeatureFlagService
from application.services.my_timeline_service import event_day
from application.services.system_config_service import SystemConfigService
from application.services.table_preference_service import TablePreferenceService
from application.services.volunteer.volunteer_availability_service import VolunteerAvailabilityService
from application.services.volunteer.volunteer_schedule_grid import (
    DayGrid,
    GridRole,
    apply_roles,
    arrange_roles,
    roles_config,
)
from application.services.volunteer.volunteer_schedule_service import VolunteerScheduleService
from application.utils.timezone import now_local
from models import CheckInEventStatus, FeatureFlag, Role, User

# Shared with the website's Full Schedule (TableKeys.VOLUNTEER_FULL_SCHEDULE), so
# a column order saved on either shows on both.
FULL_SCHEDULE_PREFS_KEY = 'volunteer.full_schedule'

MAX_EVENT_DAYS = 14


@dataclass
class DeskArea:
    open_events: int
    event_id: Optional[int] = None
    event_name: str = ''
    checked_in: int = 0
    expected: int = 0


@dataclass
class ScheduleArea:
    on_shift_now: int
    my_next_shift_at: Optional[datetime] = None


@dataclass
class AvailabilityArea:
    windows: int


@dataclass
class WorkAreas:
    desk: Optional[DeskArea] = None
    schedule: Optional[ScheduleArea] = None
    availability: Optional[AvailabilityArea] = None
    proctor: bool = False


@dataclass
class ScheduleView:
    days: List[date]
    day: Optional[date]
    roles: Tuple[GridRole, ...]
    grid: Optional[DayGrid]
    hidden_with_shifts: int = 0
    customized: bool = False


def event_days(start: date, end: date) -> List[date]:
    days: List[date] = []
    d = start
    while d <= end and len(days) < MAX_EVENT_DAYS:
        days.append(d)
        d += timedelta(days=1)
    return days


def opening_day(days: Sequence[date], asked: Optional[date], today: date) -> Optional[date]:
    """The day asked for when it's an event day, else today during the event, else day one."""
    if not days:
        return None
    if asked in days:
        return asked
    return today if today in days else days[0]


class WorkService:
    """The Work tab's reads: which areas you have, and the volunteer schedule."""

    def __init__(self) -> None:
        self.schedule = VolunteerScheduleService()
        self.desk = CheckInDeskService()
        self.prefs = TablePreferenceService()

    async def areas(self, actor: User, *, now: Optional[datetime] = None) -> WorkAreas:
        """The areas of the Work tab this person may open, as the Volunteer hub offers them."""
        now = now or datetime.now(timezone.utc)
        out = WorkAreas()
        roles = await AuthService.get_roles(actor)
        if await FeatureFlagService().is_enabled(FeatureFlag.VOLUNTEERS):
            if await AuthService.can_view_volunteer_schedule(actor):
                out.schedule = await self._schedule_line(actor, now)
            if await AuthService.can_view_volunteer(actor) and (
                Role.VOLUNTEER in roles or await AuthService.is_staff(actor)
            ):
                windows = await VolunteerAvailabilityService().availability_for(actor)
                out.availability = AvailabilityArea(windows=len(windows))
        out.proctor = await AuthService.can_open_proctor_station(actor)
        if await AuthService.can_view_check_in_desk(actor):
            out.desk = await self._desk_line(actor)
        return out

    async def _schedule_line(self, actor: User, now: datetime) -> ScheduleArea:
        shifts = await self.schedule.shifts_for_viewer(actor, now, now + timedelta(days=MAX_EVENT_DAYS))
        on_now: set[int] = set()
        mine: Optional[datetime] = None
        for shift in shifts:
            told = [a for a in shift.assignments if a.user_id and not a.auto_generated]
            if shift.starts_at <= now < shift.ends_at:
                on_now.update(a.user_id for a in told if a.called_off_at is None)
            if shift.starts_at > now and any(a.user_id == actor.id for a in told):
                mine = shift.starts_at if mine is None else min(mine, shift.starts_at)
        return ScheduleArea(on_shift_now=len(on_now), my_next_shift_at=mine)

    async def _desk_line(self, actor: User) -> DeskArea:
        events = [e for e in await self.desk.events_for(actor) if e.status == CheckInEventStatus.OPEN]
        if len(events) != 1:
            return DeskArea(open_events=len(events))
        event, entrants = await self.desk.roster_for(actor, events[0].id)
        present = [e for e in entrants if e.withdrawn_at is None or e.checked_in_at is not None]
        return DeskArea(
            open_events=1, event_id=event.id, event_name=event.name,
            checked_in=sum(1 for e in present if e.checked_in_at is not None), expected=len(present),
        )

    async def schedule_view(self, actor: User, day: Optional[date] = None) -> ScheduleView:
        """One event day of the volunteer schedule, in the viewer's saved column order."""
        positions = await self.schedule.grid_roles(actor)
        saved = await self._saved_columns(actor)
        roles = arrange_roles(positions, saved)
        start, end = await SystemConfigService.get_event_window()
        days = event_days(start, end) if start and end else []
        chosen = opening_day(days, day, event_day(now_local()))
        customized = any(not r.visible for r in roles) or [r.position_id for r in roles] != [p for p, _ in positions]
        if chosen is None:
            return ScheduleView(days=days, day=None, roles=roles, grid=None, customized=customized)
        everyone = await self.schedule.day_grid(actor, chosen)
        grid = apply_roles(everyone, roles)
        return ScheduleView(
            days=days, day=chosen, roles=roles, grid=grid, customized=customized,
            hidden_with_shifts=len(everyone.columns) - len(grid.columns),
        )

    async def save_schedule_columns(
        self, actor: User, columns: Sequence[Tuple[int, bool]],
    ) -> Tuple[GridRole, ...]:
        """Store which roles show and in what order; roles left out keep their place at the end."""
        positions = await self.schedule.grid_roles(actor)
        names = dict(positions)
        chosen: List[GridRole] = []
        for position_id, visible in columns:
            if position_id in names and all(r.position_id != position_id for r in chosen):
                chosen.append(GridRole(position_id, names[position_id], visible))
        for position_id, name in positions:
            if all(r.position_id != position_id for r in chosen):
                chosen.append(GridRole(position_id, name, True))
        saved = await self._saved_columns(actor)
        config = {'columns': roles_config(chosen, saved, TablePreferenceService.MAX_COLUMNS)}
        stored = await self.prefs.save(actor, FULL_SCHEDULE_PREFS_KEY, config)
        return arrange_roles(positions, stored.get('columns', []))

    async def reset_schedule_columns(self, actor: User) -> Tuple[GridRole, ...]:
        positions = await self.schedule.grid_roles(actor)
        await self.prefs.reset(actor, FULL_SCHEDULE_PREFS_KEY)
        return arrange_roles(positions, [])

    async def _saved_columns(self, actor: User) -> Optional[Sequence[Mapping]]:
        stored = await self.prefs.get(actor, FULL_SCHEDULE_PREFS_KEY)
        return (stored or {}).get('columns') or []
