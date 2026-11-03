"""Tests for RoomTokenService — issuing, revoking, and the resolve contract."""

import pytest

from application.services.room_token_service import (
    TOKEN_PREFIX,
    RoomTokenService,
    hash_token,
    room_seeds_path,
)
from application.tenant_context import tenant_scope
from models import Role, RoomToken, User, UserRole
from tests.conftest import DEFAULT_TEST_TENANT_ID
from tests.factories import make_user


async def _staff(discord_id: int = 501) -> User:
    user = await make_user(discord_id=discord_id, username=f'staff-{discord_id}')
    await UserRole.create(user=user, role=Role.STAFF, tenant_id=DEFAULT_TEST_TENANT_ID)
    return user


async def _member(discord_id: int = 502) -> User:
    return await make_user(discord_id=discord_id, username=f'member-{discord_id}')


class TestIssue:
    async def test_returns_a_prefixed_token_stored_only_as_a_hash(self, db):
        with tenant_scope(DEFAULT_TEST_TENANT_ID):
            token, raw = await RoomTokenService().issue(await _staff(), 'Room A desk PC')

        assert raw.startswith(TOKEN_PREFIX)
        assert token.token_hash == hash_token(raw)
        assert raw not in (token.label, token.token_hash)

    async def test_label_is_required(self, db):
        with tenant_scope(DEFAULT_TEST_TENANT_ID):
            with pytest.raises(ValueError):
                await RoomTokenService().issue(await _staff(), '   ')

    async def test_only_staff_may_issue(self, db):
        with tenant_scope(DEFAULT_TEST_TENANT_ID):
            with pytest.raises(PermissionError):
                await RoomTokenService().issue(await _member(), 'Room A desk PC')

    async def test_two_tokens_never_collide(self, db):
        with tenant_scope(DEFAULT_TEST_TENANT_ID):
            staff = await _staff()
            service = RoomTokenService()
            _, first = await service.issue(staff, 'Room A')
            _, second = await service.issue(staff, 'Room B')
        assert first != second


class TestResolve:
    async def test_a_live_token_resolves_and_records_its_use(self, db):
        with tenant_scope(DEFAULT_TEST_TENANT_ID):
            _, raw = await RoomTokenService().issue(await _staff(), 'Room A desk PC')
            resolved = await RoomTokenService().resolve(raw)

        assert resolved is not None
        assert resolved.last_used_at is not None

    async def test_a_revoked_token_opens_nothing(self, db):
        with tenant_scope(DEFAULT_TEST_TENANT_ID):
            staff = await _staff()
            service = RoomTokenService()
            token, raw = await service.issue(staff, 'Room A desk PC')
            await service.revoke(staff, token.id)

            assert await service.resolve(raw) is None
            assert (await RoomToken.get(id=token.id)).revoked_at is not None

    async def test_revoking_twice_is_not_found(self, db):
        with tenant_scope(DEFAULT_TEST_TENANT_ID):
            staff = await _staff()
            service = RoomTokenService()
            token, _ = await service.issue(staff, 'Room A desk PC')
            await service.revoke(staff, token.id)
            with pytest.raises(ValueError):
                await service.revoke(staff, token.id)

    async def test_only_staff_may_revoke(self, db):
        with tenant_scope(DEFAULT_TEST_TENANT_ID):
            token, _ = await RoomTokenService().issue(await _staff(), 'Room A desk PC')
            with pytest.raises(PermissionError):
                await RoomTokenService().revoke(await _member(), token.id)


class TestListing:
    async def test_revoked_tokens_stay_listed_below_the_live_ones(self, db):
        with tenant_scope(DEFAULT_TEST_TENANT_ID):
            staff = await _staff()
            service = RoomTokenService()
            retired, _ = await service.issue(staff, 'Old laptop')
            await service.issue(staff, 'Room A desk PC')
            await service.revoke(staff, retired.id)

            labels = [t.label for t in await service.list_tokens(staff)]
        assert labels == ['Room A desk PC', 'Old laptop']

    async def test_only_staff_may_list(self, db):
        with tenant_scope(DEFAULT_TEST_TENANT_ID):
            with pytest.raises(PermissionError):
                await RoomTokenService().list_tokens(await _member())


def test_the_path_carries_the_raw_token():
    assert room_seeds_path('wizzrobe_room_abc') == '/room/wizzrobe_room_abc/seeds'
