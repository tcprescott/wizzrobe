"""Everything one person is committed to, as blocks on a clock.

My Schedule's Timeline view: the matches they play, the matches they crew and
their volunteer shifts, merged into one list of time ranges. The merge lives
here rather than in the page because it spans three models; the page only
draws boxes. Lane placement and the event-day rule are pure functions so they
are testable without a database.
"""

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from typing import Any, List, Optional, Sequence, Tuple

from application.repositories import MatchRepository, VolunteerAssignmentRepository
from application.services.community_event_service import CommunityEventService, ends_at
from application.services.crew_service import CrewService
from application.services.event_service import EventService, with_event_name
from application.services.player_availability_service import PlayerAvailabilityService
from application.services.system_config_service import SystemConfigService
from application.utils.timezone import combine_local, local_day_bounds, to_local, to_utc_aware
from models import User, VolunteerAvailabilityStatus

MATCH = 'match'
CREW = 'crew'
SHIFT = 'shift'
#: A community event they host, RSVP'd to or asked a reminder for.
COMMUNITY = 'community'
#: How they're attending a community event, strongest first.
HOSTING = 'hosting'
GOING = 'going'
REMINDER = 'reminder'
#: An approved crew slot on a match with no stage yet: crew is only needed on
#: stage, so it may not happen.
NO_STAGE = 'no_stage'
#: Time the player blocked out under "When you can't play". Not a commitment:
#: the page draws it behind the blocks rather than in a lane of its own.
UNAVAILABLE = 'unavailable'

#: An event day runs 06:00 to 06:00, as on the volunteer grids, so a match at
#: 01:00 sits on the evening it belongs to.
DAY_STARTS_AT_HOUR = 6

_ROLE_TITLES = {'commentator': 'Commentary', 'tracker': 'Tracking'}


@dataclass(frozen=True)
class TimelineItem:
    kind: str
    starts_at: datetime
    ends_at: datetime
    title: str
    detail: str
    match_id: int = 0
    #: The shift's assignment, for its details dialog.
    assignment: Any = None
    event_id: int = 0
    #: ``HOSTING``, ``GOING`` or ``REMINDER`` for a community event,
    #: ``NO_STAGE`` for an off-stage crew slot, else empty.
    badge: str = ''
    #: Drawn as optional: a reminder-only event, an off-stage crew slot.
    optional: bool = False


@dataclass(frozen=True)
class Placed:
    item: TimelineItem
    lane: int
    lanes: int


def event_day(moment: datetime, tz: Any = None) -> date:
    local = to_local(moment, tz) or moment
    return (local - timedelta(hours=DAY_STARTS_AT_HOUR)).date()


def split_by_event_day(starts_at: datetime, ends_at: datetime, tz: Any = None) -> List[Tuple[datetime, datetime]]:
    """Cut a range at each 06:00 so every piece sits on one event day."""
    pieces: List[Tuple[datetime, datetime]] = []
    while starts_at < ends_at:
        boundary = combine_local(
            event_day(starts_at, tz) + timedelta(days=1), time(DAY_STARTS_AT_HOUR), tz,
        )
        pieces.append((starts_at, min(boundary, ends_at)))
        starts_at = boundary
    return pieces


def place_in_lanes(items: Sequence[TimelineItem]) -> List[Placed]:
    """Side by side where items overlap; each overlapping group shares the width."""
    placed: List[Placed] = []
    group: List[Tuple[TimelineItem, int]] = []
    lane_ends: List[datetime] = []
    group_end: Optional[datetime] = None

    def close_group() -> None:
        placed.extend(Placed(item, lane, len(lane_ends)) for item, lane in group)

    for item in sorted(items, key=lambda i: (i.starts_at, i.ends_at)):
        if group_end is not None and item.starts_at >= group_end:
            close_group()
            group, lane_ends, group_end = [], [], None
        lane = next((i for i, end in enumerate(lane_ends) if end <= item.starts_at), None)
        if lane is None:
            lane = len(lane_ends)
            lane_ends.append(item.ends_at)
        else:
            lane_ends[lane] = item.ends_at
        group.append((item, lane))
        group_end = item.ends_at if group_end is None else max(group_end, item.ends_at)
    close_group()
    return placed


def opening_day(days: Sequence[date], today: date) -> Optional[date]:
    """Today during the event, else the next day with something on, else the last."""
    if not days:
        return None
    if today in days:
        return today
    return next((d for d in days if d > today), days[-1])


