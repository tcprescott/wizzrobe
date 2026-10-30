"""Integration tests for volunteer scheduling services (in-memory SQLite)."""

import itertools
from datetime import datetime, timedelta, timezone

import pytest

from application.services.volunteer.volunteer_autoschedule_service import VolunteerAutoscheduleService
from application.services.volunteer.volunteer_pool_service import VolunteerPoolService
from application.services.volunteer.volunteer_schedule_service import VolunteerScheduleService
from models import (
    Role,
    User,
    UserRole,
    VolunteerAssignment,
    VolunteerAvailability,
    VolunteerAvailabilityStatus,
    VolunteerPosition,
    VolunteerQualification,
    VolunteerShift,
)
from tests.factories import join_community

UTC = timezone.utc

_next_discord_id = itertools.count(100000)


async def _user(name, *, roles=()):
    user = await User.create(discord_id=next(_next_discord_id), username=name, display_name=name)
    await join_community(user)
    for role in roles:
        await UserRole.create(user=user, role=role)
    return user


async def _staff():
    return await _user('staff', roles=[Role.STAFF])


async def _coordinator():
    """An actor outside the assignable pool, for tests that count pool members."""
    return await _user('coordinator', roles=[Role.VOLUNTEER_COORDINATOR])


async def _volunteer(name):
    return await _user(name, roles=[Role.VOLUNTEER])


async def _available(user, start, end, status=VolunteerAvailabilityStatus.AVAILABLE):
    """Declare a window. The autoscheduler's default policy needs one to fill."""
    return await VolunteerAvailability.create(
        user=user, starts_at=start, ends_at=end, status=status,
    )


def _at(hour, day=4):
    return datetime(2026, 10, day, hour, 0, tzinfo=UTC)


async def test_generate_day_shifts_counts_and_midnight(db):
    staff = await _staff()
    p1 = await VolunteerPosition.create(name='Check-in')
    p2 = await VolunteerPosition.create(name='Race Proctor')
    blocks = [('Shift 1', '08:00', '12:00'), ('Shift 4', '20:00', '00:00')]

    shifts = await VolunteerScheduleService().generate_day_shifts(
        staff, '2026-10-04', [p1.id, p2.id], blocks,
    )
    assert len(shifts) == 4  # 2 positions x 2 blocks
    # The 20:00–00:00 block must roll its end into the next day.
    overnight = [s for s in shifts if s.label == 'Shift 4']
    for s in overnight:
        assert s.ends_at > s.starts_at
        assert (s.ends_at - s.starts_at) == timedelta(hours=4)


def test_validate_stagger_rules():
    from application.services.volunteer.volunteer_position_service import VolunteerPositionService
    validate = VolunteerPositionService._validate_stagger

    validate(None, None)   # both unset -> fixed blocks
    validate(240, 120)     # overlapping rolling shifts
    validate(240, 240)     # back-to-back, still continuous coverage

    with pytest.raises(ValueError, match='both'):
        validate(240, None)
    with pytest.raises(ValueError, match='both'):
        validate(None, 120)
    with pytest.raises(ValueError, match='positive'):
        validate(240, 0)
    with pytest.raises(ValueError, match='exceed'):
        validate(120, 240)


async def test_generate_day_shifts_staggered_position(db):
    staff = await _staff()
    tech = await VolunteerPosition.create(
        name='Broadcast Tech', shift_length_minutes=240, stagger_minutes=120,
    )
    # Coverage 08:00–20:00; 4h shifts starting every 2h.
    blocks = [
        ('Shift 1', '08:00', '12:00'),
        ('Shift 2', '12:00', '16:00'),
        ('Shift 3', '16:00', '20:00'),
    ]

    shifts = await VolunteerScheduleService().generate_day_shifts(
        staff, '2026-10-04', [tech.id], blocks,
    )
    shifts = sorted(shifts, key=lambda s: s.starts_at)
    # Starts at 08,10,12,14,16,18 -> 6 rolling shifts, each a single slot.
    assert len(shifts) == 6
    assert all(s.slots_needed == 1 and s.label is None for s in shifts)
    # Consecutive starts are offset by the 2h stagger (handoffs don't bunch up).
    for earlier, later in itertools.pairwise(shifts):
        assert later.starts_at - earlier.starts_at == timedelta(hours=2)
    # Full shifts run 4h; the final one is clamped to the 20:00 coverage end.
    assert all(s.ends_at - s.starts_at == timedelta(hours=4) for s in shifts[:-1])
    assert shifts[-1].ends_at - shifts[-1].starts_at == timedelta(hours=2)
    assert shifts[-1].ends_at == shifts[-2].ends_at  # both land on 20:00


