"""Self check-in against the database: the banner's query and the shift-start DM.

Integration tests (in-memory SQLite), sharing the helpers in
``test_volunteer_scheduling``.
"""

from datetime import datetime, timedelta, timezone
from typing import ClassVar

from application.services.volunteer.volunteer_arrival_service import VolunteerArrivalService
from models import VolunteerAssignment, VolunteerPosition, VolunteerShift
from tests.services.test_volunteer_scheduling import _user

UTC = timezone.utc


async def _shift(starts_in_min: int, hours: int = 2) -> VolunteerShift:
    pos, _ = await VolunteerPosition.get_or_create(name='Admin Desk')
    starts = datetime.now(UTC) + timedelta(minutes=starts_in_min)
    return await VolunteerShift.create(
        position=pos, starts_at=starts, ends_at=starts + timedelta(hours=hours),
    )


class _RecordingDiscord:
    calls: ClassVar[list] = []

    def send_dm_with_volunteer_arrival_buttons(
        self, user_id, message, assignment_id, embed=None, link=None, notice=None,
    ):
        self.calls.append((assignment_id, link))
        return _noop()

    def send_dm(self, user_id, message, view_factory=None, embed=None, link=None, notice=None):
        return _noop()

    def send_dm_with_volunteer_acknowledgment_button(
        self, user_id, message, assignment_id, embed=None, link=None, notice=None,
    ):
        return _noop()


async def _noop():
    return True, 'mock'


async def test_banner_lists_only_open_unarrived_published_shifts(db):
    vol = await _user('banner')
    due = await VolunteerAssignment.create(shift=await _shift(20), user=vol)
    await VolunteerAssignment.create(shift=await _shift(45), user=vol)
    await VolunteerAssignment.create(shift=await _shift(10), user=vol, auto_generated=True)
    await VolunteerAssignment.create(
        shift=await _shift(-30), user=vol, checked_in_at=datetime.now(UTC),
    )
    await VolunteerAssignment.create(shift=await _shift(-180, hours=2), user=vol)

    rows = await VolunteerArrivalService().awaiting_arrival(vol)

    assert [r.id for r in rows] == [due.id]


async def test_check_in_then_undo_round_trips(db):
    vol = await _user('roundtrip')
    assignment = await VolunteerAssignment.create(shift=await _shift(5), user=vol)
    service = VolunteerArrivalService()

    await service.check_in_self(assignment.id, vol)
    row = await VolunteerAssignment.get(id=assignment.id)
    assert row.checked_in_at is not None and row.checked_in_by_id == vol.id
    assert await service.awaiting_arrival(vol) == []

    await service.undo_check_in(assignment.id, vol)
    row = await VolunteerAssignment.get(id=assignment.id)
    assert row.checked_in_at is None


async def test_shift_start_dm_goes_once_to_who_has_not_arrived(db, monkeypatch):
    from application.services.volunteer import volunteer_reminder

    _RecordingDiscord.calls = []
    monkeypatch.setattr(
        'application.services.discord.discord_service.DiscordService', _RecordingDiscord,
    )
    monkeypatch.setattr(
        'application.services.discord.discord_queue.enqueue', lambda coro: coro.close(),
    )
    started = await _shift(-5)
    waiting = await VolunteerAssignment.create(
        shift=started, user=await _user('waiting'), reminder_sent_at=datetime.now(UTC),
    )
    await VolunteerAssignment.create(
        shift=started, user=await _user('arrived'), checked_in_at=datetime.now(UTC),
        reminder_sent_at=datetime.now(UTC),
    )
    await VolunteerAssignment.create(
        shift=await _shift(-90), user=await _user('stale'), reminder_sent_at=datetime.now(UTC),
    )
    await VolunteerAssignment.create(
        shift=started, user=await _user('draft'), auto_generated=True,
    )

    await volunteer_reminder._tick()
    await volunteer_reminder._tick()

    assert [assignment_id for assignment_id, _ in _RecordingDiscord.calls] == [waiting.id]
    assert _RecordingDiscord.calls[0][1].url.endswith('/home/my-schedule?shifts=1')
    row = await VolunteerAssignment.get(id=waiting.id)
    assert row.start_dm_sent_at is not None
