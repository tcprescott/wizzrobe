"""Admin → Shift Board: the staff day grid, and moving and swapping volunteers."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from application.services.volunteer.volunteer_schedule_grid import (
    ARRIVED,
    LATE,
    NOT_HERE,
    GridPerson,
    build_day_grid,
)
from application.services.volunteer.volunteer_schedule_service import VolunteerScheduleService
from application.services.volunteer.volunteer_shift_move_service import VolunteerShiftMoveService
from models import VolunteerAssignment, VolunteerPosition, VolunteerShift
from pages.admin_tabs.admin_shift_board import coverage_chips
from tests.services.test_volunteer_scheduling import _at, _staff, _volunteer
from theme.volunteer_grid import person_status

DAY = date(2026, 10, 4)
MIDNIGHT = datetime(2026, 10, 4, tzinfo=timezone.utc)


def _assignment(user_id, name, *, draft=False, checked_in_at=None, late_minutes=None, acked=False):
    return SimpleNamespace(
        id=user_id * 10, user_id=user_id, user=SimpleNamespace(preferred_name=name),
        auto_generated=draft, checked_in_at=checked_in_at, late_minutes=late_minutes,
        acknowledged_at=MIDNIGHT if acked else None,
    )


def _shift(start, end, people=(), slots=1, position_id=1, shift_id=1):
    return SimpleNamespace(
        id=shift_id, position_id=position_id, position=SimpleNamespace(name='Race Proctor'),
        starts_at=MIDNIGHT + timedelta(hours=start), ends_at=MIDNIGHT + timedelta(hours=end),
        label='', slots_needed=slots, notes='Meet at stage 2', assignments=list(people),
    )


def test_staff_grid_keeps_drafts_ids_and_arrivals():
    now = MIDNIGHT + timedelta(hours=13)
    grid = build_day_grid([
        _shift(12, 16, [
            _assignment(1, 'here', checked_in_at=now),
            _assignment(2, 'late', late_minutes=10),
            _assignment(3, 'missing'),
            _assignment(4, 'drafted', draft=True),
        ], slots=5, shift_id=7),
    ], MIDNIGHT, viewer_id=None, for_staff=True, now=now)

    shift = grid.columns[0].shifts[0]
    assert shift.shift_id == 7 and shift.slots_needed == 5 and shift.notes == 'Meet at stage 2'
    assert [(p.name, p.arrival, p.draft) for p in shift.people] == [
        ('here', ARRIVED, False), ('late', LATE, False), ('missing', NOT_HERE, False),
        ('drafted', '', True),
    ]
    assert shift.people[0].assignment_id == 10
    assert shift.open_slots == 1


def test_nobody_is_missing_before_the_shift_starts():
    grid = build_day_grid([_shift(12, 16, [_assignment(1, 'early')])], MIDNIGHT, None,
                          for_staff=True, now=MIDNIGHT + timedelta(hours=11))
    assert grid.columns[0].shifts[0].people[0].arrival == ''


def test_staff_grid_has_a_column_for_every_position_even_empty_ones():
    grid = build_day_grid([], MIDNIGHT, None, for_staff=True, positions=[(1, 'Admin Desk'), (2, 'Check In')])
    assert [c.position for c in grid.columns] == ['Admin Desk', 'Check In']
    assert (grid.first_hour, grid.last_hour) == (8, 24)


def test_person_status_reads_the_most_useful_fact():
    at = datetime(2026, 10, 4, 15, 52, tzinfo=timezone.utc)
    assert person_status(GridPerson('a', False, draft=True))[0] == 'Draft, not told yet'
    assert person_status(GridPerson('a', False, arrival=ARRIVED, checked_in_at=at))[0].startswith('Checked in')
    assert 'running late' in person_status(GridPerson('a', False, arrival=LATE, late_minutes=10))[0].lower()
    assert person_status(GridPerson('a', False, arrival=NOT_HERE))[0] == 'Not here yet'
    assert person_status(GridPerson('a', False, acknowledged=True))[0] == 'Confirmed'
    assert person_status(GridPerson('a', False))[0] == "Hasn't confirmed yet"


def test_coverage_chips_count_open_slots_and_drafts():
    grid = build_day_grid([
        _shift(8, 12, [_assignment(1, 'a'), _assignment(2, 'b', draft=True)], slots=3),
        _shift(12, 16, [_assignment(3, 'c')], slots=1, shift_id=2),
    ], MIDNIGHT, None, for_staff=True)
    assert coverage_chips(grid) == [
        ('3/4 slots filled', 'neutral'), ('1 open', 'pending'), ('1 draft', 'live'),
    ]


async def test_board_grid_is_staff_only_and_includes_drafts(db):
    staff = await _staff()
    pos = await VolunteerPosition.create(name='Check In')
    await VolunteerPosition.create(name='Empty Desk')
    shift = await VolunteerShift.create(position=pos, starts_at=_at(8), ends_at=_at(12), slots_needed=2)
    drafted = await _volunteer('drafted')
    await VolunteerAssignment.create(shift=shift, user=drafted, auto_generated=True)
    svc = VolunteerScheduleService()

    grid = await svc.board_day_grid(staff, DAY)
    names = {c.position: [p.name for s in c.shifts for p in s.people] for c in grid.columns}
    assert names == {'Check In': ['drafted'], 'Empty Desk': []}

    with pytest.raises(PermissionError):
        await svc.board_day_grid(drafted, DAY)


async def test_move_to_an_overlapping_shift_on_another_position(db):
    staff = await _staff()
    proctor = await VolunteerPosition.create(name='Race Proctor')
    broadcast = await VolunteerPosition.create(name='Broadcast Tech')
    old = await VolunteerShift.create(position=proctor, starts_at=_at(12), ends_at=_at(16))
    new = await VolunteerShift.create(position=broadcast, starts_at=_at(12), ends_at=_at(16))
    vol = await _volunteer('werfjig')
    svc = VolunteerScheduleService()
    assignment, _ = await svc.assign(staff, old, vol)

    moved, _ = await VolunteerShiftMoveService().move_assignment(staff, await svc.get_assignment(assignment.id), new)

    assert moved.shift_id == new.id
    assert not await VolunteerAssignment.filter(shift=old).exists()


async def test_move_that_clashes_leaves_them_where_they_were(db):
    staff = await _staff()
    pos = await VolunteerPosition.create(name='Admin Desk')
    old = await VolunteerShift.create(position=pos, starts_at=_at(8), ends_at=_at(10))
    busy = await VolunteerShift.create(position=pos, starts_at=_at(14), ends_at=_at(16))
    target = await VolunteerShift.create(position=pos, starts_at=_at(15), ends_at=_at(17))
    vol = await _volunteer('dani')
    svc = VolunteerScheduleService()
    assignment, _ = await svc.assign(staff, old, vol)
    await svc.assign(staff, busy, vol)

    with pytest.raises(ValueError, match='overlapping'):
        await VolunteerShiftMoveService().move_assignment(staff, await svc.get_assignment(assignment.id), target)
    assert await VolunteerAssignment.filter(shift=old, user=vol).exists()


async def test_a_moved_draft_stays_a_draft(db):
    staff = await _staff()
    pos = await VolunteerPosition.create(name='Check In')
    old = await VolunteerShift.create(position=pos, starts_at=_at(8), ends_at=_at(12))
    new = await VolunteerShift.create(position=pos, starts_at=_at(12), ends_at=_at(16))
    vol = await _volunteer('drafted')
    draft = await VolunteerAssignment.create(shift=old, user=vol, auto_generated=True)
    svc = VolunteerScheduleService()

    moved, _ = await VolunteerShiftMoveService().move_assignment(staff, await svc.get_assignment(draft.id), new)
    assert moved.auto_generated is True


async def test_swap_puts_the_new_person_in_the_same_place(db):
    staff = await _staff()
    pos = await VolunteerPosition.create(name='Race Proctor')
    shift = await VolunteerShift.create(position=pos, starts_at=_at(12), ends_at=_at(16))
    out = await _volunteer('noshow')
    cover = await _volunteer('cover')
    svc = VolunteerScheduleService()
    assignment, _ = await svc.assign(staff, shift, out)

    swapped, warnings = await VolunteerShiftMoveService().replace_assignment(staff, await svc.get_assignment(assignment.id), cover)

    assert swapped.user_id == cover.id and swapped.shift_id == shift.id
    assert not any('slots filled' in w for w in warnings)
    assert not await VolunteerAssignment.filter(shift=shift, user=out).exists()


async def test_swap_with_someone_busy_removes_nobody(db):
    staff = await _staff()
    pos = await VolunteerPosition.create(name='Race Proctor')
    shift = await VolunteerShift.create(position=pos, starts_at=_at(12), ends_at=_at(16))
    elsewhere = await VolunteerShift.create(position=pos, starts_at=_at(13), ends_at=_at(15))
    out = await _volunteer('out')
    busy = await _volunteer('busy')
    svc = VolunteerScheduleService()
    assignment, _ = await svc.assign(staff, shift, out)
    await svc.assign(staff, elsewhere, busy)

    with pytest.raises(ValueError, match='overlapping'):
        await VolunteerShiftMoveService().replace_assignment(staff, await svc.get_assignment(assignment.id), busy)
    assert await VolunteerAssignment.filter(shift=shift, user=out).exists()


async def test_only_coordinators_move_or_swap(db):
    staff = await _staff()
    pos = await VolunteerPosition.create(name='Check In')
    shift = await VolunteerShift.create(position=pos, starts_at=_at(8), ends_at=_at(12))
    other = await VolunteerShift.create(position=pos, starts_at=_at(12), ends_at=_at(16))
    vol = await _volunteer('vol')
    svc = VolunteerScheduleService()
    assignment, _ = await svc.assign(staff, shift, vol)
    loaded = await svc.get_assignment(assignment.id)

    with pytest.raises(PermissionError):
        await VolunteerShiftMoveService().move_assignment(vol, loaded, other)
    with pytest.raises(PermissionError):
        await VolunteerShiftMoveService().replace_assignment(vol, loaded, staff)
