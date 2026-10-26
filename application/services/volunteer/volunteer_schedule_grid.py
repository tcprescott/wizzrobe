"""The day grid behind Volunteer → Full Schedule and Admin → Shift Board.

Pure layout: turns one day's shifts into role columns positioned on an hour
scale, so the page only draws boxes and the rules (which assignments count,
where a day starts and ends) are testable without a database or a browser.
The volunteers' grid is read-only and published-only; the Shift Board's
(``for_staff=True``) also carries drafts, ids and each person's arrival state.
"""

import math
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Iterable, Mapping, Optional, Sequence, Tuple

# An event day runs 06:00 to 06:00 local, so a 20:00–02:00 shift sits on the
# evening it starts rather than splitting across two tabs.
DAY_STARTS_AT_HOUR = 6
DAY_LENGTH_HOURS = 24


# A person's arrival on a shift that has started, for the Shift Board.
ARRIVED = 'arrived'
LATE = 'late'
NOT_HERE = 'not_here'
# Staff called it off: nobody is expected, and the shift still counts as worked.
CALLED_OFF = 'called_off'


@dataclass(frozen=True)
class GridPerson:
    name: str
    is_viewer: bool
    assignment_id: int = 0
    user_id: int = 0
    draft: bool = False
    acknowledged: bool = False
    # ARRIVED / LATE / NOT_HERE once the shift has started, else ''.
    arrival: str = ''
    checked_in_at: Optional[datetime] = None
    late_minutes: Optional[int] = None
    called_off_at: Optional[datetime] = None
    called_off_by: str = ''


@dataclass(frozen=True)
class GridShift:
    starts_at: datetime
    ends_at: datetime
    label: str
    # Hours from local midnight of the day, clipped to the day's window.
    start_hour: float
    end_hour: float
    people: Tuple[GridPerson, ...]
    open_slots: int
    shift_id: int = 0
    slots_needed: int = 1
    notes: str = ''
    # Side-by-side placement for shifts of one position that overlap (a
    # staggered position runs 08–12 beside 10–14): this shift's lane, and how
    # many lanes its overlapping group needs.
    lane: int = 0
    lanes: int = 1

    @property
    def has_viewer(self) -> bool:
        return any(p.is_viewer for p in self.people)

    @property
    def called_off(self) -> bool:
        """Everyone told about this shift was called off (drafts don't count)."""
        told = [p for p in self.people if not p.draft]
        return bool(told) and all(p.arrival == CALLED_OFF for p in told)

    @property
    def can_call_off(self) -> int:
        """How many people on it could still be called off."""
        return sum(1 for p in self.people if not p.draft and p.arrival != CALLED_OFF)


@dataclass(frozen=True)
class GridColumn:
    position_id: int
    position: str
    shifts: Tuple[GridShift, ...]


@dataclass(frozen=True)
class DayGrid:
    midnight: datetime
    first_hour: int
    last_hour: int
    columns: Tuple[GridColumn, ...]

    @property
    def hours(self) -> range:
        return range(self.first_hour, self.last_hour)

    def hour_at(self, moment: datetime) -> Optional[float]:
        """Where ``moment`` falls on the grid, or None when it is off it."""
        offset = (moment - self.midnight).total_seconds() / 3600
        return offset if self.first_hour <= offset < self.last_hour else None


def day_window(midnight: datetime) -> Tuple[datetime, datetime]:
    start = midnight + timedelta(hours=DAY_STARTS_AT_HOUR)
    return start, start + timedelta(hours=DAY_LENGTH_HOURS)


def arrival_state(
    assignment, starts_at: datetime, ends_at: datetime, now: Optional[datetime],
) -> str:
    """Where a volunteer stands on a shift in progress; '' before it starts.

    Once the shift is over only a check-in is shown: "not here yet" about a
    finished shift would be noise on a board read during the event. A call-off
    shows at any time, and wins over a check-in (they were sent home).
    """
    if getattr(assignment, 'called_off_at', None) is not None:
        return CALLED_OFF
    if getattr(assignment, 'checked_in_at', None) is not None:
        return ARRIVED
    if now is None or not starts_at <= now < ends_at:
        return ''
    return LATE if getattr(assignment, 'late_minutes', None) is not None else NOT_HERE


def _person(
    assignment, shift, viewer_id: Optional[int], now: Optional[datetime],
    names: Mapping[int, str],
) -> GridPerson:
    draft = bool(assignment.auto_generated)
    return GridPerson(
        assignment.user.preferred_name, assignment.user_id == viewer_id,
        assignment_id=getattr(assignment, 'id', 0) or 0,
        user_id=assignment.user_id,
        draft=draft,
        acknowledged=getattr(assignment, 'acknowledged_at', None) is not None,
        arrival='' if draft else arrival_state(assignment, shift.starts_at, shift.ends_at, now),
        checked_in_at=getattr(assignment, 'checked_in_at', None),
        late_minutes=getattr(assignment, 'late_minutes', None),
        called_off_at=getattr(assignment, 'called_off_at', None),
        called_off_by=names.get(getattr(assignment, 'called_off_by_id', None) or 0, ''),
    )


