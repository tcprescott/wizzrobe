"""Bracket sync drops what Challonge no longer has (a reset, a cleared roster)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from models import ChallongeMatch, ChallongeParticipant, Match, Tournament, User
from tests.services.test_challonge_service import make_service, make_user

pytestmark = pytest.mark.usefixtures("bypass_auth")


async def test_drops_entrants_and_matches_challonge_no_longer_has(db):
    actor = await make_user(1, 'admin')
    guest = await make_user(2, 'guest')
    tournament = await Tournament.create(name='T', challonge_tournament_id='T1')
    full = {
        'tournament': {'id': 'T1', 'name': 'T', 'url': None, 'state': 'underway'},
        'participants': [
            {'participant_id': '9001', 'name': 'Alice', 'challonge_user_id': None, 'username': None},
            {'participant_id': '9002', 'name': 'Bob', 'challonge_user_id': None, 'username': None},
        ],
        'matches': [
            {'match_id': '8001', 'state': 'open', 'round': 1,
             'player1_participant_id': '9001', 'player2_participant_id': '9002',
             'winner_participant_id': None},
        ],
    }
    api = MagicMock()
    api.get_tournament_full = AsyncMock(return_value=full)
    service = make_service(api=api)
    await service.sync_bracket(tournament.id, actor)
    bob = await ChallongeParticipant.get(tournament=tournament, challonge_participant_id='9002')
    await bob.update_from_dict({'user_id': guest.id, 'user_assigned_manually': True}).save()
    cmatch = await ChallongeMatch.get(challonge_match_id='8001')
    scheduled = await Match.create(tournament=tournament)
    cmatch.match = scheduled
    await cmatch.save()

    # Bracket reset: Bob removed, Carol added, the old match gone.
    full['participants'] = [
        full['participants'][0],
        {'participant_id': '9003', 'name': 'Carol', 'challonge_user_id': None, 'username': None},
    ]
    full['matches'] = []
    await service.sync_bracket(tournament.id, actor, force=True)
    names = await ChallongeParticipant.filter(tournament=tournament).values_list('name', flat=True)
    assert sorted(names) == ['Alice', 'Carol']
    assert not await ChallongeMatch.filter(tournament=tournament).exists()
    assert await Match.filter(id=scheduled.id).exists()

    # Roster cleared on Challonge, which only a reset bracket allows.
    full['tournament']['state'] = 'pending'
    full['participants'] = []
    await service.sync_bracket(tournament.id, actor, force=True)
    assert not await ChallongeParticipant.filter(tournament=tournament).exists()


async def test_players_list_follows_the_bracket(db):
    from models import TenantMembership, TournamentPlayers
    from tests.conftest import DEFAULT_TEST_TENANT_ID
    actor = await make_user(1, 'admin')
    alice = await make_user(2, 'alice', challonge_user_id='1001')
    leftover = await make_user(3, 'leftover')
    guest = await make_user(4, 'guest')
    await TenantMembership.create(user=guest, tenant_id=DEFAULT_TEST_TENANT_ID)
    tournament = await Tournament.create(name='T', challonge_tournament_id='T1')
    await TournamentPlayers.create(tournament=tournament, user=leftover)
    full = {
        'tournament': {'id': 'T1', 'name': 'T', 'url': None, 'state': 'pending'},
        'participants': [
            {'participant_id': '9001', 'name': 'Alice', 'challonge_user_id': '1001', 'username': 'alice'},
            {'participant_id': '9002', 'name': 'Guest', 'challonge_user_id': None, 'username': None},
        ],
        'matches': [],
    }
    api = MagicMock()
    api.get_tournament_full = AsyncMock(return_value=full)
    service = make_service(api=api)

    async def enrolled():
        return set(await TournamentPlayers.filter(tournament=tournament).values_list('user_id', flat=True))

    await service.sync_bracket(tournament.id, actor)
    assert await enrolled() == {alice.id}

    guest_p = await ChallongeParticipant.get(tournament=tournament, challonge_participant_id='9002')
    await service.assign_participant_user(guest_p.id, guest.id, actor)
    assert await enrolled() == {alice.id, guest.id}

    full['participants'] = []
    await service.sync_bracket(tournament.id, actor, force=True)
    assert await enrolled() == set()


async def test_an_invited_entrant_maps_by_linked_username(db):
    actor = await make_user(1, 'admin')
    eykir = await User.create(discord_id=2, username='eykir', challonge_user_id='1741084', challonge_username='Eykir')
    await User.create(discord_id=3, username='twin_a', challonge_user_id='7001', challonge_username='Twin')
    await User.create(discord_id=4, username='twin_b', challonge_user_id='7002', challonge_username='twin')
    await User.create(discord_id=5, username='unverified', challonge_username='Walkin')
    tournament = await Tournament.create(name='T', challonge_tournament_id='T1')
    full = {
        'tournament': {'id': 'T1', 'name': 'T', 'url': None, 'state': 'pending'},
        'participants': [
            {'participant_id': '9001', 'name': 'Eykir', 'challonge_user_id': None, 'username': 'eykir'},
            {'participant_id': '9002', 'name': 'Twin', 'challonge_user_id': None, 'username': 'Twin'},
            {'participant_id': '9003', 'name': 'Walkin', 'challonge_user_id': None, 'username': 'Walkin'},
            {'participant_id': '9004', 'name': 'Eykir', 'challonge_user_id': None, 'username': None},
        ],
        'matches': [],
    }
    api = MagicMock()
    api.get_tournament_full = AsyncMock(return_value=full)
    await make_service(api=api).sync_bracket(tournament.id, actor)

    mapped = dict(await ChallongeParticipant.filter(tournament=tournament)
                  .values_list('challonge_participant_id', 'user_id'))
    assert mapped == {'9001': eykir.id, '9002': None, '9003': None, '9004': None}
