"""A player telling staff they're running late for their own match.

What matters: only the match's players can say it, only from shortly before
the match until check-in, staff on duty hear about it, and the opponent never
does (the row data that reaches a non-staff browser carries only your own).
"""

from datetime import datetime, timedelta, timezone

import pytest

import application.services.discord as discord_pkg
from application.events import EventType, event_bus
from application.services import PlayerLateService
from application.services.match.match_display_service import MatchDisplayService
from application.services.match.player_late_service import late_window_open
from models import Match, MatchPlayers, Role, Stage, Tournament, UserRole
from tests.factories import make_user
from theme.tables.match_player_run_sheet import decorate_player_row, keep_own_late_only
from theme.tables.match_run_sheet import late_tags


@pytest.fixture
def captured_events():
    seen = []
    token = event_bus.subscribe_sync(seen.append)
    yield seen
    event_bus.unsubscribe(token)


@pytest.fixture
def dms(monkeypatch):
    pending = []
    monkeypatch.setattr(discord_pkg.discord_queue, 'enqueue', pending.append)
    yield pending
    for coro in pending:
        coro.close()


async def _match(starts_in: timedelta = timedelta(minutes=15), **fields):
    t = await Tournament.create(name='T')
    m = await Match.create(
        tournament=t, scheduled_at=datetime.now(timezone.utc) + starts_in, **fields,
    )
    p1 = await make_user(discord_id=8101, username='p1')
    p2 = await make_user(discord_id=8102, username='p2')
    await MatchPlayers.create(match=m, user=p1)
    await MatchPlayers.create(match=m, user=p2)
    return m, p1, p2


class TestReportLate:
    async def test_records_minutes_and_note_on_the_players_row(self, db, dms, captured_events):
        m, p1, _ = await _match()

        await PlayerLateService().report_late(m.id, p1, 20, '  Train is stuck  ')

        row = await MatchPlayers.get(match=m, user=p1)
        assert row.late_minutes == 20
        assert row.late_note == 'Train is stuck'
        assert row.late_at is not None
        assert [e.event_type for e in captured_events if e.event_type == EventType.MATCH_PLAYER_RUNNING_LATE]

    async def test_staff_are_told_with_the_note(self, db, dms, monkeypatch):
        m, p1, _ = await _match()
        staff = await make_user(discord_id=9101, username='staff')
        await UserRole.create(user=staff, role=Role.STAFF, tenant_id=1)
        sent = []

        async def _send(self, discord_id, message, *args, **kwargs):
            sent.append((discord_id, message, kwargs.get('notice')))
            return True, 'ok'

        monkeypatch.setattr(
            'application.services.discord.discord_service.DiscordService.send_dm', _send,
        )
        await PlayerLateService().report_late(m.id, p1, 10, '@everyone *bus*')
        for coro in list(dms):
            dms.remove(coro)
            await coro

        assert [d for d, _, _ in sent] == [staff.discord_id]
        message = sent[0][1]
        assert 'about 10 minutes' in message
        assert '@\u200beveryone' in message and '\\*bus\\*' in message
        assert sent[0][2].key == 'player_late'

    async def test_a_non_player_is_refused(self, db, dms):
        m, _, _ = await _match()
        outsider = await make_user(discord_id=8103, username='nosy')

        with pytest.raises(ValueError, match='players in a match'):
            await PlayerLateService().report_late(m.id, outsider, 10)

    async def test_only_the_offered_choices(self, db, dms):
        m, p1, _ = await _match()

        with pytest.raises(ValueError, match='how late'):
            await PlayerLateService().report_late(m.id, p1, 45)

    async def test_too_early_points_at_ask_to_change(self, db, dms):
        m, p1, _ = await _match(starts_in=timedelta(hours=3))

        with pytest.raises(ValueError, match='ask to change'):
            await PlayerLateService().report_late(m.id, p1, 10)

    async def test_after_check_in_it_is_refused(self, db, dms):
        m, p1, _ = await _match(seated_at=datetime.now(timezone.utc))

        with pytest.raises(ValueError, match='already checked in'):
            await PlayerLateService().report_late(m.id, p1, 10)

    async def test_a_long_note_is_refused(self, db, dms):
        m, p1, _ = await _match()

        with pytest.raises(ValueError, match='under'):
            await PlayerLateService().report_late(m.id, p1, 10, 'x' * 281)

    async def test_undo_clears_it(self, db, dms, captured_events):
        m, p1, _ = await _match()
        service = PlayerLateService()
        await service.report_late(m.id, p1, 10, 'soon')

        await service.take_back_late(m.id, p1)

        row = await MatchPlayers.get(match=m, user=p1)
        assert (row.late_minutes, row.late_at, row.late_note) == (None, None, None)
        assert EventType.MATCH_PLAYER_LATE_CLEARED in [e.event_type for e in captured_events]


class TestWindow:
    async def test_opens_half_an_hour_before(self, db):
        m, _, _ = await _match(starts_in=timedelta(minutes=29))
        await m.fetch_related('tournament')
        assert late_window_open(m)
        m.scheduled_at = datetime.now(timezone.utc) + timedelta(minutes=31)
        assert not late_window_open(m)

    async def test_stays_open_past_the_start_until_check_in(self, db):
        m, _, _ = await _match(starts_in=-timedelta(minutes=10))
        await m.fetch_related('tournament')
        assert late_window_open(m)

    async def test_a_stage_match_opens_when_its_reminder_goes_out(self, db):
        stage = await Stage.create(name='Kraid')
        m, _, _ = await _match(starts_in=timedelta(minutes=50), stage=stage)
        await Tournament.filter(id=m.tournament_id).update(stage_reminder_minutes=60)
        m = await Match.get(id=m.id).prefetch_related('tournament')
        assert late_window_open(m)


class TestRows:
    async def test_the_display_row_carries_who_is_late(self, db, dms):
        m, p1, _ = await _match()
        await PlayerLateService().report_late(m.id, p1, 30, 'parking')

        row = await MatchDisplayService().get_match_for_display(m.id)

        assert row['late_window'] is True
        assert [(e['user_id'], e['tag'], e['note']) for e in row['late']] == [(p1.id, '30+ min', 'parking')]
        assert late_tags(row)[0]['label'].startswith(f'{p1.preferred_name} 30+ min late · said ')

    async def test_check_in_drops_it_from_the_row(self, db, dms):
        m, p1, _ = await _match()
        await PlayerLateService().report_late(m.id, p1, 10)
        await Match.filter(id=m.id).update(seated_at=datetime.now(timezone.utc))

        row = await MatchDisplayService().get_match_for_display(m.id)

        assert row['late'] == []

    async def test_the_opponent_never_gets_it(self, db, dms):
        m, p1, p2 = await _match()
        await PlayerLateService().report_late(m.id, p1, 10, 'between me and staff')
        row = await MatchDisplayService().get_match_for_display(m.id)

        keep_own_late_only([row], p2.discord_id)

        assert row['late'] == []

    async def test_the_player_sees_their_own_as_status_and_undo(self, db, dms):
        m, p1, _ = await _match()
        row = await MatchDisplayService().get_match_for_display(m.id)
        decorate_player_row(row, p1.discord_id)
        assert row['_can_late'] is True and row['_my_late'] == ''

        await PlayerLateService().report_late(m.id, p1, 20)
        row = await MatchDisplayService().get_match_for_display(m.id)
        keep_own_late_only([row], p1.discord_id)
        decorate_player_row(row, p1.discord_id)

        assert row['_my_late'] == '~20 min'
        assert row['_can_late'] is False
        assert row['_status'] == 'Running ~20 min'