async def test_generate_day_shifts_mixes_staggered_and_fixed(db):
    staff = await _staff()
    fixed = await VolunteerPosition.create(name='Check-in')
    tech = await VolunteerPosition.create(
        name='Broadcast Tech', shift_length_minutes=240, stagger_minutes=120,
    )
    blocks = [('Shift 1', '08:00', '12:00'), ('Shift 2', '12:00', '16:00')]

    shifts = await VolunteerScheduleService().generate_day_shifts(
        staff, '2026-10-04', [fixed.id, tech.id], blocks,
    )
    fixed_shifts = [s for s in shifts if s.position_id == fixed.id]
    tech_shifts = [s for s in shifts if s.position_id == tech.id]
    assert len(fixed_shifts) == 2
    assert {s.label for s in fixed_shifts} == {'Shift 1', 'Shift 2'}
    # The staggered position rolls across 08:00–16:00: starts 08,10,12,14.
    assert len(tech_shifts) == 4
    assert all(s.label is None for s in tech_shifts)


async def test_assign_then_duplicate_and_overlap_rejected(db):
    staff = await _staff()
    pos = await VolunteerPosition.create(name='Admin Desk')
    shift = await VolunteerShift.create(position=pos, starts_at=_at(8), ends_at=_at(12))
    other = await VolunteerShift.create(position=pos, starts_at=_at(10), ends_at=_at(14))
    vol = await _volunteer('alice')
    svc = VolunteerScheduleService()

    assignment, warnings = await svc.assign(staff, shift, vol)
    assert assignment.id is not None
    assert warnings == []

    with pytest.raises(ValueError, match='already on this shift'):
        await svc.assign(staff, shift, vol)

    with pytest.raises(ValueError, match='overlapping'):
        await svc.assign(staff, other, vol)


async def test_assign_overfill_warns_but_allows(db):
    staff = await _staff()
    pos = await VolunteerPosition.create(name='Board Game Room')
    shift = await VolunteerShift.create(position=pos, starts_at=_at(8), ends_at=_at(12), slots_needed=1)
    a = await _volunteer('a')
    b = await _volunteer('b')
    svc = VolunteerScheduleService()

    await svc.assign(staff, shift, a)
    _, warnings = await svc.assign(staff, shift, b)
    assert any('slots filled' in w for w in warnings)
    assert await VolunteerAssignment.filter(shift=shift).count() == 2


async def test_assign_unavailable_warns(db):
    staff = await _staff()
    pos = await VolunteerPosition.create(name='Photography')
    shift = await VolunteerShift.create(position=pos, starts_at=_at(8), ends_at=_at(12))
    vol = await _volunteer('grumpy')
    await VolunteerAvailability.create(
        user=vol, starts_at=_at(7), ends_at=_at(13),
        status=VolunteerAvailabilityStatus.UNAVAILABLE,
    )
    _, warnings = await VolunteerScheduleService().assign(staff, shift, vol)
    assert any('unavailable' in w.lower() for w in warnings)


async def test_acknowledge_sets_timestamp_and_guards_owner(db):
    staff = await _staff()
    pos = await VolunteerPosition.create(name='Admin Desk')
    shift = await VolunteerShift.create(position=pos, starts_at=_at(8), ends_at=_at(12))
    vol = await _volunteer('owner')
    intruder = await _volunteer('intruder')
    svc = VolunteerScheduleService()
    assignment, _ = await svc.assign(staff, shift, vol)

    acked = await svc.acknowledge(assignment.id, vol)
    assert acked.acknowledged_at is not None

    with pytest.raises(ValueError, match='your own'):
        await svc.acknowledge(assignment.id, intruder)


