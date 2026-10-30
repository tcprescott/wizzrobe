"""Cross-tenant isolation for tournament-room kiosk tokens.

A ``RoomToken`` is resolved from a URL under ``/t/<slug>/room/…``, so the
community is already bound when the lookup runs and the lookup is scoped to it.
That is what makes the wrong-community case a plain "not found" rather than a
check someone has to remember to write — this pins it, along with the three
other ways a token fails to open anything.
"""

import pytest

from application.services.room_token_service import (
    TOKEN_PREFIX,
    RoomTokenService,
    hash_token,
)
from application.tenant_context import tenant_scope
from models import Role, RoomToken, Tenant, User, UserRole
from tests.factories import make_user

RAW_A = TOKEN_PREFIX + 'tenant-a-room-pc'
RAW_B = TOKEN_PREFIX + 'tenant-b-room-pc'


async def _staff(tenant: Tenant, discord_id: int) -> User:
    user = await make_user(discord_id=discord_id, username=f'staff-{discord_id}')
    await UserRole.create(user=user, role=Role.STAFF, tenant=tenant)
    return user


async def test_a_token_does_not_resolve_in_another_community(two_tenants):
    a, b = two_tenants
    with tenant_scope(a.id):
        await RoomToken.create(label='Room A desk', token_hash=hash_token(RAW_A))
    with tenant_scope(b.id):
        await RoomToken.create(label='Room B desk', token_hash=hash_token(RAW_B))

    service = RoomTokenService()
    with tenant_scope(a.id):
        assert (await service.resolve(RAW_A)) is not None
        assert (await service.resolve(RAW_B)) is None
    with tenant_scope(b.id):
        assert (await service.resolve(RAW_B)) is not None
        assert (await service.resolve(RAW_A)) is None


async def test_the_token_list_is_scoped(two_tenants):
    a, b = two_tenants
    with tenant_scope(a.id):
        staff_a = await _staff(a, 9101)
        await RoomToken.create(label='Room A desk', token_hash=hash_token(RAW_A))
    with tenant_scope(b.id):
        await RoomToken.create(label='Room B desk', token_hash=hash_token(RAW_B))

    with tenant_scope(a.id):
        labels = [t.label for t in await RoomTokenService().list_tokens(staff_a)]
    assert labels == ['Room A desk']


async def test_a_token_cannot_be_revoked_from_another_community(two_tenants):
    a, b = two_tenants
    with tenant_scope(a.id):
        token_a = await RoomToken.create(label='Room A desk', token_hash=hash_token(RAW_A))
    with tenant_scope(b.id):
        staff_b = await _staff(b, 9102)
        with pytest.raises(ValueError):
            await RoomTokenService().revoke(staff_b, token_a.id)

    with tenant_scope(a.id):
        assert (await RoomTokenService().resolve(RAW_A)) is not None


async def test_revoked_and_unknown_and_malformed_all_resolve_to_nothing(two_tenants):
    a, _ = two_tenants
    with tenant_scope(a.id):
        staff = await _staff(a, 9103)
        token = await RoomToken.create(label='Room A desk', token_hash=hash_token(RAW_A))
        service = RoomTokenService()

        assert (await service.resolve(RAW_A)) is not None
        await service.revoke(staff, token.id)
        assert (await service.resolve(RAW_A)) is None

        assert (await service.resolve(TOKEN_PREFIX + 'never-issued')) is None
        assert (await service.resolve('not-a-room-token')) is None
        assert (await service.resolve('')) is None
