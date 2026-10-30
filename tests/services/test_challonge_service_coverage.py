"""Additional coverage for ChallongeService.

Targets the branches the primary ``test_challonge_service`` suite leaves
uncovered: service-account connect/disconnect/status, tournament linking (via
the full-fetch mirror), the ``_map_state`` complete/pending arms, and the
push-result error/edge paths (missing opponent, unmappable participants,
unlinked tournament, post-push re-sync failure). The HTTP client is faked by
overriding ``_api_client`` / ``_oauth_client`` exactly as the primary suite does.
"""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from application.services.audit_service import AuditActions
from application.services.challonge_service import ChallongeService
from application.utils.clients.challonge_client import ChallongeAPIError
from models import (
    AuditLog,
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


class TestSaveServiceConnection:
    async def test_persists_connection_and_resolves_username(self, db):
        actor = await make_user(1, 'admin')
        oauth = MagicMock()
        oauth.get_me = AsyncMock(return_value={'user_id': '42', 'username': 'sgl_bot'})

        payload = {
            'access_token': 'acc', 'refresh_token': 'ref', 'expires_in': 604800,
            'scope': 'me matches:write',
        }
        connection = await make_service(oauth=oauth).save_service_connection(payload, actor)

        assert connection.access_token == 'acc'
        assert connection.refresh_token == 'ref'
        assert connection.challonge_username == 'sgl_bot'
        assert connection.scopes == 'me matches:write'
        assert connection.token_expires_at is not None
        oauth.get_me.assert_awaited_once_with('acc')

        stored = await ChallongeConnection.all().first()
        assert stored.access_token == 'acc'

    async def test_get_me_failure_leaves_username_none(self, db):
        """A failed identity lookup must not abort the connection save."""
        actor = await make_user(1, 'admin')
        oauth = MagicMock()
        oauth.get_me = AsyncMock(side_effect=ChallongeAPIError('boom'))

        payload = {'access_token': 'acc', 'refresh_token': 'ref', 'expires_in': 100}
        connection = await make_service(oauth=oauth).save_service_connection(payload, actor)

        assert connection.challonge_username is None
        assert connection.scopes and 'tournaments:read' in connection.scopes

    async def test_missing_access_token_raises(self, db):
        actor = await make_user(1, 'admin')
        oauth = MagicMock()
        oauth.get_me = AsyncMock()
        with pytest.raises(ChallongeAPIError, match='missing access_token'):
            await make_service(oauth=oauth).save_service_connection({'refresh_token': 'r'}, actor)


class TestDisconnect:
    async def test_clears_existing_connection(self, db):
        actor = await make_user(1, 'admin')
        await ChallongeConnection.create(access_token='live', refresh_token='r')
        await make_service().disconnect(actor)
        assert await ChallongeConnection.all().count() == 0


class TestConnectionStatus:
    async def test_disconnected_status(self, db):
        status = await make_service().get_connection_status()
        assert status['connected'] is False
        assert 'request_quota' not in status
        assert status['request_usage'] == 0
        assert 'configured' in status

    async def test_connected_status_reports_details(self, db):
        expires = datetime.now(timezone.utc)
        await ChallongeConnection.create(
            access_token='live', refresh_token='r', scopes='me',
            challonge_username='sgl_bot', token_expires_at=expires,
        )
        status = await make_service().get_connection_status()
        assert status['connected'] is True
        assert status['challonge_username'] == 'sgl_bot'
        assert status['scopes'] == 'me'
        assert status['token_expires_at'] is not None
        assert 'request_quota' not in status


class TestLinkTournament:
    def _full(self, tid='T1', url='https://challonge.com/spring25', participants=None, matches=None):
        return {
            'tournament': {'id': tid, 'name': 'Spring', 'url': url, 'state': 'underway'},
            'participants': participants if participants is not None else [],
            'matches': matches if matches is not None else [],
        }

    async def test_links_and_mirrors_bracket(self, db):
        actor = await make_user(1, 'admin')
        linked = await make_user(2, 'alice', challonge_user_id='1001')
        tournament = await Tournament.create(name='T')

        api = MagicMock()
        api.get_tournament_full = AsyncMock(return_value=self._full(
            participants=[
                {'participant_id': '9001', 'name': 'Alice', 'challonge_user_id': '1001', 'username': 'alice'},
            ],
            matches=[
                {'match_id': '8001', 'state': 'open', 'round': 1,
                 'player1_participant_id': '9001', 'player2_participant_id': None,
                 'winner_participant_id': None},
            ],
        ))

        returned = await make_service(api=api).link_tournament(
            tournament.id, 'https://myorg.challonge.com/spring25', actor,
        )
        await returned.refresh_from_db()
        assert returned.challonge_tournament_id == 'T1'
        assert returned.challonge_tournament_url == 'https://challonge.com/spring25'
        assert returned.challonge_community == 'myorg'
        assert returned.challonge_last_synced_at is not None
        api.get_tournament_full.assert_awaited_once_with('spring25', community='myorg')

        participant = await ChallongeParticipant.get(tournament=returned, challonge_participant_id='9001')
        assert participant.user_id == linked.id

    async def test_falls_back_to_identifier_when_remote_id_missing(self, db):
        actor = await make_user(1, 'admin')
        tournament = await Tournament.create(name='T')
        api = MagicMock()
        api.get_tournament_full = AsyncMock(return_value=self._full(tid=None, url=None))

        returned = await make_service(api=api).link_tournament(tournament.id, 'plainslug', actor)
        await returned.refresh_from_db()
        assert returned.challonge_tournament_id == 'plainslug'
        assert returned.challonge_tournament_url is None

    async def test_missing_tournament_raises(self, db):
        actor = await make_user(1, 'admin')
        with pytest.raises(ValueError, match='not found'):
            await make_service().link_tournament(999999, 'abc', actor)

    async def test_api_error_wrapped_as_value_error(self, db):
        actor = await make_user(1, 'admin')
        tournament = await Tournament.create(name='T')
        api = MagicMock()
        api.get_tournament_full = AsyncMock(side_effect=ChallongeAPIError('404 not found'))
        with pytest.raises(ValueError, match='Could not find that Challonge tournament'):
            await make_service(api=api).link_tournament(tournament.id, 'abc', actor)

    async def test_not_found_explains_owner_or_community(self, db):
        actor = await make_user(1, 'admin')
        tournament = await Tournament.create(name='T')
        api = MagicMock()
        api.get_tournament_full = AsyncMock(side_effect=ChallongeAPIError(
            'Challonge API error (404): {"errors":{"detail":"Tournament with ID or URL sglmmr26 '
            'does not exist","status":404}}', status=404,
        ))
        with pytest.raises(ValueError) as excinfo:
            await make_service(api=api).link_tournament(
                tournament.id, 'https://challonge.com/sglmmr26', actor,
            )
        message = str(excinfo.value)
        assert "Challonge can't find 'sglmmr26'" in message
        assert "being an admin on someone else's bracket isn't enough" in message
        assert '{' not in message

    async def test_community_403_explains_how_to_reconnect(self, db):
        actor = await make_user(1, 'admin')
        tournament = await Tournament.create(name='T')
        api = MagicMock()
        api.get_tournament_full = AsyncMock(side_effect=ChallongeAPIError(
            'Challonge API error (403): Access resources belonging to your communities', status=403,
        ))
        with pytest.raises(ValueError, match="enter 'speedgaming' as the community and reconnect"):
            await make_service(api=api).link_tournament(
                tournament.id, 'https://speedgaming.challonge.com/testing', actor,
            )


class TestMapState:
    def test_open(self):
        assert ChallongeService._map_state('open') == ChallongeMatchState.OPEN

    def test_complete(self):
        assert ChallongeService._map_state('complete') == ChallongeMatchState.COMPLETE

    def test_pending_default(self):
        assert ChallongeService._map_state('pending') == ChallongeMatchState.PENDING

    def test_unknown_and_none_default_to_pending(self):
        assert ChallongeService._map_state('weird') == ChallongeMatchState.PENDING
        assert ChallongeService._map_state(None) == ChallongeMatchState.PENDING


class TestPushEdgeCases:
    async def _linked_setup(self, tournament_challonge_id='T1', p2_user_matches=True):
        """Build a match linked to a Challonge match with a recorded winner.

        ``p2_user_matches`` controls whether the second Challonge participant is
        wired to the loser (so their participant id resolves) or to a stranger.
        """
        actor = await make_user(1, 'admin')
        winner = await make_user(2, 'alice', challonge_user_id='1001')
        loser = await make_user(3, 'bob', challonge_user_id='1002')
        tournament = await Tournament.create(name='T', challonge_tournament_id=tournament_challonge_id)
        match = await Match.create(tournament=tournament)
        await MatchPlayers.create(match=match, user=winner, finish_rank=1)
        await MatchPlayers.create(match=match, user=loser, finish_rank=2)
        p1 = await ChallongeParticipant.create(
            tournament=tournament, challonge_participant_id='100', name='Alice',
            challonge_user_id='1001', user=winner,
        )
        if p2_user_matches:
            p2_user = loser
        else:
            p2_user = await make_user(4, 'carol', challonge_user_id='9999')
        p2 = await ChallongeParticipant.create(
            tournament=tournament, challonge_participant_id='200', name='P2',
            challonge_user_id=p2_user.challonge_user_id, user=p2_user,
        )
        cmatch = await ChallongeMatch.create(
            tournament=tournament, challonge_match_id='8001', state=ChallongeMatchState.OPEN,
            participant1=p1, participant2=p2, match=match,
        )
        return actor, match, cmatch

    async def test_push_if_linked_returns_false_without_actor(self, db):
        tournament = await Tournament.create(name='T')
        match = await Match.create(tournament=tournament)
        assert await make_service().push_result_if_linked(match, None) is False

    async def test_push_if_linked_pushes_when_linked(self, db):
        actor, match, _ = await self._linked_setup()
        api = MagicMock()
        api.get_match = AsyncMock(return_value={'match_id': '8001', 'state': 'open'})
        api.update_match = AsyncMock()
        api.get_tournament_full = AsyncMock(return_value={
            'tournament': {'id': 'T1', 'name': 'T', 'url': None, 'state': 'underway'},
            'participants': [], 'matches': [],
        })
        attempted = await make_service(api=api).push_result_if_linked(match, actor)
        assert attempted is True
        api.update_match.assert_awaited_once()

    async def test_no_opponent_raises(self, db):
        """A match with only a winner (no loser row) cannot be pushed."""
        actor = await make_user(1, 'admin')
        winner = await make_user(2, 'alice', challonge_user_id='1001')
        tournament = await Tournament.create(name='T', challonge_tournament_id='T1')
        match = await Match.create(tournament=tournament)
        await MatchPlayers.create(match=match, user=winner, finish_rank=1)
        p1 = await ChallongeParticipant.create(
            tournament=tournament, challonge_participant_id='100', name='Alice',
            challonge_user_id='1001', user=winner,
        )
        await ChallongeMatch.create(
            tournament=tournament, challonge_match_id='8001', state=ChallongeMatchState.OPEN,
            participant1=p1, participant2=None, match=match,
        )
        with pytest.raises(ValueError, match='without an opponent'):
            await make_service().push_match_result(match, actor)

    async def test_unmappable_participant_raises(self, db):
        actor, match, _ = await self._linked_setup(p2_user_matches=False)
        with pytest.raises(ValueError, match='map both players'):
            await make_service().push_match_result(match, actor)

    async def test_unlinked_tournament_raises(self, db):
        actor, match, _ = await self._linked_setup(tournament_challonge_id=None)
        with pytest.raises(ValueError, match='no longer linked'):
            await make_service().push_match_result(match, actor)

    async def test_post_push_resync_failure_is_swallowed(self, db):
        """The push succeeds even if the follow-up re-sync raises."""
        actor, match, cmatch = await self._linked_setup()
        api = MagicMock()
        api.get_match = AsyncMock(return_value={'match_id': '8001', 'state': 'open'})
        api.update_match = AsyncMock()
        api.get_tournament_full = AsyncMock(side_effect=ChallongeAPIError('resync boom', status=502))

        await make_service(api=api).push_match_result(match, actor)
        api.update_match.assert_awaited_once()
        api.get_tournament_full.assert_awaited_once()

    async def test_post_push_resync_timeout_retries_then_warns(self, db, monkeypatch, caplog):
        """Sentry SGLMAN-2J: a re-sync timeout after a landed push is retried once, then a warning."""
        from application.services import _challonge_results
        monkeypatch.setattr(_challonge_results, '_RESYNC_RETRY_DELAY', 0)
        actor, match, cmatch = await self._linked_setup()
        api = MagicMock()
        api.get_match = AsyncMock(return_value={'match_id': '8001', 'state': 'open'})
        api.update_match = AsyncMock()
        api.get_tournament_full = AsyncMock(
            side_effect=ChallongeAPIError("Challonge didn't answer (TimeoutError)"),
        )

        with caplog.at_level('WARNING', logger=_challonge_results.__name__):
            assert await make_service(api=api).push_match_result(match, actor) is True
        assert api.get_tournament_full.await_count == 2
        records = [r for r in caplog.records if 're-sync failed' in r.getMessage()]
        assert [r.levelname for r in records] == ['WARNING']

    async def test_post_push_resync_timeout_recovers_on_retry(self, db, monkeypatch, caplog):
        from application.services import _challonge_results
        monkeypatch.setattr(_challonge_results, '_RESYNC_RETRY_DELAY', 0)
        actor, match, cmatch = await self._linked_setup()
        api = MagicMock()
        api.get_match = AsyncMock(return_value={'match_id': '8001', 'state': 'open'})
        api.update_match = AsyncMock()
        sync = AsyncMock(side_effect=[ChallongeAPIError("Challonge didn't answer (TimeoutError)"), None])
        service = make_service(api=api)
        service._sync_tournament = sync

        with caplog.at_level('WARNING', logger=_challonge_results.__name__):
            await service.push_match_result(match, actor)
        assert sync.await_count == 2
        assert not [r for r in caplog.records if 're-sync failed' in r.getMessage()]

    async def test_post_push_resync_http_error_is_not_retried(self, db):
        actor, match, cmatch = await self._linked_setup()
        api = MagicMock()
        api.get_match = AsyncMock(return_value={'match_id': '8001', 'state': 'open'})
        api.update_match = AsyncMock()
        api.get_tournament_full = AsyncMock(side_effect=ChallongeAPIError('bad', status=404))

        await make_service(api=api).push_match_result(match, actor)
        api.get_tournament_full.assert_awaited_once()

    async def test_push_timeout_retries_then_succeeds(self, db, monkeypatch):
        """Sentry SGLMAN-2M: a push that times out is retried once."""
        from application.services import _challonge_results
        monkeypatch.setattr(_challonge_results, '_PUSH_RETRY_DELAY', 0)
        actor, match, _ = await self._linked_setup()
        api = MagicMock()
        api.get_match = AsyncMock(return_value={'match_id': '8001', 'state': 'open'})
        api.update_match = AsyncMock(
            side_effect=[ChallongeAPIError("Challonge didn't answer (TimeoutError)"), None],
        )
        api.get_tournament_full = AsyncMock(return_value={
            'tournament': {'id': 'T1', 'name': 'T', 'url': None, 'state': 'underway'},
            'participants': [], 'matches': [],
        })
        assert await make_service(api=api).push_result_if_linked(match, actor) is True
        assert api.update_match.await_count == 2
        assert await AuditLog.filter(action=AuditActions.CHALLONGE_RESULT_PUSHED).count() == 1
        assert await AuditLog.filter(action=AuditActions.CHALLONGE_RESULT_PUSH_FAILED).count() == 0

    async def test_push_timeout_that_landed_is_not_sent_twice(self, db, monkeypatch):
        from application.services import _challonge_results
        monkeypatch.setattr(_challonge_results, '_PUSH_RETRY_DELAY', 0)
        actor, match, _ = await self._linked_setup()
        api = MagicMock()
        api.get_match = AsyncMock(side_effect=[
            {'match_id': '8001', 'state': 'open'},
            {'match_id': '8001', 'state': 'complete', 'winner_participant_id': '100'},
        ])
        api.update_match = AsyncMock(side_effect=ChallongeAPIError("Challonge didn't answer (TimeoutError)"))
        api.get_tournament_full = AsyncMock(return_value={
            'tournament': {'id': 'T1', 'name': 'T', 'url': None, 'state': 'underway'},
            'participants': [], 'matches': [],
        })
        assert await make_service(api=api).push_result_if_linked(match, actor) is False
        api.update_match.assert_awaited_once()
        assert await AuditLog.filter(action=AuditActions.CHALLONGE_RESULT_PUSH_FAILED).count() == 0

    async def test_push_timeout_twice_warns_and_audits(self, db, monkeypatch, caplog):
        from application.services import _challonge_results
        monkeypatch.setattr(_challonge_results, '_PUSH_RETRY_DELAY', 0)
        actor, match, _ = await self._linked_setup()
        api = MagicMock()
        api.get_match = AsyncMock(return_value={'match_id': '8001', 'state': 'open'})
        api.update_match = AsyncMock(side_effect=ChallongeAPIError("Challonge didn't answer (TimeoutError)"))

        with caplog.at_level('WARNING', logger=_challonge_results.__name__):
            assert await make_service(api=api).push_result_if_linked(match, actor) is False
        assert api.update_match.await_count == 2
        records = [r for r in caplog.records if 'result push failed' in r.getMessage()]
        assert [r.levelname for r in records] == ['WARNING']
        row = await AuditLog.get(action=AuditActions.CHALLONGE_RESULT_PUSH_FAILED)
        details = json.loads(row.details)
        assert details['match_id'] == match.id
        assert details['challonge_match_id'] == '8001'

    @pytest.mark.parametrize('status', [429, 502, 503, 504, 520, 521, 522, 523, 524])
    async def test_push_gateway_error_retries_then_warns(self, db, monkeypatch, caplog, status):
        """Sentry SGLMAN-2N: a Cloudflare 502 (and friends) is retried like a timeout."""
        from application.services import _challonge_results
        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)
        monkeypatch.setattr(_challonge_results.asyncio, 'sleep', fake_sleep)
        actor, match, _ = await self._linked_setup()
        api = MagicMock()
        api.get_match = AsyncMock(return_value={'match_id': '8001', 'state': 'open'})
        api.update_match = AsyncMock(side_effect=ChallongeAPIError(
            f'Challonge API error ({status}): ' + 'x' * 5000, status=status, retry_after=60,
        ))

        with caplog.at_level('WARNING', logger=_challonge_results.__name__):
            assert await make_service(api=api).push_result_if_linked(match, actor) is False
        assert api.update_match.await_count == 2
        assert sleeps == [_challonge_results._PUSH_RETRY_MAX_DELAY]
        records = [r for r in caplog.records if 'result push failed' in r.getMessage()]
        assert [r.levelname for r in records] == ['WARNING']
        row = await AuditLog.get(action=AuditActions.CHALLONGE_RESULT_PUSH_FAILED)
        details = json.loads(row.details)
        assert details['status'] == status
        assert len(details['error']) == _challonge_results._AUDIT_ERROR_CHARS

    async def test_push_gateway_error_recovers_on_retry(self, db, monkeypatch):
        from application.services import _challonge_results
        monkeypatch.setattr(_challonge_results, '_PUSH_RETRY_DELAY', 0)
        actor, match, _ = await self._linked_setup()
        api = MagicMock()
        api.get_match = AsyncMock(return_value={'match_id': '8001', 'state': 'open'})
        api.update_match = AsyncMock(side_effect=[ChallongeAPIError('bad gateway', status=502), None])
        api.get_tournament_full = AsyncMock(return_value={
            'tournament': {'id': 'T1', 'name': 'T', 'url': None, 'state': 'underway'},
            'participants': [], 'matches': [],
        })
        assert await make_service(api=api).push_result_if_linked(match, actor) is True
        assert api.update_match.await_count == 2

    async def test_push_read_cloudflare_error_retries_then_warns(self, db, monkeypatch, caplog):
        """Sentry SGLMAN-2P: a 520 on the read before the update is retried, then warned and audited."""
        from application.services import _challonge_results
        monkeypatch.setattr(_challonge_results, '_PUSH_RETRY_MAX_DELAY', 0)
        actor, match, _ = await self._linked_setup()
        api = MagicMock()
        api.get_match = AsyncMock(side_effect=ChallongeAPIError(
            'Challonge API error (520): unknown origin error', status=520, retry_after=60,
        ))
        api.update_match = AsyncMock()

        with caplog.at_level('WARNING'):
            assert await make_service(api=api).push_result_if_linked(match, actor) is False
        assert api.get_match.await_count == 2
        api.update_match.assert_not_awaited()
        assert not [r for r in caplog.records if r.levelname in ('ERROR', 'CRITICAL')]
        records = [r for r in caplog.records if 'result push failed' in r.getMessage()]
        assert [r.levelname for r in records] == ['WARNING']
        row = await AuditLog.get(action=AuditActions.CHALLONGE_RESULT_PUSH_FAILED)
        assert json.loads(row.details)['status'] == 520

    async def test_push_read_cloudflare_error_recovers_on_retry(self, db, monkeypatch):
        from application.services import _challonge_results
        monkeypatch.setattr(_challonge_results, '_PUSH_RETRY_MAX_DELAY', 0)
        actor, match, _ = await self._linked_setup()
        api = MagicMock()
        api.get_match = AsyncMock(side_effect=[
            ChallongeAPIError('Challonge API error (520)', status=520, retry_after=60),
            {'match_id': '8001', 'state': 'open'},
        ])
        api.update_match = AsyncMock()
        api.get_tournament_full = AsyncMock(return_value={
            'tournament': {'id': 'T1', 'name': 'T', 'url': None, 'state': 'underway'},
            'participants': [], 'matches': [],
        })
        assert await make_service(api=api).push_result_if_linked(match, actor) is True
        api.update_match.assert_awaited_once()
        assert await AuditLog.filter(action=AuditActions.CHALLONGE_RESULT_PUSH_FAILED).count() == 0

    async def test_push_http_error_is_not_retried(self, db):
        actor, match, _ = await self._linked_setup()
        api = MagicMock()
        api.get_match = AsyncMock(return_value={'match_id': '8001', 'state': 'open'})
        api.update_match = AsyncMock(side_effect=ChallongeAPIError('bad', status=422))

        with pytest.raises(ChallongeAPIError):
            await make_service(api=api).push_result_if_linked(match, actor)
        api.update_match.assert_awaited_once()
        assert await AuditLog.filter(action=AuditActions.CHALLONGE_RESULT_PUSH_FAILED).count() == 1