async def test_draft_is_hidden_from_the_volunteers_own_list(db):
    await _staff()
    pos = await VolunteerPosition.create(name='Check-in')
    shift = await VolunteerShift.create(position=pos, starts_at=_at(8), ends_at=_at(12))
    vol = await _volunteer('drafted')
    await VolunteerAssignment.create(shift=shift, user=vol, auto_generated=True)
    svc = VolunteerScheduleService()

    assert await svc.assignments_for_user(vol) == []
    assert len(await svc.assignments_for_user(vol, include_drafts=True)) == 1


async def test_draft_is_never_the_first_dm(db):
    pos = await VolunteerPosition.create(name='Admin Desk')
    soon = datetime.now(UTC) + timedelta(minutes=30)
    shift = await VolunteerShift.create(
        position=pos, starts_at=soon, ends_at=soon + timedelta(hours=4),
    )
    drafted = await _volunteer('drafted')
    published = await _volunteer('published')
    await VolunteerAssignment.create(shift=shift, user=drafted, auto_generated=True)
    await VolunteerAssignment.create(shift=shift, user=published, auto_generated=False)

    from application.repositories import VolunteerAssignmentRepository
    due = await VolunteerAssignmentRepository.due_for_reminder(
        datetime.now(UTC), datetime.now(UTC) + timedelta(hours=1),
    )
    assert [a.user_id for a in due] == [published.id]


async def test_acknowledging_a_draft_is_refused(db):
    pos = await VolunteerPosition.create(name='Admin Desk')
    shift = await VolunteerShift.create(position=pos, starts_at=_at(8), ends_at=_at(12))
    vol = await _volunteer('drafted')
    assignment = await VolunteerAssignment.create(shift=shift, user=vol, auto_generated=True)

    with pytest.raises(ValueError, match='still a draft'):
        await VolunteerScheduleService().acknowledge(assignment.id, vol)


async def test_count_drafts_counts_only_drafts_in_the_window(db):
    await _staff()
    pos = await VolunteerPosition.create(name='Check-in')
    today = await VolunteerShift.create(position=pos, starts_at=_at(8), ends_at=_at(12))
    tomorrow = await VolunteerShift.create(
        position=pos, starts_at=_at(8, day=5), ends_at=_at(12, day=5),
    )
    a = await _volunteer('a')
    b = await _volunteer('b')
    c = await _volunteer('c')
    await VolunteerAssignment.create(shift=today, user=a, auto_generated=True)
    await VolunteerAssignment.create(shift=today, user=b, auto_generated=False)
    await VolunteerAssignment.create(shift=tomorrow, user=c, auto_generated=True)

    assert await VolunteerScheduleService().count_drafts(_at(0), _at(23)) == 1


async def test_publish_draft_commits_and_survives_clear_draft(db):
    staff = await _staff()
    pos = await VolunteerPosition.create(name='Check-in')
    morning = await VolunteerShift.create(position=pos, starts_at=_at(8), ends_at=_at(12))
    afternoon = await VolunteerShift.create(position=pos, starts_at=_at(12), ends_at=_at(16))
    a = await _volunteer('aa')
    b = await _volunteer('bb')
    await VolunteerAssignment.create(shift=morning, user=a, auto_generated=True)
    await VolunteerAssignment.create(shift=afternoon, user=a, auto_generated=True)
    await VolunteerAssignment.create(shift=morning, user=b, auto_generated=True)

    auto = VolunteerAutoscheduleService()
    result = await auto.publish_draft(staff, _at(0), _at(23))
    assert result == {'published': 3, 'volunteers': 2, 'clashes': []}
    assert await VolunteerAssignment.filter(auto_generated=True).count() == 0

    # Idempotent: nothing left to publish.
    assert (await auto.publish_draft(staff, _at(0), _at(23)))['published'] == 0

    # And the published rows are no longer clear_draft's to delete.
    assert await auto.clear_draft(staff, _at(0), _at(23)) == 0
    assert await VolunteerAssignment.all().count() == 3


