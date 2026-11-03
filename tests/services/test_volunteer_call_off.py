"""Staff calling off a volunteer's shift, against the database.

A called-off assignment stays on the shift, skips check-in (banner, start DM,
reminder) and counts as worked; the volunteer is DMed each way.
"""

from datetime import datetime, timedelta, timezone

import pytest

from application.services.analytics_service import AnalyticsService
from application.services.volunteer.volunteer_arrival_service import VolunteerArrivalService
from application.services.volunteer.volunteer_call_off_service import (
    VolunteerCallOffService,
    can_call_off,
)
from application.services.volunteer.volunteer_schedule_grid import CALLED_OFF, build_day_grid
from application.services.volunteer.volunteer_schedule_service import VolunteerScheduleService
from application.utils.discord_messages_volunteer import (
    call_off_description,
    call_off_title,
    volunteer_called_off_dm,
)
from models import AuditLog, VolunteerAssignment
from tests.services.test_volunteer_arrival import _shift
from tests.services.test_volunteer_scheduling import _coordinator, _user, _volunteer

UTC = timezone.utc


@pytest.fixture
def sent(monkeypatch):
    """The DMs the service queued, as (discord id, embed title, notice key, link)."""
    calls = []

    async def _noop():
        return True, 'mock'

    def send_dm(self, user_id, message, view_factory=None, embed=None, link=None, notice=None, **_):
        calls.append((user_id, embed.title if embed else None, notice.key if notice else None, link))
        return _noop()

    monkeypatch.setattr(
        'application.services.discord.discord_service.DiscordService.send_dm', send_dm,
    )
    monkeypatch.setattr(
        'application.services.discord.discord_queue.enqueue', lambda coro: coro.close(),
    )
    return calls


async def test_call_off_marks_it_and_dms_the_volunteer(db, sent):
    coordinator = await _coordinator()
    vol = await _volunteer('callee')
    assignment = await VolunteerAssignment.create(shift=await _shift(60), user=vol, late_minutes=10)

    await VolunteerCallOffService().call_off(assignment.id, coordinator, '  Overstaffed, enjoy!  ')

    row = await VolunteerAssignment.get(id=assignment.id)
    assert row.called_off_at is not None
    assert row.called_off_by_id == coordinator.id
    assert row.called_off_note == 'Overstaffed, enjoy!'
    assert row.late_minutes is None
    assert row.counts_as_worked
    assert [(s[0], s[2]) for s in sent] == [(vol.discord_id, 'shift_called_off')]
    assert "not needed" in sent[0][1]
    assert sent[0][3].url.endswith('/home/my-schedule?shifts=1')
    assert await AuditLog.filter(action='volunteer.called_off').count() == 1


async def test_call_off_twice_is_a_no_op(db, sent):
    coordinator = await _coordinator()
    assignment = await VolunteerAssignment.create(shift=await _shift(60), user=await _volunteer('twice'))
    service = VolunteerCallOffService()

    await service.call_off(assignment.id, coordinator)
    await service.call_off(assignment.id, coordinator)

    assert len(sent) == 1


async def test_only_coordinators_can_call_off(db, sent):
    vol = await _volunteer('self')
    assignment = await VolunteerAssignment.create(shift=await _shift(60), user=vol)

    with pytest.raises((PermissionError, ValueError)):
        await VolunteerCallOffService().call_off(assignment.id, vol)
    assert (await VolunteerAssignment.get(id=assignment.id)).called_off_at is None


async def test_refuses_drafts_and_finished_shifts(db, sent):
    coordinator = await _coordinator()
    draft = await VolunteerAssignment.create(
        shift=await _shift(60), user=await _volunteer('draft'), auto_generated=True,
    )
    done = await VolunteerAssignment.create(
        shift=await _shift(-180, hours=2), user=await _volunteer('done'),
    )
    service = VolunteerCallOffService()

    with pytest.raises(ValueError, match='draft'):
        await service.call_off(draft.id, coordinator)
    with pytest.raises(ValueError, match='finished'):
        await service.call_off(done.id, coordinator)
    assert sent == []