class TestExchangeCode:
    async def test_exchange_player_code_returns_identity(self, db):
        oauth = MagicMock()
        oauth.exchange_code = AsyncMock(return_value={'access_token': 'tok'})
        oauth.get_me = AsyncMock(return_value={'user_id': '55', 'username': 'p55'})
        result = await make_service(oauth=oauth).exchange_player_code('the-code')
        assert result == {'user_id': '55', 'username': 'p55'}
        oauth.get_me.assert_awaited_once_with('tok')

    async def test_exchange_player_code_missing_token_raises(self, db):
        oauth = MagicMock()
        oauth.exchange_code = AsyncMock(return_value={'error': 'bad'})
        with pytest.raises(ChallongeAPIError, match='missing access_token'):
            await make_service(oauth=oauth).exchange_player_code('the-code')

    async def test_exchange_service_code_passes_through_payload(self, db):
        oauth = MagicMock()
        oauth.exchange_code = AsyncMock(return_value={'access_token': 'svc', 'scope': 'me'})
        result = await make_service(oauth=oauth).exchange_service_code('svc-code')
        assert result == {'access_token': 'svc', 'scope': 'me'}


class TestListUnscheduledMatches:
    async def test_returns_open_unscheduled_matches_for_user(self, db):
        user = await make_user(1, 'alice', challonge_user_id='1001')
        other = await make_user(2, 'bob', challonge_user_id='1002')
        tournament = await Tournament.create(name='T', challonge_tournament_id='T1')
        p1 = await ChallongeParticipant.create(
            tournament=tournament, challonge_participant_id='100', name='Alice',
            challonge_user_id='1001', user=user,
        )
        p2 = await ChallongeParticipant.create(
            tournament=tournament, challonge_participant_id='200', name='Bob',
            challonge_user_id='1002', user=other,
        )
        open_match = await ChallongeMatch.create(
            tournament=tournament, challonge_match_id='8001', state=ChallongeMatchState.OPEN,
            participant1=p1, participant2=p2,
        )
        await ChallongeMatch.create(
            tournament=tournament, challonge_match_id='8002', state=ChallongeMatchState.PENDING,
            participant1=p1, participant2=p2,
        )
        matches = await make_service().list_unscheduled_matches_for_user(user)
        assert [m.id for m in matches] == [open_match.id]


@pytest.mark.parametrize('header, expected', [
    ('60', 60.0), ('1.5', 1.5), ('-3', 0.0), (None, None), ('', None),
    ('Wed, 21 Oct 2026 07:28:00 GMT', None),
])
def test_retry_after_header_parsing(header, expected):
    from application.utils.clients.challonge_client import _retry_after_seconds
    assert _retry_after_seconds(header) == expected
