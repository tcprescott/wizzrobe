"""One-tap results: record + finish, and confirm too for whoever may confirm.

Real roles, no ``bypass_auth``: whether the result comes out confirmed depends
on *who* tapped it.
"""

import json
from unittest.mock import MagicMock

import pytest

from application.services import MatchReportService
from application.services.notification_catalog import MATCH_CONFIRMED, MATCH_FINISHED
from models import AuditLog, Match, MatchPlayers, Tournament
from tests.factories import utc
from tests.services._match_schedule_setup import build_service, make_dm_user, make_proctor, make_staff


@pytest.fixture
def service():
    svc = MatchReportService()
    svc.schedule_service = build_service()
    return svc


async def _live_match(service, actor, *, players=2, first_id=100) -> tuple[Match, list[MatchPlayers]]:
    t = await Tournament.create(name="T")
    m = await Match.create(tournament=t, scheduled_at=utc(2025, 1, 15, 19, 30))
    rows = [await MatchPlayers.create(match=m, user=await make_dm_user(first_id + i, name=f"p{first_id + i}"))
            for i in range(players)]
    await service.schedule_service.seat_match(m, actor)
    await service.schedule_service.start_match(m, actor)
    return m, rows


async def _actions(match_id: int) -> list[str]:
    def details(a) -> dict:
        return json.loads(a.details) if isinstance(a.details, str) else (a.details or {})
    return [a.action for a in await AuditLog.all().order_by('id') if details(a).get('match_id') == match_id]


async def _noop():
    return None


class TestReport:
    async def test_staff_result_is_recorded_finished_and_confirmed(self, service, db):
        staff = await make_staff()
        m, (p1, _p2) = await _live_match(service, staff)

        assert await service.report(m.id, p1.id, staff) is True

        await m.refresh_from_db()
        assert m.finished_at and m.confirmed_at
        assert (await MatchPlayers.get(id=p1.id)).finish_rank == 1
        assert [a for a in await _actions(m.id) if a in (
            'match.result_recorded', 'match.finished', 'match.confirmed')] == [
            'match.result_recorded', 'match.finished', 'match.confirmed']

    async def test_staff_result_tells_players_once_not_finished_then_confirmed(self, service, db):
        staff = await make_staff()
        m, (p1, _p2) = await _live_match(service, staff)
        notify = service.schedule_service.notify_match_participants = MagicMock(side_effect=lambda *a, **k: _noop())

        await service.report(m.id, p1.id, staff)

        notices = [c.kwargs['notice'] for c in notify.call_args_list]
        assert notices == [MATCH_CONFIRMED]

    async def test_proctor_result_waits_for_an_admin(self, service, db):
        proctor = await make_proctor()
        m, (p1, _p2) = await _live_match(service, proctor)
        notify = service.schedule_service.notify_match_participants = MagicMock(side_effect=lambda *a, **k: _noop())

        assert await service.report(m.id, p1.id, proctor) is False

        await m.refresh_from_db()
        assert m.finished_at and not m.confirmed_at
        assert [c.kwargs['notice'] for c in notify.call_args_list] == [MATCH_FINISHED]

    async def test_flag_leaves_it_unconfirmed_and_flagged(self, service, db):
        staff = await make_staff()
        m, (p1, _p2) = await _live_match(service, staff)

        assert await service.report(m.id, p1.id, staff, flag=True) is False

        await m.refresh_from_db()
        assert m.finished_at and not m.confirmed_at and m.needs_review


class TestCheck:
    async def test_names_the_winner_and_whether_it_confirms(self, service, db):
        proctor = await make_proctor()
        m, (p1, _p2) = await _live_match(service, proctor)

        checked = await service.check(m.id, p1.id, proctor)

        assert (checked.winner_name, checked.confirms) == ('P100', False)

    async def test_writes_nothing(self, service, db):
        staff = await make_staff()
        m, (p1, _p2) = await _live_match(service, staff)

        await service.check(m.id, p1.id, staff)

        await m.refresh_from_db()
        assert not m.finished_at
        assert (await MatchPlayers.get(id=p1.id)).finish_rank is None

    async def test_refuses_a_player_from_another_match(self, service, db):
        staff = await make_staff()
        m, _ = await _live_match(service, staff)
        _other, (stranger, _) = await _live_match(service, staff, first_id=200)

        with pytest.raises(ValueError, match="isn't part"):
            await service.check(m.id, stranger.id, staff)

    async def test_refuses_a_finished_match(self, service, db):
        staff = await make_staff()
        m, (p1, _p2) = await _live_match(service, staff)
        await service.report(m.id, p1.id, staff)

        with pytest.raises(ValueError, match='already finished'):
            await service.check(m.id, p1.id, staff)

    async def test_refuses_more_than_two_players(self, service, db):
        staff = await make_staff()
        m, (p1, *_rest) = await _live_match(service, staff, players=3)

        with pytest.raises(ValueError, match='more than two'):
            await service.check(m.id, p1.id, staff)

    async def test_refuses_someone_who_cannot_run_it(self, service, db):
        staff = await make_staff()
        m, (p1, _p2) = await _live_match(service, staff)
        nobody = await make_dm_user(555, name='nobody')

        with pytest.raises(PermissionError):
            await service.check(m.id, p1.id, nobody)
