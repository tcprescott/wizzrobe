"""Player-side bracket-site rules that had drifted from their staff and native twins.

The Challonge and start.gg player listings skipped archived tournaments' filter
the staff listings apply, and their "matchup ready" DMs never said the stage
plays a best-of set, which the native DM does.
"""

from unittest.mock import AsyncMock

import pytest

from application.repositories import ChallongeRepository, StartggRepository
from application.services import _challonge_notifications, startgg_service
from application.services._bracket.notifications import BracketNotificationMixin
from application.services._challonge_notifications import ChallongeNotificationMixin
from application.services.startgg_service import StartggService
from models import (
    ChallongeMatch,
    ChallongeMatchState,
    ChallongeParticipant,
    StartggEntrant,
    StartggPhase,
    StartggPool,
    StartggSet,
    StartggSetState,
    Tournament,
)
from tests.factories import make_user


async def _challonge_match(player, opponent, *, active=True):
    t = await Tournament.create(name='C', challonge_tournament_id='T1', is_active=active)
    p1 = await ChallongeParticipant.create(tournament=t, challonge_participant_id='1', name='A', user=player)
    p2 = await ChallongeParticipant.create(tournament=t, challonge_participant_id='2', name='B', user=opponent)
    return await ChallongeMatch.create(
        tournament=t, challonge_match_id='8001', state=ChallongeMatchState.OPEN, round=3,
        participant1=p1, participant2=p2,
    )


async def _startgg_set(player, opponent, *, active=True):
    t = await Tournament.create(name='S', startgg_event_slug='tournament/x/event/y', is_active=active)
    phase = await StartggPhase.create(tournament=t, phase_id='1', name='Top 8')
    pool = await StartggPool.create(tournament=t, phase=phase, phase_group_id='2', identifier='1')
    e1 = await StartggEntrant.create(tournament=t, entrant_id='3', name='A', user=player)
    e2 = await StartggEntrant.create(tournament=t, entrant_id='4', name='B', user=opponent)
    return await StartggSet.create(
        tournament=t, pool=pool, set_id='5', state=StartggSetState.OPEN, round=3,
        round_text='Winners Final', entrant1=e1, entrant2=e2,
    )


class TestArchivedTournamentsLeaveThePlayerList:
    async def test_challonge(self, db):
        player, opponent = await make_user(7101, 'p'), await make_user(7102, 'o')
        live = await _challonge_match(player, opponent)
        await _challonge_match(player, opponent, active=False)
        rows = await ChallongeRepository.unscheduled_open_matches_for_user(player)
        assert [r.id for r in rows] == [live.id]

    async def test_startgg(self, db):
        player, opponent = await make_user(7101, 'p'), await make_user(7102, 'o')
        live = await _startgg_set(player, opponent)
        await _startgg_set(player, opponent, active=False)
        rows = await StartggRepository.open_unbooked_sets_for_user(player)
        assert [r.id for r in rows] == [live.id]


@pytest.fixture
def ready_dms(monkeypatch):
    """Run each queued matchup-ready DM's arguments into a list instead of Discord."""
    sent: list[dict] = []

    async def record(**kwargs):
        sent.append(kwargs)

    monkeypatch.setattr(BracketNotificationMixin, '_send_matchup_ready', staticmethod(record))
    queued: list = []
    for module in (_challonge_notifications, startgg_service):
        monkeypatch.setattr(module.discord_queue, 'enqueue', queued.append)
    monkeypatch.setattr(
        _challonge_notifications.notification_links, 'player_challonge_schedule', AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        startgg_service.notification_links, 'player_startgg_schedule', AsyncMock(return_value=None),
    )

    async def drain() -> list[dict]:
        for coro in queued:
            await coro
        queued.clear()
        return sent

    return drain


class TestReadyDmCarriesTheBestOf:
    async def test_challonge_bo3_stage(self, db, ready_dms, monkeypatch):
        cmatch = await _challonge_match(await make_user(7101, 'p'), await make_user(7102, 'o'))
        cmatch = await ChallongeMatch.get(id=cmatch.id).prefetch_related(
            'tournament', 'participant1__user', 'participant2__user',
        )
        best_of = AsyncMock(return_value=3)
        monkeypatch.setattr(
            'application.services.round_schedule_service.RoundScheduleService.best_of_for', best_of,
        )
        await ChallongeNotificationMixin._notify_matchup_ready(cmatch)
        sent = await ready_dms()
        assert [dm['best_of'] for dm in sent] == [3, 3]
        assert best_of.await_args.kwargs == {'challonge_match_id': cmatch.id}

    async def test_startgg_bo3_stage(self, db, ready_dms, monkeypatch):
        row = await _startgg_set(await make_user(7101, 'p'), await make_user(7102, 'o'))
        row = await StartggSet.get(id=row.id).prefetch_related('entrant1__user', 'entrant2__user')
        monkeypatch.setattr(
            'application.services.round_schedule_service.RoundScheduleService.best_of_for',
            AsyncMock(return_value=3),
        )
        await StartggService._notify_matchup_ready(await Tournament.get(id=row.tournament_id), row)
        assert [dm['best_of'] for dm in await ready_dms()] == [3, 3]

    async def test_a_failed_lookup_still_sends_the_dm(self, db, ready_dms, monkeypatch):
        cmatch = await _challonge_match(await make_user(7101, 'p'), await make_user(7102, 'o'))
        cmatch = await ChallongeMatch.get(id=cmatch.id).prefetch_related(
            'tournament', 'participant1__user', 'participant2__user',
        )
        monkeypatch.setattr(
            'application.services.round_schedule_service.RoundScheduleService.best_of_for',
            AsyncMock(side_effect=RuntimeError('db hiccup')),
        )
        await ChallongeNotificationMixin._notify_matchup_ready(cmatch)
        assert [dm['best_of'] for dm in await ready_dms()] == [1, 1]
