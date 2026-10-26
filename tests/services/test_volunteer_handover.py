"""Who a volunteer takes over from and hands off to, against the database."""

from datetime import datetime, timedelta, timezone
from typing import ClassVar

from application.services.volunteer import volunteer_reminder
from application.services.volunteer.volunteer_reminder import handover_for
from application.utils.discord_messages_volunteer import handover_text
from models import VolunteerAssignment, VolunteerPosition, VolunteerShift
from tests.services.test_volunteer_scheduling import _user

UTC = timezone.utc
BASE = datetime(2026, 10, 23, 16, 0, tzinfo=UTC)


async def _slot(position: str, start_h: float, end_h: float) -> VolunteerShift:
    pos, _ = await VolunteerPosition.get_or_create(name=position)
    return await VolunteerShift.create(
        position=pos,
        starts_at=BASE + timedelta(hours=start_h),
        ends_at=BASE + timedelta(hours=end_h),
        slots_needed=2,
    )


async def _on(shift, user, **kw) -> VolunteerAssignment:
    return await VolunteerAssignment.create(shift=shift, user=user, **kw)


def _names(users):
    return [u.username for u in users]


async def test_back_to_back_shifts_name_both_sides(db):
    before, mine, after = (
        await _slot('Check In', -4, 0), await _slot('Check In', 0, 4), await _slot('Check In', 4, 6),
    )
    me = await _user('me')
    await _on(before, await _user('early1'))
    await _on(before, await _user('early2'))
    await _on(after, await _user('late1'))
    assignment = await _on(mine, me)
    await assignment.fetch_related('shift')

    handover = await handover_for(assignment)

    assert _names(handover.relieving) == ['early1', 'early2']
    assert _names(handover.relieved_by) == ['late1']


async def test_only_real_handovers_count(db):
    before, mine, after = (
        await _slot('Check In', -4, 0), await _slot('Check In', 0, 4), await _slot('Check In', 4, 6),
    )
    me, staying = await _user('me'), await _user('staying')
    await _on(before, me)                                   # my own previous shift
    await _on(before, staying)                              # carries on alongside me
    await _on(mine, staying)
    await _on(before, await _user('draft'), auto_generated=True)
    await _on(before, await _user('calledoff'), called_off_at=BASE)
    await _on(await _slot('Admin Desk', -2, 0), await _user('otherpos'))
    await _on(await _slot('Check In', -3, -0.5), await _user('gap'))       # ends 30 min early
    await _on(await _slot('Check In', -3, 0.25), await _user('slack'))     # ends 15 min late
    await _on(after, me)                                    # I stay on
    assignment = await _on(mine, me)
    await assignment.fetch_related('shift')

    handover = await handover_for(assignment)

    assert _names(handover.relieving) == ['slack']
    assert handover.relieved_by == []


def test_handover_text_reads_both_ways():
    assert handover_text(['a', 'b'], ['c'], '8:00 PM') == (
        'Taking over from a, b\nHanding off to c at 8:00 PM'
    )
    assert handover_text([], ['c'], '8:00 PM') == 'Handing off to c at 8:00 PM'
    assert handover_text([], [], '8:00 PM') == ''


class _Recording:
    calls: ClassVar[list] = []

    def send_dm_with_volunteer_arrival_buttons(
        self, user_id, message, assignment_id, embed=None, link=None, notice=None,
    ):
        self.calls.append((message, embed))
        return _noop()

    def send_dm(self, *a, **kw):
        return _noop()

    def send_dm_with_volunteer_acknowledgment_button(self, *a, **kw):
        return _noop()


async def _noop():
    return True, 'mock'


async def test_shift_start_dm_carries_the_handover(db, monkeypatch):
    _Recording.calls = []
    monkeypatch.setattr('application.services.discord.discord_service.DiscordService', _Recording)
    monkeypatch.setattr('application.services.discord.discord_queue.enqueue', lambda coro: coro.close())
    now = datetime.now(UTC).replace(microsecond=0)
    pos, _ = await VolunteerPosition.get_or_create(name='Race Proctor')
    before = await VolunteerShift.create(position=pos, starts_at=now - timedelta(hours=4), ends_at=now - timedelta(minutes=5))
    mine = await VolunteerShift.create(position=pos, starts_at=now - timedelta(minutes=5), ends_at=now + timedelta(hours=4))
    outgoing = await _user('out_going')
    await _on(before, outgoing, reminder_sent_at=now, start_dm_sent_at=now)
    await _on(mine, await _user('incoming'), reminder_sent_at=now)

    await volunteer_reminder._tick()

    [(message, embed)] = _Recording.calls
    assert 'Taking over from out\\_going' in message
    assert any(f.value == f'Taking over from <@{outgoing.discord_id}>' for f in embed.fields)