async def test_started_shift_sends_them_home_and_keeps_the_check_in(db, sent):
    coordinator = await _coordinator()
    assignment = await VolunteerAssignment.create(
        shift=await _shift(-60, hours=3), user=await _volunteer('early'),
        checked_in_at=datetime.now(UTC),
    )

    await VolunteerCallOffService().call_off(assignment.id, coordinator)

    row = await VolunteerAssignment.get(id=assignment.id)
    assert row.called_off_at is not None and row.checked_in_at is not None
    assert 'head out' in sent[0][1]


async def test_whole_shift_skips_drafts_and_the_already_called_off(db, sent):
    coordinator = await _coordinator()
    shift = await _shift(60)
    first = await VolunteerAssignment.create(shift=shift, user=await _volunteer('one'))
    second = await VolunteerAssignment.create(shift=shift, user=await _volunteer('two'))
    draft = await VolunteerAssignment.create(shift=shift, user=await _volunteer('d'), auto_generated=True)
    service = VolunteerCallOffService()
    await service.call_off(first.id, coordinator)
    sent.clear()

    done = await service.call_off_shift(shift.id, coordinator, 'Stage is dark')

    assert [a.id for a in done] == [second.id]
    assert len(sent) == 1
    assert (await VolunteerAssignment.get(id=draft.id)).called_off_at is None
    with pytest.raises(ValueError, match='Nobody'):
        await service.call_off_shift(shift.id, coordinator)


async def test_undo_puts_them_back_and_dms(db, sent):
    coordinator = await _coordinator()
    assignment = await VolunteerAssignment.create(shift=await _shift(60), user=await _volunteer('back'))
    service = VolunteerCallOffService()
    await service.call_off(assignment.id, coordinator, 'note')

    await service.undo_call_off(assignment.id, coordinator)

    row = await VolunteerAssignment.get(id=assignment.id)
    assert row.called_off_at is None and row.called_off_by_id is None and row.called_off_note is None
    assert [s[2] for s in sent] == ['shift_called_off', 'shift_call_off_undone']
    assert 'after all' in sent[1][1]


async def test_called_off_skips_banner_start_dm_and_reminder(db, sent):
    from application.repositories import VolunteerAssignmentRepository

    coordinator = await _coordinator()
    vol = await _volunteer('skipper')
    started = await _shift(-5)
    assignment = await VolunteerAssignment.create(shift=started, user=vol)
    upcoming = await VolunteerAssignment.create(shift=await _shift(30), user=vol)
    service = VolunteerCallOffService()
    await service.call_off(assignment.id, coordinator)
    await service.call_off(upcoming.id, coordinator)
    now = datetime.now(UTC)

    assert await VolunteerArrivalService().awaiting_arrival(vol) == []
    assert await VolunteerAssignmentRepository.due_for_start_dm(now - timedelta(minutes=30), now) == []
    assert await VolunteerAssignmentRepository.due_for_reminder(now, now + timedelta(hours=2)) == []
    with pytest.raises(ValueError, match='called off'):
        await VolunteerArrivalService().check_in_self(assignment.id, vol)
    with pytest.raises(ValueError, match='called off'):
        await VolunteerArrivalService().report_late(upcoming.id, vol, 10)
    with pytest.raises(ValueError, match='called off'):
        await VolunteerScheduleService().release(upcoming.id, vol)
    with pytest.raises(ValueError, match='called off'):
        await VolunteerScheduleService().check_in(assignment.id, coordinator)


