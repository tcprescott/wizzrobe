"""Unit tests for ChallongeService.

The HTTP client is replaced with a fake (the service builds clients via
``_api_client`` / ``_oauth_client``, which we override per-test). The bracket
mirror, participant→user resolution, scheduling, and result-push logic run
against a real in-memory DB so the mapping is exercised end to end.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from application.services.challonge_service import ChallongeService
from models import (
    ChallongeConnection,
    ChallongeMatch,
    ChallongeMatchState,
    ChallongeParticipant,
    Match,
    MatchPlayers,
    Tournament,
    User,
)

pytestmark = pytest.mark.usefixtures("bypass_auth")
def make_service(api=None, oauth=None) -> ChallongeService:
    service = ChallongeService()
    if api is not None:
        service._api_client = lambda: api
    if oauth is not None:
        service._oauth_client = lambda: oauth
    return service


async def make_user(discord_id: int, username: str = 'u', challonge_user_id=None) -> User:
    return await User.create(
        discord_id=discord_id, username=username, challonge_user_id=challonge_user_id,
    )


# ----------------------------------------------------------------------
# parse_tournament_identifier (pure)
# ----------------------------------------------------------------------
def test_default_service_scopes_include_communities_manage(monkeypatch):
    from application.services.challonge_service import _service_scopes
    monkeypatch.delenv('CHALLONGE_SCOPES', raising=False)
    assert 'communities:manage' in _service_scopes().split()
    # change_state (stage control) needs it.
    assert 'tournaments:write' in _service_scopes().split()
    # Players signing themselves up for a bracket needs it.
    assert 'participants:write' in _service_scopes().split()


class TestServiceAuthorizeUrl:
    def test_no_community(self, monkeypatch):
        monkeypatch.setenv('CHALLONGE_CLIENT_ID', 'cid')
        url = ChallongeService.service_authorize_url('st')
        assert 'community_id' not in url
        assert 'communities%3Amanage' in url

    def test_community_is_requested(self, monkeypatch):
        monkeypatch.setenv('CHALLONGE_CLIENT_ID', 'cid')
        url = ChallongeService.service_authorize_url('st', community='SpeedGaming')
        assert url.endswith('&community_id=speedgaming')

    @pytest.mark.parametrize('raw, expected', [
        ('speedgaming', 'speedgaming'),
        (' SpeedGaming ', 'speedgaming'),
        ('speedgaming.challonge.com', 'speedgaming'),
        ('https://speedgaming.challonge.com/testing', 'speedgaming'),
        ('https://challonge.com/testing', None),
        ('', None),
        (None, None),
        ('bad slug&x=1', None),
    ])
    def test_normalize_community(self, raw, expected):
        assert ChallongeService.normalize_community(raw) == expected


class TestParseIdentifier:
    def test_plain_slug(self):
        assert ChallongeService.parse_tournament_identifier('abc123') == ('abc123', None)

    def test_numeric_id(self):
        assert ChallongeService.parse_tournament_identifier('12345') == ('12345', None)

    def test_root_url(self):
        assert ChallongeService.parse_tournament_identifier('https://challonge.com/abc123') == ('abc123', None)

    def test_subdomain_url(self):
        assert (
            ChallongeService.parse_tournament_identifier('https://speedgaming.challonge.com/testing')
            == ('testing', 'speedgaming')
        )

    def test_www_is_not_a_community(self):
        assert (
            ChallongeService.parse_tournament_identifier('https://www.challonge.com/abc123')
            == ('abc123', None)
        )

    def test_blank_raises(self):
        with pytest.raises(ValueError):
            ChallongeService.parse_tournament_identifier('   ')


# ----------------------------------------------------------------------
# get_valid_access_token
# ----------------------------------------------------------------------
class TestGetValidAccessToken:
    async def test_no_connection_raises(self, db):
        with pytest.raises(ValueError, match='not connected'):
            await make_service().get_valid_access_token()

    async def test_returns_live_token(self, db):
        await ChallongeConnection.create(
            access_token='live', refresh_token='r',
            token_expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
        assert await make_service().get_valid_access_token() == 'live'

    async def test_refreshes_when_expiring(self, db):
        await ChallongeConnection.create(
            access_token='old', refresh_token='r1',
            token_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        oauth = MagicMock()
        oauth.refresh = AsyncMock(return_value={
            'access_token': 'new', 'refresh_token': 'r2', 'expires_in': 604800,
        })
        token = await make_service(oauth=oauth).get_valid_access_token()
        assert token == 'new'
        oauth.refresh.assert_awaited_once_with('r1')
        connection = await ChallongeConnection.all().first()
        assert connection.access_token == 'new'
        assert connection.refresh_token == 'r2'

    async def test_force_refresh_without_refresh_token_raises(self, db):
        await ChallongeConnection.create(access_token='only', refresh_token=None)
        with pytest.raises(ValueError, match='reconnect'):
            await make_service().get_valid_access_token(force_refresh=True)


# ----------------------------------------------------------------------
# sync_bracket
# ----------------------------------------------------------------------
class TestSyncBracket:
    async def test_mirrors_participants_and_matches(self, db):
        actor = await make_user(1, 'admin')
        linked = await make_user(2, 'alice', challonge_user_id='1001')
        tournament = await Tournament.create(name='T', challonge_tournament_id='T1')

        api = MagicMock()
        api.get_tournament_full = AsyncMock(return_value={
            'tournament': {'id': 'T1', 'name': 'T', 'url': None, 'state': 'underway'},
            'participants': [
                {'participant_id': '9001', 'name': 'Alice', 'challonge_user_id': '1001', 'username': 'alice'},
                {'participant_id': '9002', 'name': 'Bob', 'challonge_user_id': '9999', 'username': 'bob'},
            ],
            'matches': [
                {'match_id': '8001', 'state': 'open', 'round': 1,
                 'player1_participant_id': '9001', 'player2_participant_id': '9002',
                 'winner_participant_id': None},
            ],
        })

        result = await make_service(api=api).sync_bracket(tournament.id, actor)
        assert result == {'participants': 2, 'matches': 1}

        alice_p = await ChallongeParticipant.get(tournament=tournament, challonge_participant_id='9001')
        assert alice_p.user_id == linked.id  # resolved by challonge_user_id
        bob_p = await ChallongeParticipant.get(tournament=tournament, challonge_participant_id='9002')
        assert bob_p.user_id is None  # unlinked

        cmatch = await ChallongeMatch.get(tournament=tournament, challonge_match_id='8001')
        assert cmatch.state == ChallongeMatchState.OPEN
        assert cmatch.participant1_id == alice_p.id
        assert cmatch.participant2_id == bob_p.id

    async def test_an_entrant_added_from_wizzrobe_maps_before_the_invite_is_accepted(self, db):
        actor = await make_user(1, 'admin')
        player = await make_user(2, 'alice', challonge_user_id='1001')
        tournament = await Tournament.create(name='T', challonge_tournament_id='T1')
        api = MagicMock()
        api.get_tournament_full = AsyncMock(return_value={
            'tournament': {'id': 'T1', 'name': 'T', 'url': None, 'state': 'pending'},
            'participants': [
                {'participant_id': '9001', 'name': 'alice', 'challonge_user_id': None,
                 'username': 'alice', 'misc': f'wizzrobe-user:{player.id}'},
                {'participant_id': '9002', 'name': 'Bob', 'challonge_user_id': None,
                 'username': None, 'misc': 'wizzrobe-user:999999'},
            ],
            'matches': [],
        })
        await make_service(api=api).sync_bracket(tournament.id, actor)
        alice_p = await ChallongeParticipant.get(tournament=tournament, challonge_participant_id='9001')
        assert alice_p.user_id == player.id
        assert not alice_p.user_assigned_manually
        bob_p = await ChallongeParticipant.get(tournament=tournament, challonge_participant_id='9002')
        assert bob_p.user_id is None

    async def test_community_tournament_syncs_under_its_community(self, db):
        actor = await make_user(1, 'admin')
        tournament = await Tournament.create(
            name='T', challonge_tournament_id='T1', challonge_community='speedgaming',
        )
        api = MagicMock()
        api.get_tournament_full = AsyncMock(return_value={
            'tournament': {'id': 'T1', 'name': 'T', 'url': None, 'state': 'underway'},
            'participants': [], 'matches': [],
        })
        await make_service(api=api).sync_bracket(tournament.id, actor)
        api.get_tournament_full.assert_awaited_once_with('T1', community='speedgaming')

    async def test_requires_linked_tournament(self, db):
        actor = await make_user(1, 'admin')
        tournament = await Tournament.create(name='T')  # no challonge id
        with pytest.raises(ValueError, match='not linked'):
            await make_service().sync_bracket(tournament.id, actor)

    async def test_throttles_repeat_sync_without_force(self, db):
        actor = await make_user(1, 'admin')
        tournament = await Tournament.create(name='T', challonge_tournament_id='T1')

        api = MagicMock()
        api.get_tournament_full = AsyncMock(return_value={
            'tournament': {'id': 'T1', 'name': 'T', 'url': None, 'state': 'underway'},
            'participants': [], 'matches': [],
        })
        service = make_service(api=api)

        await service.sync_bracket(tournament.id, actor)
        # Second non-forced sync within the throttle window must not hit the API.
        result = await service.sync_bracket(tournament.id, actor)
        assert result.get('skipped') is True
        api.get_tournament_full.assert_awaited_once()

        # Forcing overrides the throttle.
        await service.sync_bracket(tournament.id, actor, force=True)
        assert api.get_tournament_full.await_count == 2


# ----------------------------------------------------------------------
# schedule_challonge_match
# ----------------------------------------------------------------------
class TestScheduleChallongeMatch:
    async def _open_match(self, tournament, p1_user, p2_user):
        p1 = await ChallongeParticipant.create(
            tournament=tournament, challonge_participant_id='9001', name='P1',
            challonge_user_id=p1_user.challonge_user_id, user=p1_user,
        )
        p2 = await ChallongeParticipant.create(
            tournament=tournament, challonge_participant_id='9002', name='P2',
            challonge_user_id=p2_user.challonge_user_id, user=p2_user,
        )
        return await ChallongeMatch.create(
            tournament=tournament, challonge_match_id='8001', round=1,
            state=ChallongeMatchState.OPEN, participant1=p1, participant2=p2,
        )

    async def test_happy_path_links_match(self, db, monkeypatch):
        actor = await make_user(1, 'alice', challonge_user_id='1001')
        opponent = await make_user(2, 'bob', challonge_user_id='1002')
        tournament = await Tournament.create(name='T', challonge_tournament_id='T1')
        cmatch = await self._open_match(tournament, actor, opponent)

        created_match = await Match.create(tournament=tournament)
        fake_ms = MagicMock()
        fake_ms.submit_match_request = AsyncMock(return_value=created_match)
        monkeypatch.setattr('application.services.challonge_service.MatchService', lambda: fake_ms)

        returned = await make_service().schedule_challonge_match(
            challonge_match_pk=cmatch.id, scheduled_date='2026-06-12', scheduled_time='14:30', actor=actor,
        )
        assert returned.id == created_match.id
        fake_ms.submit_match_request.assert_awaited_once()
        _, kwargs = fake_ms.submit_match_request.call_args
        assert sorted(kwargs['player_ids']) == sorted([actor.id, opponent.id])

        await cmatch.refresh_from_db()
        assert cmatch.match_id == created_match.id

    async def test_unlinked_opponent_raises(self, db):
        actor = await make_user(1, 'alice', challonge_user_id='1001')
        tournament = await Tournament.create(name='T', challonge_tournament_id='T1')
        # participant2 has no user
        p1 = await ChallongeParticipant.create(
            tournament=tournament, challonge_participant_id='9001', name='P1',
            challonge_user_id='1001', user=actor,
        )
        p2 = await ChallongeParticipant.create(
            tournament=tournament, challonge_participant_id='9002', name='P2', user=None,
        )
        cmatch = await ChallongeMatch.create(
            tournament=tournament, challonge_match_id='8001', state=ChallongeMatchState.OPEN,
            participant1=p1, participant2=p2,
        )
        with pytest.raises(ValueError, match='link their Challonge account'):
            await make_service().schedule_challonge_match(
                challonge_match_pk=cmatch.id, scheduled_date='2026-06-12', scheduled_time='14:30', actor=actor,
            )

    async def test_already_scheduled_raises(self, db):
        actor = await make_user(1, 'alice', challonge_user_id='1001')
        opponent = await make_user(2, 'bob', challonge_user_id='1002')
        tournament = await Tournament.create(name='T', challonge_tournament_id='T1')
        cmatch = await self._open_match(tournament, actor, opponent)
        existing = await Match.create(tournament=tournament)
        cmatch.match = existing
        await cmatch.save()
        with pytest.raises(ValueError, match='already been scheduled'):
            await make_service().schedule_challonge_match(
                challonge_match_pk=cmatch.id, scheduled_date='2026-06-12', scheduled_time='14:30', actor=actor,
            )


# ----------------------------------------------------------------------
# push_match_result
# ----------------------------------------------------------------------
class TestPushMatchResult:
    async def _setup(self, winner_rank_set=True):
        actor = await make_user(1, 'admin')
        winner = await make_user(2, 'alice', challonge_user_id='1001')
        loser = await make_user(3, 'bob', challonge_user_id='1002')
        tournament = await Tournament.create(name='T', challonge_tournament_id='T1')
        match = await Match.create(tournament=tournament)
        await MatchPlayers.create(match=match, user=winner, finish_rank=1 if winner_rank_set else None)
        await MatchPlayers.create(match=match, user=loser, finish_rank=2 if winner_rank_set else None)
        p1 = await ChallongeParticipant.create(
            tournament=tournament, challonge_participant_id='100', name='Alice',
            challonge_user_id='1001', user=winner,
        )
        p2 = await ChallongeParticipant.create(
            tournament=tournament, challonge_participant_id='200', name='Bob',
            challonge_user_id='1002', user=loser,
        )
        cmatch = await ChallongeMatch.create(
            tournament=tournament, challonge_match_id='8001', state=ChallongeMatchState.OPEN,
            participant1=p1, participant2=p2, match=match,
        )
        return actor, match, cmatch

    async def test_pushes_winner_and_loser(self, db):
        actor, match, _ = await self._setup()
        api = MagicMock()
        api.update_match = AsyncMock()
        # The push triggers a single post-push re-sync to mirror bracket advancement.
        api.get_tournament_full = AsyncMock(return_value={
            'tournament': {'id': 'T1', 'name': 'T', 'url': None, 'state': 'underway'},
            'participants': [], 'matches': [],
        })
        await make_service(api=api).push_match_result(match, actor)
        api.update_match.assert_awaited_once()
        _, kwargs = api.update_match.call_args
        assert kwargs['winner_participant_id'] == '100'
        assert kwargs['loser_participant_id'] == '200'
        assert kwargs['match_id'] == '8001'
        assert kwargs['tournament_id'] == 'T1'
        assert kwargs['community'] is None
        api.get_tournament_full.assert_awaited_once()

    async def test_pushes_under_community(self, db):
        actor, match, cmatch = await self._setup()
        tournament = await Tournament.get(id=cmatch.tournament_id)
        tournament.challonge_community = 'speedgaming'
        await tournament.save()
        api = MagicMock()
        api.update_match = AsyncMock()
        api.get_tournament_full = AsyncMock(return_value={
            'tournament': {'id': 'T1', 'name': 'T', 'url': None, 'state': 'underway'},
            'participants': [], 'matches': [],
        })
        await make_service(api=api).push_match_result(match, actor)
        _, kwargs = api.update_match.call_args
        assert kwargs['community'] == 'speedgaming'

    async def test_no_winner_recorded_raises(self, db):
        actor, match, _ = await self._setup(winner_rank_set=False)
        with pytest.raises(ValueError, match='No winner'):
            await make_service().push_match_result(match, actor)

    async def test_unlinked_match_raises(self, db):
        actor = await make_user(1, 'admin')
        tournament = await Tournament.create(name='T')
        match = await Match.create(tournament=tournament)
        with pytest.raises(ValueError, match='not linked'):
            await make_service().push_match_result(match, actor)

    async def test_push_if_linked_skips_unlinked(self, db):
        actor = await make_user(1, 'admin')
        tournament = await Tournament.create(name='T')
        match = await Match.create(tournament=tournament)
        attempted = await make_service().push_result_if_linked(match, actor)
        assert attempted is False


# ----------------------------------------------------------------------
# Player identity linking
# ----------------------------------------------------------------------
class TestPlayerLink:
    async def test_record_and_unlink(self, db):
        user = await make_user(1, 'alice')
        service = make_service()
        await service.record_player_link(user, '1001', 'alice_c', actor=user)
        await user.refresh_from_db()
        assert user.challonge_user_id == '1001'
        assert user.challonge_username == 'alice_c'
        assert user.challonge_linked_at is not None

        await service.unlink_player(user, actor=user)
        await user.refresh_from_db()
        assert user.challonge_user_id is None
        assert user.challonge_username is None
        assert user.challonge_linked_at is None

    async def test_set_username_updates_linked_account(self, db):
        user = await make_user(1, 'alice', challonge_user_id='1001')
        user.challonge_username = 'old_name'
        await user.save()

        await make_service().set_player_username(user, '  new_name  ', actor=user)
        await user.refresh_from_db()
        assert user.challonge_username == 'new_name'
        # The stable account id is untouched by a username edit.
        assert user.challonge_user_id == '1001'

    async def test_set_username_blank_clears_it(self, db):
        user = await make_user(1, 'alice', challonge_user_id='1001')
        user.challonge_username = 'old_name'
        await user.save()

        await make_service().set_player_username(user, '   ', actor=user)
        await user.refresh_from_db()
        assert user.challonge_username is None

    async def test_set_username_requires_linked_account(self, db):
        user = await make_user(1, 'alice')  # no challonge_user_id
        with pytest.raises(ValueError):
            await make_service().set_player_username(user, 'whatever', actor=user)

    async def test_link_player_manually(self, db):
        admin = await make_user(1, 'admin')
        user = await make_user(2, 'bob')  # not linked
        await make_service().link_player_manually(user, ' 2002 ', ' bob_c ', actor=admin)
        await user.refresh_from_db()
        assert user.challonge_user_id == '2002'
        assert user.challonge_username == 'bob_c'
        assert user.challonge_linked_at is not None

    async def test_link_player_manually_requires_id(self, db):
        admin = await make_user(1, 'admin')
        user = await make_user(2, 'bob')
        with pytest.raises(ValueError):
            await make_service().link_player_manually(user, '   ', None, actor=admin)

    async def test_link_player_manually_rejects_duplicate_id(self, db):
        admin = await make_user(1, 'admin')
        await make_user(2, 'alice', challonge_user_id='3003')
        user = await make_user(3, 'bob')
        with pytest.raises(ValueError):
            await make_service().link_player_manually(user, '3003', None, actor=admin)
        await user.refresh_from_db()
        assert user.challonge_user_id is None

    async def test_challonge_oauth_link_rejects_an_id_another_user_holds(self, db):
        # The OAuth path was the only one of the three link paths with no
        # pre-check against a unique=True column: it hit the index, raised
        # IntegrityError, and the callback answered "Please try again." — advice
        # that fails identically every time it is followed.
        await make_user(2, 'alice', challonge_user_id='4004')
        user = await make_user(3, 'bob')
        with pytest.raises(ValueError, match='already linked to alice'):
            await make_service().record_player_link(user, '4004', 'bob_c', actor=user)
        await user.refresh_from_db()
        assert user.challonge_user_id is None

    async def test_challonge_oauth_link_rejects_a_blank_id(self, db):
        user = await make_user(2, 'bob')
        with pytest.raises(ValueError, match='required'):
            await make_service().record_player_link(user, '   ', 'bob_c', actor=user)

    async def test_challonge_oauth_link_normalizes_like_the_staff_override(self, db):
        user = await make_user(2, 'bob')
        await make_service().record_player_link(user, ' 5005 ', '  bob_c  ', actor=user)
        await user.refresh_from_db()
        assert user.challonge_user_id == '5005'
        assert user.challonge_username == 'bob_c'

    async def test_relinking_the_same_id_to_the_same_user_is_allowed(self, db):
        # The pre-check excludes the user themselves, so re-linking (a rebind to
        # the identity already held) is not a collision with oneself.
        user = await make_user(2, 'bob', challonge_user_id='6006')
        await make_service().record_player_link(user, '6006', 'bob_renamed', actor=user)
        await user.refresh_from_db()
        assert user.challonge_username == 'bob_renamed'

    async def test_only_the_staff_override_audits_manual(self, db):
        # Both paths now share one private helper; `manual` is the only thing that
        # distinguishes their audit rows, so it has to survive the extraction.
        import json

        from application.services.audit_service import AuditActions
        from models import AuditLog
        admin = await make_user(1, 'admin')
        oauth_user = await make_user(2, 'bob')
        manual_user = await make_user(3, 'carol')
        service = make_service()
        await service.record_player_link(oauth_user, '7007', None, actor=oauth_user)
        await service.link_player_manually(manual_user, '7008', None, actor=admin)

        by_user = {}
        for row in await AuditLog.filter(action=AuditActions.CHALLONGE_PLAYER_LINKED):
            details = row.details if isinstance(row.details, dict) else json.loads(row.details)
            by_user[details['user_id']] = details
        assert 'manual' not in by_user[oauth_user.id]
        assert by_user[manual_user.id]['manual'] is True


class TestParticipantTournamentIds:
    async def test_returns_only_tournaments_user_participates_in(self, db):
        user = await make_user(1, 'alice', challonge_user_id='1001')
        other = await make_user(2, 'bob', challonge_user_id='1002')
        in_t = await Tournament.create(name='In', challonge_tournament_id='T1')
        other_t = await Tournament.create(name='Other', challonge_tournament_id='T2')
        await ChallongeParticipant.create(
            tournament=in_t, challonge_participant_id='9001', name='P1',
            challonge_user_id=user.challonge_user_id, user=user,
        )
        await ChallongeParticipant.create(
            tournament=other_t, challonge_participant_id='9002', name='P2',
            challonge_user_id=other.challonge_user_id, user=other,
        )

        ids = await make_service().participant_tournament_ids(user)
        assert ids == {in_t.id}

    async def test_empty_when_not_a_participant(self, db):
        user = await make_user(1, 'alice', challonge_user_id='1001')
        await Tournament.create(name='In', challonge_tournament_id='T1')
        assert await make_service().participant_tournament_ids(user) == set()


# ----------------------------------------------------------------------
# ChallongeClient path building
# ----------------------------------------------------------------------
class TestClientPaths:
    def _client(self):
        from application.utils.clients.challonge_client import ChallongeClient
        client = ChallongeClient('id', 'secret', token_provider=AsyncMock(return_value='tok'))
        client._authed_request = AsyncMock(return_value={'data': {'id': '1'}})
        return client

    async def test_root_tournament_path(self):
        client = self._client()
        await client.get_tournament_full('testing')
        method, path = client._authed_request.call_args.args
        assert method == 'GET'
        assert path == '/tournaments/testing.json?include_participants=1&include_matches=1'

    async def test_community_tournament_path(self):
        client = self._client()
        await client.get_tournament_full('testing', community='speedgaming')
        _, path = client._authed_request.call_args.args
        assert path == (
            '/communities/speedgaming/tournaments/testing.json'
            '?include_participants=1&include_matches=1'
        )

    async def test_community_update_match_path(self):
        client = self._client()
        await client.update_match('T1', '8001', '100', '200', community='speedgaming')
        _, path = client._authed_request.call_args.args
        assert path == '/communities/speedgaming/tournaments/T1/matches/8001.json'


# ----------------------------------------------------------------------
# ChallongeClient participant identity (shapes captured from the live v2.1 API)
# ----------------------------------------------------------------------
def _included_participant(pid, username, user_rel):
    return {
        'id': pid, 'type': 'participant',
        'attributes': {'name': username or 'Guest', 'username': username, 'seed': 1},
        'relationships': {'invitation': {}, 'user': user_rel},
    }


class TestClientParticipantIdentity:
    def _client(self, responses):
        from application.utils.clients.challonge_client import ChallongeClient
        client = ChallongeClient('id', 'secret', token_provider=AsyncMock(return_value='tok'))
        client._authed_request = AsyncMock(side_effect=responses)
        return client

    async def test_participants_endpoint_reads_the_user_relationship(self):
        client = self._client([{'data': [
            _included_participant('305857241', 'The_Synack', {'data': {'id': '60661', 'type': 'user'}}),
            _included_participant('305857244', None, {'data': None}),
        ]}])
        rows = await client.list_participants('18567901', community='speedgaming')
        assert [r['challonge_user_id'] for r in rows] == ['60661', None]

    async def test_full_fetch_backfills_user_ids_the_embed_leaves_empty(self):
        embedded = {'data': {'id': '18567901', 'attributes': {}}, 'included': [
            _included_participant('305857241', 'The_Synack', {}),
            _included_participant('305857244', None, {}),
        ]}
        listed = {'data': [
            _included_participant('305857241', 'The_Synack', {'data': {'id': '60661', 'type': 'user'}}),
            _included_participant('305857244', None, {'data': None}),
        ]}
        client = self._client([embedded, listed])
        full = await client.get_tournament_full('18567901', community='speedgaming')
        assert [p['challonge_user_id'] for p in full['participants']] == ['60661', None]
        _, path = client._authed_request.call_args.args
        assert path == '/communities/speedgaming/tournaments/18567901/participants.json?per_page=100'

    async def test_full_fetch_skips_the_extra_call_with_no_account_entrants(self):
        embedded = {'data': {'id': '1', 'attributes': {}}, 'included': [
            _included_participant('1', None, {}),
        ]}
        client = self._client([embedded])
        await client.get_tournament_full('1')
        assert client._authed_request.await_count == 1


# ----------------------------------------------------------------------
# Manual participant -> user assignment
# ----------------------------------------------------------------------
class TestManualParticipantAssignment:
    async def _bracket(self):
        from models import TenantMembership
        from tests.conftest import DEFAULT_TEST_TENANT_ID
        actor = await make_user(1, 'staff')
        verified = await make_user(2, 'synack', challonge_user_id='60661')
        guest = await make_user(3, 'jamevil')
        for u in (verified, guest):
            await TenantMembership.create(user=u, tenant_id=DEFAULT_TEST_TENANT_ID)
        tournament = await Tournament.create(name='T', challonge_tournament_id='T1')
        api = MagicMock()
        api.get_tournament_full = AsyncMock(return_value={
            'tournament': {'id': 'T1', 'name': 'T', 'url': None, 'state': 'underway'},
            'participants': [
                {'participant_id': '241', 'name': 'The_Synack', 'challonge_user_id': '60661', 'username': 'The_Synack'},
                {'participant_id': '244', 'name': 'Jamevil', 'challonge_user_id': None, 'username': None},
            ],
            'matches': [],
        })
        service = make_service(api=api)
        await service.sync_bracket(tournament.id, actor)
        return service, actor, verified, guest, tournament

    async def _participant(self, tournament, cid):
        return await ChallongeParticipant.get(tournament=tournament, challonge_participant_id=cid)

    async def test_assignment_survives_resync(self, db):
        service, actor, _, guest, tournament = await self._bracket()
        jam = await self._participant(tournament, '244')
        await service.assign_participant_user(jam.id, guest.id, actor)

        await service.sync_bracket(tournament.id, actor, force=True)
        jam = await self._participant(tournament, '244')
        assert jam.user_id == guest.id and jam.user_assigned_manually

    async def test_manual_beats_a_verified_account_and_is_flagged(self, db):
        service, actor, verified, guest, tournament = await self._bracket()
        synack = await self._participant(tournament, '241')
        await service.assign_participant_user(synack.id, guest.id, actor)
        await service.sync_bracket(tournament.id, actor, force=True)

        rows = {r['name']: r for r in await service.list_bracket_participants(tournament.id, actor)}
        assert rows['The_Synack']['status'] == 'manual'
        assert rows['The_Synack']['user'].id == guest.id
        assert rows['The_Synack']['verified_user'].id == verified.id
        assert rows['Jamevil']['status'] == 'unlinked'

    async def test_clear_falls_back_to_the_verified_account(self, db):
        service, actor, verified, guest, tournament = await self._bracket()
        synack = await self._participant(tournament, '241')
        await service.assign_participant_user(synack.id, guest.id, actor)
        await service.clear_participant_user(synack.id, actor)
        synack = await self._participant(tournament, '241')
        assert synack.user_id == verified.id and not synack.user_assigned_manually

    async def test_one_user_per_bracket(self, db):
        service, actor, verified, _, tournament = await self._bracket()
        jam = await self._participant(tournament, '244')
        with pytest.raises(ValueError, match='already mapped to The_Synack'):
            await service.assign_participant_user(jam.id, verified.id, actor)

    async def test_assignee_must_be_a_member(self, db):
        service, actor, _, _, tournament = await self._bracket()
        outsider = await make_user(9, 'outsider')
        jam = await self._participant(tournament, '244')
        with pytest.raises(ValueError, match="isn't a member"):
            await service.assign_participant_user(jam.id, outsider.id, actor)

    async def test_assignment_is_audited(self, db):
        from models import AuditLog
        service, actor, _, guest, tournament = await self._bracket()
        jam = await self._participant(tournament, '244')
        await service.assign_participant_user(jam.id, guest.id, actor)
        await service.clear_participant_user(jam.id, actor)
        actions = await AuditLog.filter(action__startswith='challonge.participant').values_list('action', flat=True)
        assert sorted(actions) == ['challonge.participant_assigned', 'challonge.participant_unassigned']

    async def test_requires_tournament_edit_rights(self, db, monkeypatch):
        from application.services import auth_service
        service, actor, _, guest, tournament = await self._bracket()
        jam = await self._participant(tournament, '244')

        async def deny(*_a, **_k):
            return False

        async def ensure(ok, msg):
            if not ok:
                raise PermissionError(msg)

        monkeypatch.setattr(auth_service.AuthService, 'can_edit_tournament', deny)
        monkeypatch.setattr(auth_service.AuthService, 'ensure', ensure)
        with pytest.raises(PermissionError):
            await service.assign_participant_user(jam.id, guest.id, actor)


class TestFinalStageMatchShape:
    def test_players_come_from_points_when_relationships_are_absent(self):
        # Captured from a live two-stage tournament right after the final stage
        # started: no player1/player2 relationships at all.
        from application.utils.clients.challonge_client import _normalize_match
        resource = {
            'id': '474720405', 'type': 'match',
            'attributes': {
                'state': 'open', 'round': 1, 'identifier': 'A', 'winner_id': None,
                'points_by_participant': [
                    {'participant_id': 305857244, 'scores': []},
                    {'participant_id': 305857246, 'scores': []},
                ],
            },
            'relationships': {'attachments': {'links': {}}},
        }
        m = _normalize_match(resource)
        assert (m['player1_participant_id'], m['player2_participant_id']) == ('305857244', '305857246')

    def test_relationships_still_win_when_present(self):
        from application.utils.clients.challonge_client import _normalize_match
        resource = {
            'id': '1', 'attributes': {'state': 'open', 'round': 1, 'points_by_participant': [
                {'participant_id': 9, 'scores': []}, {'participant_id': 8, 'scores': []}]},
            'relationships': {'player1': {'data': {'id': '1'}}, 'player2': {'data': {'id': '2'}}},
        }
        m = _normalize_match(resource)
        assert (m['player1_participant_id'], m['player2_participant_id']) == ('1', '2')

    def test_pending_match_with_no_players_stays_empty(self):
        from application.utils.clients.challonge_client import _normalize_match
        m = _normalize_match({'id': '2', 'attributes': {'state': 'pending', 'round': 2}, 'relationships': {}})
        assert (m['player1_participant_id'], m['player2_participant_id']) == (None, None)


class TestClientPagination:
    async def test_follows_next_links_across_pages(self):
        from application.utils.clients.challonge_client import ChallongeClient
        client = ChallongeClient('id', 'secret', token_provider=AsyncMock(return_value='tok'))
        base = 'https://api.challonge.com/v2.1'
        client._authed_request = AsyncMock(side_effect=[
            {'data': [{'id': str(i), 'attributes': {}} for i in range(100)],
             'links': {'next': f'{base}/tournaments/T/participants.json?page=2&per_page=100'}},
            {'data': [{'id': '100', 'attributes': {}}], 'links': {'next': None}},
        ])
        rows = await client.list_participants('T')
        assert len(rows) == 101
        paths = [c.args[1] for c in client._authed_request.await_args_list]
        assert paths == [
            '/tournaments/T/participants.json?per_page=100',
            '/tournaments/T/participants.json?page=2&per_page=100',
        ]