async def test_assignments_for_user_prefetches_assigned_by(db):
    staff = await _staff()
    pos = await VolunteerPosition.create(name='Admin Desk')
    shift = await VolunteerShift.create(position=pos, starts_at=_at(8), ends_at=_at(12))
    vol = await _volunteer('assignee')
    await VolunteerScheduleService().assign(staff, shift, vol)

    rows = await VolunteerScheduleService().assignments_for_user(vol)
    assert rows[0].assigned_by.id == staff.id


async def test_release_frees_the_slot(db):
    staff = await _staff()
    pos = await VolunteerPosition.create(name='Race Proctor')
    soon = datetime.now(UTC) + timedelta(days=2)
    shift = await VolunteerShift.create(
        position=pos, starts_at=soon, ends_at=soon + timedelta(hours=4), slots_needed=1,
    )
    vol = await _volunteer('quitter')
    svc = VolunteerScheduleService()
    assignment, _ = await svc.assign(staff, shift, vol)

    await svc.release(assignment.id, vol, 'Something came up')

    assert await VolunteerAssignment.get_or_none(id=assignment.id) is None
    rows = await svc.coverage(soon - timedelta(hours=1), soon + timedelta(hours=6))
    assert rows[0]['filled'] == 0 and rows[0]['understaffed'] is True


async def test_shiftmates_are_only_loaded_when_asked_for(db):
    staff = await _staff()
    pos = await VolunteerPosition.create(name='Broadcast Tech')
    shift = await VolunteerShift.create(
        position=pos, starts_at=_at(8), ends_at=_at(12), slots_needed=3,
    )
    me = await _volunteer('me')
    them = await _volunteer('them')
    svc = VolunteerScheduleService()
    await svc.assign(staff, shift, me)
    await svc.assign(staff, shift, them)

    with_mates = await svc.assignments_for_user(me, with_shiftmates=True)
    names = {a.user.preferred_name for a in with_mates[0].shift.assignments}
    assert names == {'me', 'them'}

    # Nobody quietly makes the expensive version the default.
    from tortoise.exceptions import NoValuesFetched
    plain = await svc.assignments_for_user(me)
    with pytest.raises(NoValuesFetched):
        _ = plain[0].shift.assignments[0]


async def test_day_summary_counts_the_states(db):
    staff = await _staff()
    pos = await VolunteerPosition.create(name='Check-in')
    shift = await VolunteerShift.create(
        position=pos, starts_at=_at(8), ends_at=_at(12), slots_needed=2,
    )
    committed = await _volunteer('committed')
    drafted = await _volunteer('drafted')
    svc = VolunteerScheduleService()
    assignment, _ = await svc.assign(staff, shift, committed)
    await svc.acknowledge(assignment.id, committed)
    await VolunteerAssignment.create(shift=shift, user=drafted, auto_generated=True)

    summary = await svc.day_summary(_at(0), _at(23))
    assert summary['filled'] == 2 and summary['open'] == 0
    assert summary['drafts'] == 1 and summary['unacknowledged'] == 0


async def test_day_summary_of_an_empty_day(db):
    summary = await VolunteerScheduleService().day_summary(_at(0), _at(23))
    assert summary['shifts'] == 0 and summary['open'] == 0


async def test_coverage_reports_understaffing(db):
    staff = await _staff()
    pos = await VolunteerPosition.create(name='Race Proctor')
    shift = await VolunteerShift.create(position=pos, starts_at=_at(8), ends_at=_at(12), slots_needed=2)
    vol = await _volunteer('one')
    svc = VolunteerScheduleService()
    await svc.assign(staff, shift, vol)

    rows = await svc.coverage(_at(0), _at(23))
    assert len(rows) == 1
    assert rows[0]['filled'] == 1 and rows[0]['needed'] == 2
    assert rows[0]['understaffed'] is True


