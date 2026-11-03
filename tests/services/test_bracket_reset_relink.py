"""A bracket reset on Challonge or start.gg keeps its bookings.

A reset renumbers every match, so the sync that follows drops the mirrored rows
a booking hung off. When the restarted bracket pairs the same two players
again, the sync links their booking back instead of offering the matchup as new.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from application.services._challonge_notifications import ChallongeNotificationMixin
from application.services.challonge_service import ChallongeService
from application.services.startgg_service import StartggService
from models import AuditLog, ChallongeMatch, Match, MatchPlayers, StartggSet, Tournament, User
from tests.services.test_startgg_service import _linked, _players, client, sent  # noqa: F401

pytestmark = pytest.mark.usefixtures("bypass_auth")


def _bracket(match_ids, state='underway'):
    return {
        'tournament': {'id': 'T1', 'name': 'T', 'url': None, 'state': state},
        'participants': [
            {'participant_id': '9001', 'name': 'Alice', 'challonge_user_id': '1001', 'username': 'alice'},
            {'participant_id': '9002', 'name': 'Bob', 'challonge_user_id': '1002', 'username': 'bob'},
        ],
        'matches': [
            {'match_id': mid, 'state': 'open', 'round': 1,
             'player1_participant_id': '9001', 'player2_participant_id': '9002',
             'winner_participant_id': None}
            for mid in match_ids
        ],
    }


@pytest.fixture
def announced(monkeypatch):
    calls: list = []

    async def record(cmatch):
        calls.append(cmatch.challonge_match_id)

    monkeypatch.setattr(ChallongeNotificationMixin, '_notify_matchup_ready', staticmethod(record))
    return calls


async def _booking(tournament, *users) -> Match:
    match = await Match.create(tournament=tournament)
    for user in users:
        await MatchPlayers.create(match=match, user=user)
    return match


async def _challonge_setup():
    actor = await User.create(discord_id=1, username='admin')
    alice = await User.create(discord_id=2, username='alice', challonge_user_id='1001')
    bob = await User.create(discord_id=3, username='bob', challonge_user_id='1002')
    tournament = await Tournament.create(name='T', challonge_tournament_id='T1')
    api = MagicMock()
    api.get_tournament_full = AsyncMock(return_value=_bracket(['8001']))
    service = ChallongeService()
    service._api_client = lambda: api
    await service.sync_bracket(tournament.id, actor, force=True)
    return service, api, actor, tournament, alice, bob


async def _reset_and_restart(service, api, actor, tournament, new_ids):
    api.get_tournament_full = AsyncMock(return_value=_bracket([], state='pending'))
    await service.sync_bracket(tournament.id, actor, force=True)
    api.get_tournament_full = AsyncMock(return_value=_bracket(new_ids))
    await service.sync_bracket(tournament.id, actor, force=True)


class TestChallongeReset:
    async def test_booking_follows_its_matchup_through_a_reset(self, db, announced):
        service, api, actor, tournament, alice, bob = await _challonge_setup()
        booked = await _booking(tournament, alice, bob)
        old = await ChallongeMatch.get(challonge_match_id='8001')
        old.match = booked
        await old.save(update_fields=['match_id'])

        await _reset_and_restart(service, api, actor, tournament, ['8101'])

        assert (await ChallongeMatch.get(challonge_match_id='8101')).match_id == booked.id
        assert announced == ['8001']
        assert await service.list_unscheduled_matches_for_user(alice) == []
        log = await AuditLog.get(action='challonge.match_linked')
        assert json.loads(log.details)['automatic'] is True

    async def test_a_match_made_by_hand_links_when_its_matchup_opens(self, db, announced):
        actor = await User.create(discord_id=1, username='admin')
        alice = await User.create(discord_id=2, username='alice', challonge_user_id='1001')
        bob = await User.create(discord_id=3, username='bob', challonge_user_id='1002')
        tournament = await Tournament.create(name='T', challonge_tournament_id='T1')
        booked = await _booking(tournament, alice, bob)
        api = MagicMock()
        api.get_tournament_full = AsyncMock(return_value=_bracket(['8001']))
        service = ChallongeService()
        service._api_client = lambda: api
        await service.sync_bracket(tournament.id, actor, force=True)
        assert (await ChallongeMatch.get(challonge_match_id='8001')).match_id == booked.id
        assert announced == []

    async def test_two_candidates_are_left_for_staff(self, db, announced):
        service, api, actor, tournament, alice, bob = await _challonge_setup()
        await _booking(tournament, alice, bob)
        await _booking(tournament, alice, bob)

        await _reset_and_restart(service, api, actor, tournament, ['8101'])

        assert (await ChallongeMatch.get(challonge_match_id='8101')).match_id is None
        assert announced == ['8001', '8101']

    async def test_a_confirmed_match_is_not_relinked(self, db, announced):
        from datetime import datetime, timezone
        service, api, actor, tournament, alice, bob = await _challonge_setup()
        done = await _booking(tournament, alice, bob)
        done.confirmed_at = datetime.now(timezone.utc)
        await done.save(update_fields=['confirmed_at'])

        await _reset_and_restart(service, api, actor, tournament, ['8101'])

        assert (await ChallongeMatch.get(challonge_match_id='8101')).match_id is None

    async def test_a_changed_pairing_leaves_the_booking_on_the_schedule(self, db, announced):
        service, api, actor, tournament, alice, bob = await _challonge_setup()
        carol = await User.create(discord_id=4, username='carol')
        other = await _booking(tournament, alice, carol)

        await _reset_and_restart(service, api, actor, tournament, ['8101'])

        assert (await ChallongeMatch.get(challonge_match_id='8101')).match_id is None
        assert await Match.exists(id=other.id)


class TestStartggReset:
    async def test_booking_follows_its_set_through_a_phase_reset(self, db, client, sent):  # noqa: F811
        one, two = await _players()
        tournament, actor = await _linked(client)
        row = await StartggSet.get(tournament=tournament, set_id='8101')
        booked = await _booking(tournament, one, two)
        row.match = booked
        await row.save(update_fields=['match_id'])
        assert (await StartggSet.get(tournament=tournament, set_id='8101')).match_id == booked.id

        pool = client.full['pools']['7203']
        for row in pool['sets']:
            row['set_id'] = str(int(row['set_id']) + 1000)
            for slot in row['slots']:
                if slot.get('prereq_id'):
                    slot['prereq_id'] = str(int(slot['prereq_id']) + 1000)
        sent.clear()
        await StartggService().sync_event(tournament.id, actor, force=True)

        assert not await StartggSet.exists(tournament=tournament, set_id='8101')
        assert (await StartggSet.get(tournament=tournament, set_id='9101')).match_id == booked.id
        assert '9101' not in sent
