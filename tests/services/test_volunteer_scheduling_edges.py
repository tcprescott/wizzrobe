"""Volunteer scheduling edges: the day window's boundary, publishing a clash,
deleting shifts people are on, and what the reminder does with a stale row.

Integration tests (in-memory SQLite), sharing the helpers in
``test_volunteer_scheduling``.
"""

from datetime import datetime, timedelta, timezone
from typing import ClassVar

import pytest

from application.services.volunteer.volunteer_autoschedule_service import VolunteerAutoscheduleService
from application.services.volunteer.volunteer_schedule_service import VolunteerScheduleService
from models import VolunteerAssignment, VolunteerPosition, VolunteerShift
from tests.services.test_volunteer_scheduling import (
    _at,
    _available,
    _coordinator,
    _user,
    _volunteer,
)

UTC = timezone.utc


async def test_autofill_sees_a_clash_outside_the_day_window(db):
    """An overnight shift overlapping the window must not double-book someone
    already on a shift that sits wholly before the window."""
    staff = await _coordinator()
    pos = await VolunteerPosition.create(name='Desk')
    sat = _at(0, day=24)
    friday_evening = await VolunteerShift.create(
        position=pos, starts_at=sat - timedelta(hours=4), ends_at=sat - timedelta(hours=1),
    )
    await VolunteerShift.create(
        position=pos, starts_at=sat - timedelta(hours=2), ends_at=sat + timedelta(hours=2),
    )
    vol = await _volunteer('night owl')
    await VolunteerAssignment.create(shift=friday_evening, user=vol, auto_generated=False)
    await _available(vol, sat - timedelta(hours=6), sat + timedelta(hours=6))

    result = await VolunteerAutoscheduleService().generate_draft(
        staff, sat, sat + timedelta(hours=30),
    )

    assert result['created'] == 0
    assert await VolunteerAssignment.filter(user=vol).count() == 1


async def test_publish_refuses_a_draft_that_clashes_with_a_published_shift(db):
    staff = await _coordinator()
    pos = await VolunteerPosition.create(name='Desk')
    morning = await VolunteerShift.create(position=pos, starts_at=_at(8), ends_at=_at(12))
    overlap = await VolunteerShift.create(position=pos, starts_at=_at(10), ends_at=_at(14))
    vol = await _volunteer('busy')
    await VolunteerAssignment.create(shift=morning, user=vol, auto_generated=False)
    draft = await VolunteerAssignment.create(shift=overlap, user=vol, auto_generated=True)

    result = await VolunteerAutoscheduleService().publish_draft(staff, _at(0), _at(23))

    assert result['published'] == 0
    assert len(result['clashes']) == 1
    assert 'busy' in result['clashes'][0] and 'Desk' in result['clashes'][0]
    assert (await VolunteerAssignment.get(id=draft.id)).auto_generated is True

    loaded = await VolunteerScheduleService().get_assignment(draft.id)
    with pytest.raises(ValueError, match='overlaps this shift'):
        await VolunteerScheduleService().confirm_assignment(staff, loaded, notify=False)


async def test_autofill_for_some_positions_keeps_other_positions_drafts(db):
    staff = await _coordinator()
    desk = await VolunteerPosition.create(name='Registration')
    crew = await VolunteerPosition.create(name='Stage Crew')
    desk_shift = await VolunteerShift.create(position=desk, starts_at=_at(8), ends_at=_at(12))
    await VolunteerShift.create(position=crew, starts_at=_at(13), ends_at=_at(17))
    reviewed = await _volunteer('reviewed')
    kept = await VolunteerAssignment.create(shift=desk_shift, user=reviewed, auto_generated=True)

    await VolunteerAutoscheduleService().generate_draft(
        staff, _at(0), _at(23), position_ids=[crew.id],
    )

    assert await VolunteerAssignment.get_or_none(id=kept.id) is not None


async def test_deleting_a_shift_tells_its_published_volunteers(db, monkeypatch):
    staff = await _coordinator()
    pos = await VolunteerPosition.create(name='Desk')
    soon = datetime.now(UTC) + timedelta(days=1)
    shift = await VolunteerShift.create(
        position=pos, starts_at=soon, ends_at=soon + timedelta(hours=4), slots_needed=2,
    )
    told = await _volunteer('told')
    drafted = await _volunteer('drafted')
    await VolunteerAssignment.create(shift=shift, user=told, auto_generated=False)
    await VolunteerAssignment.create(shift=shift, user=drafted, auto_generated=True)

    notified = []

    async def fake_notify(self, user, removed_shift):
        notified.append((user.id, removed_shift.id))

    monkeypatch.setattr(VolunteerScheduleService, '_notify_removed', fake_notify)
    svc = VolunteerScheduleService()
    await svc.delete_shift(staff, await svc.get_shift(shift.id))

    assert notified == [(told.id, shift.id)]


async def test_reset_tells_only_volunteers_on_shifts_still_to_come(db, monkeypatch):
    staff = await _coordinator()
    pos = await VolunteerPosition.create(name='Desk')
    now = datetime.now(UTC)
    past = await VolunteerShift.create(
        position=pos, starts_at=now - timedelta(days=2), ends_at=now - timedelta(days=2, hours=-4),
    )
    upcoming = await VolunteerShift.create(
        position=pos, starts_at=now + timedelta(days=1), ends_at=now + timedelta(days=1, hours=4),
    )
    old = await _volunteer('old')
    new = await _volunteer('new')
    await VolunteerAssignment.create(shift=past, user=old)
    await VolunteerAssignment.create(shift=upcoming, user=new)

    notified = []

    async def fake_notify(self, user, removed_shift):
        notified.append(user.id)

    monkeypatch.setattr(VolunteerScheduleService, '_notify_removed', fake_notify)
    await VolunteerScheduleService().reset_all_shifts(staff)

    assert notified == [new.id]