async def test_called_off_counts_as_attended_in_reports(db, sent):
    coordinator = await _coordinator()
    shift = await _shift(60)
    assignment = await VolunteerAssignment.create(shift=shift, user=await _volunteer('credited'))
    await VolunteerCallOffService().call_off(assignment.id, coordinator)
    now = datetime.now(UTC)

    report = await AnalyticsService().volunteer_hour_trends(
        now - timedelta(days=1), now + timedelta(days=1), 'day', actor=coordinator,
    )

    assert report['totals']['checked_in_hours'] == report['totals']['scheduled_hours'] == 2.0


async def test_board_shows_who_called_it_off(db, sent):
    coordinator = await _user('Thomas', roles=[])
    from models import Role, UserRole
    await UserRole.create(user=coordinator, role=Role.STAFF)
    from datetime import time

    from application.utils.timezone import combine_local, today_local
    from models import VolunteerPosition, VolunteerShift

    # Noon tomorrow, not an hour from now: the board day runs 06:00 to 06:00.
    day = today_local() + timedelta(days=1)
    starts = combine_local(day, time(12, 0))
    pos, _ = await VolunteerPosition.get_or_create(name='Admin Desk')
    shift = await VolunteerShift.create(position=pos, starts_at=starts, ends_at=starts + timedelta(hours=2))
    assignment = await VolunteerAssignment.create(shift=shift, user=await _volunteer('shown'))
    await VolunteerCallOffService().call_off(assignment.id, coordinator)

    grid = await VolunteerScheduleService().board_day_grid(coordinator, day)
    people = [p for c in grid.columns for s in c.shifts for p in s.people]

    assert [(p.arrival, p.called_off_by) for p in people] == [(CALLED_OFF, 'Thomas')]
    assert [s.called_off for c in grid.columns for s in c.shifts] == [True]


def test_can_call_off_rules():
    from types import SimpleNamespace

    now = datetime.now(UTC)
    shift = SimpleNamespace(ends_at=now + timedelta(hours=1))
    base = dict(called_off_at=None, auto_generated=False, shift=shift)
    assert can_call_off(SimpleNamespace(**base), now)
    assert not can_call_off(SimpleNamespace(**{**base, 'called_off_at': now}), now)
    assert not can_call_off(SimpleNamespace(**{**base, 'auto_generated': True}), now)
    assert not can_call_off(SimpleNamespace(**{**base, 'shift': SimpleNamespace(ends_at=now)}), now)


def test_grid_whole_shift_called_off_ignores_drafts():
    from types import SimpleNamespace

    now = datetime.now(UTC)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    starts = midnight + timedelta(hours=10)

    def person(name, **kw):
        return SimpleNamespace(
            id=1, user=SimpleNamespace(preferred_name=name), user_id=hash(name) % 1000,
            auto_generated=kw.get('draft', False), acknowledged_at=None,
            checked_in_at=None, late_minutes=None, called_off_at=kw.get('off'), called_off_by_id=7,
        )

    shift = SimpleNamespace(
        id=1, starts_at=starts, ends_at=starts + timedelta(hours=2), label='', notes='',
        slots_needed=2, position_id=1, position=SimpleNamespace(name='Desk'),
        assignments=[person('a', off=now), person('b', draft=True)],
    )
    grid = build_day_grid([shift], midnight, 0, for_staff=True, now=now, caller_names={7: 'Thomas'})
    (only,) = grid.columns[0].shifts

    assert only.called_off
    assert only.can_call_off == 0


def test_call_off_copy():
    assert call_off_title('Registration', started=False) == "You're not needed for Registration"
    assert call_off_title('Registration', started=True) == 'You can head out from Registration'
    body = call_off_description(4.0, started=False, note='@everyone *hi*', by_name='Thomas')
    assert 'all 4 hours' in body
    assert '@everyone' not in body
    assert 'Note from Thomas' in body
    assert '1 hour ' in call_off_description(1.0, started=False) + ' '
    text = volunteer_called_off_dm('Desk', None, 'a', 'b', 2.5, started=True)
    assert 'head out' in text and '2.5 hours' in text
