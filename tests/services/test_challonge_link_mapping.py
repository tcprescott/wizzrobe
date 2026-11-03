"""A newly linked Challonge account maps onto already-mirrored entrants without a sync."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from models import ChallongeParticipant, Tournament, TournamentPlayers
from tests.services.test_challonge_service import make_service, make_user

pytestmark = pytest.mark.usefixtures("bypass_auth")


class TestLinkMapsSyncedEntrants:
    async def test_linking_maps_an_already_synced_entrant_without_a_sync(self, db):
        actor = await make_user(1, 'admin')
        bob = await make_user(2, 'bob')
        hand_picked = await make_user(3, 'carol')
        tournament = await Tournament.create(name='T', challonge_tournament_id='T1')
        api = MagicMock()
        api.get_tournament_full = AsyncMock(return_value={
            'tournament': {'id': 'T1', 'name': 'T', 'url': None, 'state': 'underway'},
            'participants': [
                {'participant_id': '9002', 'name': 'Bob', 'challonge_user_id': '9999', 'username': 'bob'},
                {'participant_id': '9003', 'name': 'Bob alt', 'challonge_user_id': '9999', 'username': 'bob'},
            ],
            'matches': [],
        })
        service = make_service(api=api)
        await service.sync_bracket(tournament.id, actor)
        alt = await ChallongeParticipant.get(tournament=tournament, challonge_participant_id='9003')
        alt.user_id, alt.user_assigned_manually = hand_picked.id, True
        await alt.save(update_fields=['user_id', 'user_assigned_manually'])
        api.get_tournament_full.reset_mock()

        await service.record_player_link(bob, '9999', 'bob', actor=bob)

        api.get_tournament_full.assert_not_called()
        bob_p = await ChallongeParticipant.get(tournament=tournament, challonge_participant_id='9002')
        assert bob_p.user_id == bob.id and not bob_p.user_assigned_manually
        alt = await ChallongeParticipant.get(tournament=tournament, challonge_participant_id='9003')
        assert alt.user_id == hand_picked.id
        assert await TournamentPlayers.filter(tournament=tournament, user=bob).exists()

    async def test_linking_with_nothing_mirrored_just_links(self, db):
        user = await make_user(2, 'bob')
        await make_service().record_player_link(user, '9999', 'bob', actor=user)
        await user.refresh_from_db()
        assert user.challonge_user_id == '9999'