class MyTimelineService:
    def __init__(self) -> None:
        self.match_repository = MatchRepository()
        self.assignment_repository = VolunteerAssignmentRepository()
        self.crew_service = CrewService()
        self.availability_service = PlayerAvailabilityService()
        self.community_service = CommunityEventService()

    async def window(self) -> Tuple[datetime, datetime]:
        """The event's days on the viewer's clock, each running 06:00 to 06:00."""
        start, end = await SystemConfigService.get_event_window()
        lo, hi = local_day_bounds(start, end)
        shift = timedelta(hours=DAY_STARTS_AT_HOUR)
        return lo + shift, hi + shift

    async def items(
        self, user: User, *, include_shifts: bool, include_community: bool = False,
    ) -> List[TimelineItem]:
        """``include_shifts`` is the caller's ``FeatureFlag.VOLUNTEERS`` check,
        ``include_community`` its ``FeatureFlag.COMMUNITY_EVENTS`` one."""
        start, end = await self.window()
        crew = {
            row['match_id']: row
            for row in await self.crew_service.list_my_commitments(user, upcoming_only=False)
        }
        # Each item beside the event it came from: the list spans every live event.
        sourced: List[Tuple[TimelineItem, Optional[int]]] = []
        for match in await self.match_repository.commitments_for_user(user.id, start, end):
            row = crew.get(match.id)
            sourced.append((self._match_item(
                match, user, row['role'] if row else None, approved=bool(row and row['approved']),
            ), match.event_id))
        if include_shifts:
            for assignment in await self.assignment_repository.list_for_user(user, with_shiftmates=True):
                shift = assignment.shift
                if shift.ends_at <= start or shift.starts_at >= end:
                    continue
                sourced.append((TimelineItem(
                    kind=SHIFT,
                    starts_at=to_utc_aware(shift.starts_at),
                    ends_at=to_utc_aware(shift.ends_at),
                    title=shift.position.name if shift.position else 'Volunteer',
                    detail=shift.label or '',
                    assignment=assignment,
                ), shift.event_id))
        if include_community:
            for event, attending in await self.community_service.list_attending(user, start, end):
                lo = max(to_utc_aware(event.starts_at), start)
                hi = min(to_utc_aware(ends_at(event)), end)
                for a, b in split_by_event_day(lo, hi):
                    sourced.append((TimelineItem(
                        kind=COMMUNITY, starts_at=a, ends_at=b,
                        title=event.title, detail=event.location or '',
                        event_id=event.id, badge=attending, optional=attending == REMINDER,
                    ), event.event_id))
        names = await EventService.names_if_mixed(eid for _, eid in sourced)
        items = [
            replace(item, detail=with_event_name(item.detail, eid, names)) if eid in names else item
            for item, eid in sourced
        ]
        items.sort(key=lambda i: i.starts_at)
        return items

    async def unavailable(self, user: User) -> List[TimelineItem]:
        """The player's "can't play" windows inside the event, one piece per day."""
        start, end = await self.window()
        pieces: List[TimelineItem] = []
        for window in await self.availability_service.availability_for(user):
            if window.status != VolunteerAvailabilityStatus.UNAVAILABLE:
                continue
            lo = max(to_utc_aware(window.starts_at), start)
            hi = min(to_utc_aware(window.ends_at), end)
            for a, b in split_by_event_day(lo, hi):
                pieces.append(TimelineItem(
                    kind=UNAVAILABLE, starts_at=a, ends_at=b,
                    title="Can't play", detail=window.note or '',
                ))
        pieces.sort(key=lambda i: i.starts_at)
        return pieces

    @staticmethod
    def _match_item(
        match: Any, user: User, crew_role: Optional[str], *, approved: bool = False,
    ) -> TimelineItem:
        tournament = match.tournament
        minutes = (
            getattr(tournament, 'average_match_duration', None)
            or CrewService.DEFAULT_MATCH_MINUTES
        )
        starts_at = to_utc_aware(match.scheduled_at)
        short = tournament.short_label if tournament else ''
        players = [p for p in match.players if p.user]
        playing = any(p.user_id == user.id for p in players)
        if playing or crew_role is None:
            opponents = [p.user.preferred_name for p in players if p.user_id != user.id]
            return TimelineItem(
                kind=MATCH, starts_at=starts_at, ends_at=starts_at + timedelta(minutes=minutes),
                title=('vs ' + ', '.join(opponents)) if opponents else (match.title or 'Your match'),
                detail=short, match_id=match.id,
            )
        names = ' vs '.join(p.user.preferred_name for p in players)
        off_stage = approved and match.stage_id is None
        return TimelineItem(
            kind=CREW, starts_at=starts_at, ends_at=starts_at + timedelta(minutes=minutes),
            title=_ROLE_TITLES.get(crew_role, 'Crew'),
            detail=' · '.join(part for part in (names, short) if part),
            match_id=match.id, badge=NO_STAGE if off_stage else '', optional=off_stage,
        )