class _RecordingDiscord:
    """Records which send method the reminder chose; the queue never runs it."""

    calls: ClassVar[list] = []

    def send_dm(self, user_id, message, view_factory=None, embed=None, link=None, notice=None):
        self.calls.append(('plain', link))
        return _noop()

    def send_dm_with_volunteer_acknowledgment_button(
        self, user_id, message, assignment_id, embed=None, link=None, notice=None,
    ):
        self.calls.append(('ack', link))
        return _noop()


async def _noop():
    return True, 'mock'


def _patch_reminder_discord(monkeypatch):
    _RecordingDiscord.calls = []
    monkeypatch.setattr(
        'application.services.discord.discord_service.DiscordService', _RecordingDiscord,
    )
    monkeypatch.setattr(
        'application.services.discord.discord_queue.enqueue', lambda coro: coro.close(),
    )


async def test_reminder_does_not_overwrite_an_acknowledgment_made_mid_tick(db, monkeypatch):
    from application.repositories import VolunteerAssignmentRepository
    from application.services.volunteer import volunteer_reminder

    _patch_reminder_discord(monkeypatch)
    pos = await VolunteerPosition.create(name='Admin Desk')
    soon = datetime.now(UTC) + timedelta(minutes=30)
    shift = await VolunteerShift.create(position=pos, starts_at=soon, ends_at=soon + timedelta(hours=4))
    vol = await _user('mid-tick')
    assignment = await VolunteerAssignment.create(shift=shift, user=vol)

    stale = await VolunteerAssignmentRepository.due_for_reminder(
        datetime.now(UTC), datetime.now(UTC) + timedelta(days=7),
    )
    await VolunteerAssignment.filter(id=assignment.id).update(acknowledged_at=datetime.now(UTC))

    async def fake_due(window_start, window_end):
        return stale

    monkeypatch.setattr(VolunteerAssignmentRepository, 'due_for_reminder', staticmethod(fake_due))
    await volunteer_reminder._tick()

    row = await VolunteerAssignment.get(id=assignment.id)
    assert row.reminder_sent_at is not None
    assert row.acknowledged_at is not None


async def test_acknowledged_reminder_drops_the_button_and_links_the_way_out(db, monkeypatch):
    from application.services.volunteer import volunteer_reminder

    _patch_reminder_discord(monkeypatch)
    pos = await VolunteerPosition.create(name='Admin Desk')
    soon = datetime.now(UTC) + timedelta(minutes=30)
    shift = await VolunteerShift.create(position=pos, starts_at=soon, ends_at=soon + timedelta(hours=4))
    acked = await VolunteerAssignment.create(
        shift=shift, user=await _user('acked'), acknowledged_at=datetime.now(UTC),
    )
    pending = await VolunteerAssignment.create(shift=shift, user=await _user('pending'))

    await volunteer_reminder._tick()

    kinds = sorted(kind for kind, _ in _RecordingDiscord.calls)
    assert kinds == ['ack', 'plain']
    links = {kind: link for kind, link in _RecordingDiscord.calls}
    assert links['plain'].url.endswith(f'/home/my-schedule?release={acked.id}')
    assert links['ack'].url.endswith(f'/home/my-schedule?release={pending.id}')


async def test_moving_a_shift_rearms_its_reminder(db, monkeypatch):
    staff = await _coordinator()
    pos = await VolunteerPosition.create(name='Desk')
    soon = datetime.now(UTC) + timedelta(minutes=30)
    shift = await VolunteerShift.create(position=pos, starts_at=soon, ends_at=soon + timedelta(hours=4))
    vol = await _volunteer('moved')
    assignment = await VolunteerAssignment.create(
        shift=shift, user=vol, reminder_sent_at=datetime.now(UTC),
        acknowledged_at=datetime.now(UTC),
    )

    async def fake_moved(self, *args, **kwargs):
        return None

    monkeypatch.setattr(VolunteerScheduleService, '_notify_shift_moved', fake_moved)
    svc = VolunteerScheduleService()
    later = soon + timedelta(hours=6)
    await svc.update_shift(
        staff, await svc.get_shift(shift.id), starts_at=later, ends_at=later + timedelta(hours=4),
    )

    row = await VolunteerAssignment.get(id=assignment.id)
    assert row.reminder_sent_at is None
    assert row.acknowledged_at is None


async def test_removing_an_assignment_loaded_from_its_shift_dms_the_volunteer(db, monkeypatch):
    """The admin board hands ``unassign`` an assignment from ``shift.assignments``,
    whose ``shift`` relation was never fetched (SGLMAN-1N)."""
    from application.services.volunteer import volunteer_schedule_service as module

    staff = await _coordinator()
    pos = await VolunteerPosition.create(name='Desk')
    soon = datetime.now(UTC) + timedelta(days=1)
    shift = await VolunteerShift.create(position=pos, starts_at=soon, ends_at=soon + timedelta(hours=4))
    vol = await _volunteer('removed')
    await VolunteerAssignment.create(shift=shift, user=vol, auto_generated=False)

    sent = []
    monkeypatch.setattr(module.discord_queue, 'enqueue', lambda coro: (sent.append(coro), coro.close()))
    board_shift = await VolunteerShift.get(id=shift.id).prefetch_related('assignments__user')

    await VolunteerScheduleService().unassign(staff, board_shift.assignments[0])

    assert len(sent) == 1
    assert not await VolunteerAssignment.filter(shift=shift).exists()