async def test_autoschedule_respects_qualification_and_availability(db):
    staff = await _staff()
    proctor = await VolunteerPosition.create(name='Race Proctor')
    tech = await VolunteerPosition.create(name='Broadcast Tech')
    shift = await VolunteerShift.create(position=proctor, starts_at=_at(8), ends_at=_at(12))

    qualified = await _volunteer('qualified')
    await VolunteerQualification.create(user=qualified, position=proctor)
    await VolunteerAvailability.create(
        user=qualified, starts_at=_at(8), ends_at=_at(12),
        status=VolunteerAvailabilityStatus.AVAILABLE,
    )
    # Qualified only for a different position -> ineligible for this shift.
    wrong = await _volunteer('wrongskill')
    await VolunteerQualification.create(user=wrong, position=tech)

    result = await VolunteerAutoscheduleService().generate_draft(staff, _at(0), _at(23))
    assert result['created'] == 1
    assignment = await VolunteerAssignment.get(shift=shift)
    assert assignment.user_id == qualified.id
    assert assignment.auto_generated is True


async def test_autoschedule_load_balances(db):
    staff = await _staff()
    pos = await VolunteerPosition.create(name='Check-in')
    await VolunteerShift.create(position=pos, starts_at=_at(8), ends_at=_at(12))
    await VolunteerShift.create(position=pos, starts_at=_at(12), ends_at=_at(16))
    a = await _volunteer('aa')
    b = await _volunteer('bb')
    for vol in (a, b):
        await _available(vol, _at(8), _at(16))

    result = await VolunteerAutoscheduleService().generate_draft(staff, _at(0), _at(23))
    assert result['created'] == 2
    # Each generalist volunteer should get one of the two non-overlapping shifts.
    holders = {a.user_id for a in await VolunteerAssignment.all()}
    assert holders == {a.id, b.id}


async def test_autoschedule_leaves_unfillable_open_and_clear_draft(db):
    staff = await _coordinator()
    pos = await VolunteerPosition.create(name='Admin Desk')
    shift = await VolunteerShift.create(position=pos, starts_at=_at(8), ends_at=_at(12), slots_needed=3)
    solo = await _volunteer('solo')
    await _available(solo, _at(8), _at(12))

    auto = VolunteerAutoscheduleService()
    result = await auto.generate_draft(staff, _at(0), _at(23))
    assert result['created'] == 1
    assert result['unfilled'] and result['unfilled'][0]['open'] == 2
    assert result['unfilled'][0]['reason'] == 'No qualified volunteer in the pool.'

    # A manual assignment must survive clear_draft; drafts must not.
    manual = await _volunteer('manual')
    await VolunteerScheduleService().assign(staff, shift, manual)
    removed = await auto.clear_draft(staff, _at(0), _at(23))
    assert removed == 1
    remaining = await VolunteerAssignment.filter(shift=shift)
    assert len(remaining) == 1 and remaining[0].user_id == manual.id


async def test_does_not_repeat_the_sixteen_hour_draft(db):
    """The measured F2 failure: four consecutive 4-hour blocks to one person."""
    staff = await _coordinator()
    pos = await VolunteerPosition.create(name='Check-in')
    for hour in (8, 12, 16, 20):
        await VolunteerShift.create(
            position=pos, starts_at=_at(hour), ends_at=_at(hour) + timedelta(hours=4),
        )
    hero = await _volunteer('hero')
    await _available(hero, _at(8), _at(8) + timedelta(hours=16))

    result = await VolunteerAutoscheduleService().generate_draft(staff, _at(0), _at(23) + timedelta(hours=6))

    assert result['created'] == 2  # 8 hours, not 16
    assert await VolunteerAssignment.filter(user=hero).count() == 2
    at_cap = [u for u in result['unfilled']
              if u['reason'] == 'Everyone eligible is at the 8-hour limit.']
    assert len(at_cap) == 2
    assert [r['name'] for r in result['heavy_loads']] == ['hero']


