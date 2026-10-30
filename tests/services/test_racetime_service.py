"""Unit tests for RacetimeService.

The account-link write path (record/unlink), the uniqueness guard, and the
OAuth code exchange (via a fake client) run against a real in-memory DB.
"""

import pytest

from application.services.audit_service import AuditActions
from application.services.racetime_service import RacetimeService
from models import AuditLog, User


def make_service(oauth=None) -> RacetimeService:
    service = RacetimeService()
    if oauth is not None:
        service._oauth_client = lambda: oauth
    return service


async def make_user(discord_id: int, username: str = 'u', racetime_user_id=None) -> User:
    return await User.create(
        discord_id=discord_id, username=username, racetime_user_id=racetime_user_id,
    )


class TestPlayerLink:
    async def test_record_and_unlink(self, db):
        user = await make_user(1, 'alice')
        service = make_service()

        await service.record_player_link(user, 'rtAlice01', 'AliceRT', actor=user)
        await user.refresh_from_db()
        assert user.racetime_user_id == 'rtAlice01'
        assert user.racetime_username == 'AliceRT'
        assert user.racetime_linked_at is not None
        assert await AuditLog.filter(action=AuditActions.RACETIME_LINKED).count() == 1

        await service.unlink_player(user, actor=user)
        await user.refresh_from_db()
        assert user.racetime_user_id is None
        assert user.racetime_username is None
        assert user.racetime_linked_at is None
        assert await AuditLog.filter(action=AuditActions.RACETIME_UNLINKED).count() == 1

    async def test_record_strips_and_nulls_blank_username(self, db):
        user = await make_user(1, 'alice')
        await make_service().record_player_link(user, '  rtAlice01  ', '   ', actor=user)
        await user.refresh_from_db()
        assert user.racetime_user_id == 'rtAlice01'
        assert user.racetime_username is None

    async def test_record_requires_id(self, db):
        user = await make_user(1, 'alice')
        with pytest.raises(ValueError):
            await make_service().record_player_link(user, '   ', 'AliceRT', actor=user)

    async def test_record_rejects_duplicate_id(self, db):
        await make_user(1, 'alice', racetime_user_id='rtAlice01')
        bob = await make_user(2, 'bob')
        with pytest.raises(ValueError):
            await make_service().record_player_link(bob, 'rtAlice01', 'BobRT', actor=bob)

    async def test_record_same_user_relink_is_allowed(self, db):
        user = await make_user(1, 'alice', racetime_user_id='rtAlice01')
        await make_service().record_player_link(user, 'rtAlice01', 'AliceRT2', actor=user)
        await user.refresh_from_db()
        assert user.racetime_username == 'AliceRT2'


class TestExchangePlayerCode:
    async def test_returns_identity_and_discards_token(self, db):
        class FakeClient:
            async def exchange_code(self, code, redirect_uri):
                assert code == 'the-code'
                return {'access_token': 'tok'}

            async def get_me(self, access_token):
                assert access_token == 'tok'
                return {'user_id': 'rtAlice01', 'username': 'AliceRT'}

        me = await make_service(oauth=FakeClient()).exchange_player_code('the-code')
        assert me == {'user_id': 'rtAlice01', 'username': 'AliceRT'}

    async def test_missing_access_token_raises(self, db):
        from application.utils.clients.racetime_client import RacetimeAPIError

        class FakeClient:
            async def exchange_code(self, code, redirect_uri):
                return {}

            async def get_me(self, access_token):  # pragma: no cover - never reached
                raise AssertionError('get_me should not be called without a token')

        with pytest.raises(RacetimeAPIError):
            await make_service(oauth=FakeClient()).exchange_player_code('the-code')


class TestConfiguration:
    def test_authorize_url_shape(self):
        url = RacetimeService.player_authorize_url('STATE123')
        assert url.startswith('https://racetime.gg/o/authorize')
        assert 'response_type=code' in url
        assert 'state=STATE123' in url
        assert 'scope=read' in url
