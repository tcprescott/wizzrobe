"""What Wizzrobe does when an outside service degrades mid-event.

Each case is an answer a provider gives while it is half-down (an empty 200, a
null GraphQL node, a refused connection) and the failure it used to cause: a
sync that pruned the whole mirror, or an error that skipped every handler.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from application.utils.clients.challonge_client import ChallongeAPIError, ChallongeClient
from application.utils.clients.speedgaming_client import SpeedGamingAPIError
from application.utils.clients.startgg_client import StartggAPIError, StartggClient
from models import ChallongeMatch, ChallongeParticipant, Tournament, TournamentPlayers
from tests.services.test_challonge_service import make_service, make_user

pytestmark = pytest.mark.usefixtures("bypass_auth")


def _full(state='underway', participants=True):
    return {
        'tournament': {'id': 'T1', 'name': 'T', 'url': None, 'state': state},
        'participants': [
            {'participant_id': '9001', 'name': 'Alice', 'challonge_user_id': '1001', 'username': 'alice'},
            {'participant_id': '9002', 'name': 'Bob', 'challonge_user_id': None, 'username': None},
        ] if participants else [],
        'matches': [
            {'match_id': '8001', 'state': 'open', 'round': 1,
             'player1_participant_id': '9001', 'player2_participant_id': '9002',
             'winner_participant_id': None},
        ] if participants else [],
    }


async def _synced_bracket():
    actor = await make_user(1, 'admin')
    await make_user(2, 'alice', challonge_user_id='1001')
    tournament = await Tournament.create(name='T', challonge_tournament_id='T1')
    api = MagicMock()
    api.get_tournament_full = AsyncMock(return_value=_full())
    service = make_service(api=api)
    await service.sync_bracket(tournament.id, actor, force=True)
    return actor, tournament, service


async def _mirror_counts(tournament):
    return (
        await ChallongeParticipant.filter(tournament=tournament).count(),
        await ChallongeMatch.filter(tournament=tournament).count(),
        await TournamentPlayers.filter(tournament=tournament).count(),
    )


async def test_challonge_empty_answer_raises_instead_of_wiping_the_mirror(db, monkeypatch):
    actor, tournament, service = await _synced_bracket()
    before = await _mirror_counts(tournament)

    real = ChallongeClient('id', 'secret', token_provider=AsyncMock(return_value='tok'))
    monkeypatch.setattr(real, '_authed_request', AsyncMock(return_value={}))
    service._api_client = lambda: real
    with pytest.raises(ChallongeAPIError):
        await service.sync_bracket(tournament.id, actor, force=True)

    assert await _mirror_counts(tournament) == before


async def test_challonge_started_bracket_with_no_entrants_is_refused(db):
    actor, tournament, service = await _synced_bracket()
    before = await _mirror_counts(tournament)

    service._api_client().get_tournament_full = AsyncMock(
        return_value=_full(state='underway', participants=False),
    )
    with pytest.raises(ValueError, match='nothing was changed'):
        await service.sync_bracket(tournament.id, actor, force=True)

    assert await _mirror_counts(tournament) == before


async def test_challonge_unreachable_is_a_challonge_error(monkeypatch):
    from application.utils.clients import challonge_client as cc
    client = cc.ChallongeClient('id', 'secret', token_provider=AsyncMock(return_value='tok'))
    monkeypatch.setattr(cc, 'BASE_URL', 'http://127.0.0.1:9')
    with pytest.raises(ChallongeAPIError) as exc:
        await client.create_participant('T1', name='x', username='x', misc='m')
    assert exc.value.status is None


async def test_speedgaming_unreachable_is_a_speedgaming_error(monkeypatch):
    from application.utils.clients import speedgaming_client as sc
    monkeypatch.setattr(sc, 'SCHEDULE_URL', 'http://127.0.0.1:9/api/schedule')
    now = datetime.now(timezone.utc)
    with pytest.raises(SpeedGamingAPIError):
        await sc.SpeedGamingClient().fetch_schedule('alttpr', now, now)


async def test_startgg_null_nodes_raise_instead_of_reading_as_empty(monkeypatch):
    client = StartggClient('tok')
    monkeypatch.setattr(client, '_query', AsyncMock(return_value={'event': None}))
    with pytest.raises(StartggAPIError):
        await client.get_entrants('1')
    monkeypatch.setattr(client, '_query', AsyncMock(return_value={'phaseGroup': None}))
    with pytest.raises(StartggAPIError):
        await client.get_pool('1')