def build_day_grid(
    shifts: Iterable, midnight: datetime, viewer_id: Optional[int], *,
    for_staff: bool = False,
    positions: Iterable[Tuple[int, str]] = (),
    now: Optional[datetime] = None,
    caller_names: Optional[Mapping[int, str]] = None,
) -> DayGrid:
    """Columns per position, in the order ``shifts`` arrive (position order).

    Draft assignments are left out unless ``for_staff``: those volunteers have
    not been told, and the volunteers' grid is read by everyone on the
    schedule. ``positions`` adds an empty column for each one with no shift
    that day, so the Shift Board can add the first; ``now`` decides who is
    late or missing on a shift that has started; ``caller_names`` maps user
    ids to names for who called someone off.
    """
    lo_bound = float(DAY_STARTS_AT_HOUR)
    hi_bound = float(DAY_STARTS_AT_HOUR + DAY_LENGTH_HOURS)
    by_position: dict = {}
    names: dict = {}
    for position_id, name in positions:
        names[position_id] = name
        by_position[position_id] = []
    for shift in shifts:
        start_h = max(lo_bound, (shift.starts_at - midnight).total_seconds() / 3600)
        end_h = min(hi_bound, (shift.ends_at - midnight).total_seconds() / 3600)
        if end_h <= start_h:
            continue
        shown = [a for a in shift.assignments if a.user and (for_staff or not a.auto_generated)]
        people = tuple(sorted(
            (_person(a, shift, viewer_id, now, caller_names or {}) for a in shown),
            key=lambda p: (p.draft, p.name.casefold()),
        ))
        key = shift.position_id
        names.setdefault(key, shift.position.name if shift.position else 'Volunteer')
        by_position.setdefault(key, []).append(GridShift(
            starts_at=shift.starts_at, ends_at=shift.ends_at, label=shift.label or '',
            start_hour=start_h, end_hour=end_h, people=people,
            open_slots=max(0, shift.slots_needed - len(people)),
            shift_id=getattr(shift, 'id', 0) or 0,
            slots_needed=shift.slots_needed,
            notes=getattr(shift, 'notes', None) or '',
        ))
    columns = tuple(
        GridColumn(key, names[key], _in_lanes(items)) for key, items in by_position.items()
    )
    return _framed(midnight, columns)


# The span an empty Shift Board shows, so there is somewhere to tap.
_EMPTY_SPAN = (8, 24)


def _framed(midnight: datetime, columns: Tuple[GridColumn, ...]) -> DayGrid:
    """The grid's hour span: from the earliest shift shown to the latest."""
    if not columns:
        return DayGrid(midnight, DAY_STARTS_AT_HOUR, DAY_STARTS_AT_HOUR, ())
    shifts = [s for c in columns for s in c.shifts]
    if not shifts:
        return DayGrid(midnight, _EMPTY_SPAN[0], _EMPTY_SPAN[1], columns)
    first = math.floor(min(s.start_hour for s in shifts))
    last = math.ceil(max(s.end_hour for s in shifts))
    return DayGrid(midnight, first, last, columns)


@dataclass(frozen=True)
class GridRole:
    """One role in a viewer's column choice: shown or not, in their order."""

    position_id: int
    name: str
    visible: bool = True


def arrange_roles(
    positions: Iterable[Tuple[int, str]], saved: Optional[Sequence[Mapping]],
) -> Tuple[GridRole, ...]:
    """The viewer's saved order first, then any role added since, shown, at the end.

    ``saved`` is the stored column list, keyed by position id as a string.
    Entries naming a position this community no longer has are skipped.
    """
    names = dict(positions)
    roles: list = []
    for entry in saved or ():
        try:
            position_id = int(str(entry.get('name')))
        except (TypeError, ValueError):
            continue
        if position_id in names and all(r.position_id != position_id for r in roles):
            roles.append(GridRole(position_id, names[position_id], entry.get('visible') is not False))
    placed = {r.position_id for r in roles}
    roles.extend(GridRole(pid, name) for pid, name in names.items() if pid not in placed)
    return tuple(roles)


def apply_roles(grid: DayGrid, roles: Sequence[GridRole]) -> DayGrid:
    """Drop hidden roles and put the rest in the viewer's order.

    A column for a role the list doesn't know (an inactive position that still
    has shifts) stays visible, after the ordered ones.
    """
    rank = {r.position_id: i for i, r in enumerate(roles)}
    hidden = {r.position_id for r in roles if not r.visible}
    kept = sorted(
        (c for c in grid.columns if c.position_id not in hidden),
        key=lambda c: rank.get(c.position_id, len(rank)),
    )
    return _framed(grid.midnight, tuple(kept))


def roles_config(
    roles: Sequence[GridRole], saved: Optional[Sequence[Mapping]], limit: int,
) -> list:
    """The column list to store for ``roles``.

    The preference is per person, not per community, so entries for another
    community's roles ride along after this one's rather than being lost.
    """
    ours = [{'name': str(r.position_id), 'visible': r.visible, 'width': None} for r in roles]
    names = {e['name'] for e in ours}
    others = [
        {'name': str(e.get('name')), 'visible': e.get('visible') is not False, 'width': None}
        for e in saved or () if str(e.get('name')) not in names
    ]
    return (ours + others)[:limit]


def _in_lanes(shifts: list) -> Tuple[GridShift, ...]:
    """Give overlapping shifts their own lanes; each group of overlaps shares a width."""
    placed: list = []
    group: list = []
    group_end = None
    lane_ends: list = []

    def close_group() -> None:
        for shift in group:
            placed.append(replace(shift, lanes=len(lane_ends)))

    for shift in sorted(shifts, key=lambda s: (s.start_hour, s.end_hour)):
        if group_end is not None and shift.start_hour >= group_end:
            close_group()
            group, lane_ends, group_end = [], [], None
        lane = next((i for i, end in enumerate(lane_ends) if end <= shift.start_hour), None)
        if lane is None:
            lane = len(lane_ends)
            lane_ends.append(shift.end_hour)
        else:
            lane_ends[lane] = shift.end_hour
        group.append(replace(shift, lane=lane))
        group_end = shift.end_hour if group_end is None else max(group_end, shift.end_hour)
    close_group()
    return tuple(placed)
