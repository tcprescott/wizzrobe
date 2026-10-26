"""The ready-to-schedule DM for Challonge brackets.

Both players hear about a match once, when it becomes bookable: on the sync
that opens it, or on the staff hand-assignment that links its last entrant.
Re-syncing an unchanged bracket must stay quiet, but a match whose players
change (a result corrected on Challonge moved a different winner up) is
announced to the new pair.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from application.services._challonge_notifications import ChallongeNotificationMixin, is_bookable
from application.services.challonge_service import ChallongeService
from application.utils.match_labels import challonge_round_label
from models import ChallongeMatch, ChallongeParticipant, Match, Tournament, User

pytestmark = pytest.mark.usefixtures("bypass_auth")


def _payload(state='open', p2_challonge_id='1002', round_=1):
    return {
        'tournament': {'id': 'T1', 'name': 'T', 'url': None, 'state': 'underway'},
        'participants': [
            {'participant_id': '9001', 'name': 'Alice', 'challonge_user_id': '1001', 'username': 'alice'},
            {'participant_id': '9002', 'name': 'Bob', 'challonge_user_id': p2_challonge_id, 'username': 'bob'},
        ],
        'matches': [
            {'match_id': '8001', 'state': state, 'round': round_,
             'player1_participant_id': '9001', 'player2_participant_id': '9002',
             'winner_participant_id': None},
        ],
    }


@pytest.fixture
def sent(monkeypatch):
    calls: list[int] = []

    async def record(cmatch):
        calls.append(cmatch.id)

    monkeypatch.setattr(ChallongeNotificationMixin, '_notify_matchup_ready', staticmethod(record))
    return calls


async def _setup(payload):
    actor = await User.create(discord_id=1, username='admin')
    await User.create(discord_id=2, username='alice', challonge_user_id='1001')
    await User.create(discord_id=3, username='bob', challonge_user_id='1002')
    tournament = await Tournament.create(name='T', challonge_tournament_id='T1')
    api = MagicMock()
    api.get_tournament_full = AsyncMock(return_value=payload)
    service = ChallongeService()
    service._api_client = lambda: api
    return service, api, actor, tournament


class TestSyncAnnouncesOnce:
    async def test_sync_that_opens_a_match_sends_it(self, db, sent):
        service, _, actor, tournament = await _setup(_payload())
        await service.sync_bracket(tournament.id, actor)
        cmatch = await ChallongeMatch.get(tournament=tournament, challonge_match_id='8001')
        assert sent == [cmatch.id]

    async def test_resync_of_an_unchanged_bracket_is_quiet(self, db, sent):
        service, _, actor, tournament = await _setup(_payload())
        await service.sync_bracket(tournament.id, actor)
        await service.sync_bracket(tournament.id, actor, force=True)
        assert len(sent) == 1

    async def test_pending_match_waits_until_it_opens(self, db, sent):
        service, api, actor, tournament = await _setup(_payload(state='pending'))
        await service.sync_bracket(tournament.id, actor)
        assert sent == []
        api.get_tournament_full.return_value = _payload(state='open')
        await service.sync_bracket(tournament.id, actor, force=True)
        assert len(sent) == 1

    async def test_unlinked_entrant_is_not_announced(self, db, sent):
        service, _, actor, tournament = await _setup(_payload(p2_challonge_id='9999'))
        await service.sync_bracket(tournament.id, actor)
        assert sent == []

    async def test_a_different_winner_moving_up_announces_again(self, db, sent):
        service, api, actor, tournament = await _setup(_payload())
        await User.create(discord_id=5, username='carol', challonge_user_id='1003')
        await service.sync_bracket(tournament.id, actor)
        assert len(sent) == 1

        corrected = _payload()
        corrected['participants'].append(
            {'participant_id': '9003', 'name': 'Carol', 'challonge_user_id': '1003', 'username': 'carol'})
        corrected['matches'][0]['player2_participant_id'] = '9003'
        api.get_tournament_full.return_value = corrected
        await service.sync_bracket(tournament.id, actor, force=True)
        assert len(sent) == 2

        await service.sync_bracket(tournament.id, actor, force=True)
        assert len(sent) == 2

    async def test_a_failed_dm_never_fails_the_sync(self, db, monkeypatch):
        async def boom(cmatch):
            raise RuntimeError('discord down')

        monkeypatch.setattr(ChallongeNotificationMixin, '_notify_matchup_ready', staticmethod(boom))
        service, _, actor, tournament = await _setup(_payload())
        result = await service.sync_bracket(tournament.id, actor)
        assert result == {'participants': 2, 'matches': 1}


class TestAssignmentAnnounces:
    async def test_linking_the_last_entrant_sends_it(self, db, sent):
        from models import TenantMembership
        from tests.conftest import DEFAULT_TEST_TENANT_ID

        service, _, actor, tournament = await _setup(_payload(p2_challonge_id='9999'))
        guest = await User.create(discord_id=4, username='guest')
        await TenantMembership.create(user=guest, tenant_id=DEFAULT_TEST_TENANT_ID)
        await service.sync_bracket(tournament.id, actor)
        assert sent == []

        bob = await ChallongeParticipant.get(tournament=tournament, challonge_participant_id='9002')
        await service.assign_participant_user(bob.id, guest.id, actor)
        assert len(sent) == 1

        await service.sync_bracket(tournament.id, actor, force=True)
        assert len(sent) == 1


class TestIsBookable:
    async def test_booked_match_is_not_bookable(self, db, sent):
        service, _, actor, tournament = await _setup(_payload())
        await service.sync_bracket(tournament.id, actor)
        cmatch = await ChallongeMatch.get(tournament=tournament, challonge_match_id='8001') \
            .prefetch_related('participant1', 'participant2')
        assert is_bookable(cmatch)
        cmatch.match = await Match.create(tournament=tournament)
        await cmatch.save()
        cmatch = await ChallongeMatch.get(id=cmatch.id).prefetch_related('participant1', 'participant2')
        assert not is_bookable(cmatch)


@pytest.mark.parametrize('round_, label', [
    (1, 'Round 1'), (3, 'Round 3'), (-1, 'Losers round 1'), (-4, 'Losers round 4'), (0, ''), (None, ''),
])
def test_challonge_round_label(round_, label):
    assert challonge_round_label(round_) == label