async def test_undeclared_volunteer_is_skipped_unless_the_policy_allows(db):
    staff = await _coordinator()
    pos = await VolunteerPosition.create(name='Check-in')
    await VolunteerShift.create(position=pos, starts_at=_at(8), ends_at=_at(12))
    await _volunteer('undeclared')

    auto = VolunteerAutoscheduleService()
    default_run = await auto.generate_draft(staff, _at(0), _at(23))
    assert default_run['created'] == 0
    assert default_run['unfilled'][0]['reason'] == (
        'Nobody qualified has marked this time as available.'
    )
    assert default_run['outside_availability'] == []

    from application.services.volunteer.volunteer_autoschedule_service import DraftPolicy
    outside_run = await auto.generate_draft(
        staff, _at(0), _at(23), policy=DraftPolicy(fill_outside_availability=True),
    )
    assert outside_run['created'] == 1
    assert [r['name'] for r in outside_run['outside_availability']] == ['undeclared']


async def test_draft_audit_records_the_policy(db):
    from models import AuditLog

    staff = await _coordinator()
    pos = await VolunteerPosition.create(name='Check-in')
    await VolunteerShift.create(position=pos, starts_at=_at(8), ends_at=_at(12), slots_needed=2)
    await _volunteer('undeclared')

    from application.services.volunteer.volunteer_autoschedule_service import DraftPolicy
    await VolunteerAutoscheduleService().generate_draft(
        staff, _at(0), _at(23), policy=DraftPolicy(max_hours=6, fill_outside_availability=True),
    )

    row = await AuditLog.filter(action='volunteer.draft_generated').first()
    import json
    details = json.loads(row.details)
    assert details['max_hours'] == 6
    assert details['fill_outside_availability'] is True
    assert details['open'] == 1
    assert details['pool_size'] == 1


async def test_reminder_loop_fires_once(db, monkeypatch):
    from application.services.volunteer import volunteer_reminder

    class _DummyDiscord:
        async def send_dm_with_volunteer_acknowledgment_button(self, *a, **k):
            return True, 'mock'

    monkeypatch.setattr(
        'application.services.discord.discord_service.DiscordService', _DummyDiscord,
    )

    pos = await VolunteerPosition.create(name='Admin Desk')
    soon = datetime.now(UTC) + timedelta(minutes=30)
    shift = await VolunteerShift.create(
        position=pos, starts_at=soon, ends_at=soon + timedelta(hours=4),
    )
    vol = await _user('reminded')
    await VolunteerAssignment.create(shift=shift, user=vol)

    await volunteer_reminder._tick()
    assignment = await VolunteerAssignment.get(shift=shift)
    assert assignment.reminder_sent_at is not None
    first_stamp = assignment.reminder_sent_at

    # Second tick must not re-fire (already stamped -> filtered out).
    await volunteer_reminder._tick()
    assignment = await VolunteerAssignment.get(shift=shift)
    assert assignment.reminder_sent_at == first_stamp


async def test_reminder_skips_far_future_shift(db, monkeypatch):
    from application.services.volunteer import volunteer_reminder

    class _DummyDiscord:
        async def send_dm_with_volunteer_acknowledgment_button(self, *a, **k):
            return True, 'mock'

    monkeypatch.setattr(
        'application.services.discord.discord_service.DiscordService', _DummyDiscord,
    )

    pos = await VolunteerPosition.create(name='Admin Desk')
    far = datetime.now(UTC) + timedelta(hours=6)  # beyond default 60-min lead
    shift = await VolunteerShift.create(
        position=pos, starts_at=far, ends_at=far + timedelta(hours=4),
    )
    vol = await _user('later')
    await VolunteerAssignment.create(shift=shift, user=vol)

    await volunteer_reminder._tick()
    assignment = await VolunteerAssignment.get(shift=shift)
    assert assignment.reminder_sent_at is None


async def test_assignable_pool_is_volunteer_or_staff_role_users(db):
    staff = await _staff()
    with_role = await _volunteer('withvolunteer')
    both = await _user('both', roles=[Role.VOLUNTEER, Role.STAFF])
    proctor = await _user('proctor', roles=[Role.PROCTOR])
    no_role = await _user('norole')

    pool = await VolunteerPoolService().assignable_volunteers()
    ids = [u.id for u in pool]
    assert with_role.id in ids
    assert staff.id in ids
    assert ids.count(both.id) == 1
    assert proctor.id not in ids
    assert no_role.id not in ids

