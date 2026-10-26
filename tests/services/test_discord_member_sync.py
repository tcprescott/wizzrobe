"""Unit tests for the live (gateway-event) member syncs: roles and avatar.

`sync_member_roles` runs from the bot's `on_member_update` / `on_member_remove`
handlers. It resolves every tenant linked to the Discord guild (a guild may be
shared by several communities), provisions an account for an unknown member who
holds a mapped role, and re-syncs that user's app roles for each of those tenants.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest

from application.services import tenant_service as tsvc
from application.services.discord import discord_member_events as dsvc
from application.services.discord import discord_role_mapping_service as drms
from application.services.discord.discord_guild_ops import GuildMember
from application.services.user_service import UserService
from models import User
from tests.conftest import DEFAULT_TEST_EVENT_ID


@pytest.fixture
def sync_spy(monkeypatch):
    spy = AsyncMock()
    monkeypatch.setattr(drms.DiscordRoleMappingService, 'sync_user_roles_for_tenant', spy)
    return spy


def _route_guild_to(monkeypatch, tenants):
    monkeypatch.setattr(
        tsvc.TenantService, 'list_tenants_for_guild', AsyncMock(return_value=tenants),
    )


async def test_skips_when_guild_not_linked_to_a_tenant(monkeypatch, sync_spy):
    _route_guild_to(monkeypatch, [])
    await dsvc.sync_member_roles(guild_id=42, discord_user_id=5)
    sync_spy.assert_not_awaited()


async def test_skips_when_user_unknown(monkeypatch, sync_spy):
    _route_guild_to(monkeypatch, [SimpleNamespace(id=1, discord_guild_id=42)])
    monkeypatch.setattr(User, 'get_or_none', AsyncMock(return_value=None))
    await dsvc.sync_member_roles(guild_id=42, discord_user_id=5)
    sync_spy.assert_not_awaited()


async def test_unknown_member_with_a_mapped_role_gets_an_account(monkeypatch, sync_spy):
    tenant = SimpleNamespace(id=1, discord_guild_id=42)
    _route_guild_to(monkeypatch, [tenant])
    monkeypatch.setattr(User, 'get_or_none', AsyncMock(return_value=None))
    created = SimpleNamespace(id=9, discord_id=5)
    provision = AsyncMock(return_value=created)
    monkeypatch.setattr(drms.DiscordRoleMappingService, 'provision_member', provision)
    member = GuildMember(id=5, username='guildie', avatar=None, role_ids=frozenset({111}))

    await dsvc.sync_member_roles(guild_id=42, discord_user_id=5, member=member)

    provision.assert_awaited_once_with([tenant], member)
    sync_spy.assert_awaited_once_with(created, tenant)


async def test_unknown_member_without_a_mapped_role_stays_unknown(monkeypatch, sync_spy):
    _route_guild_to(monkeypatch, [SimpleNamespace(id=1, discord_guild_id=42)])
    monkeypatch.setattr(User, 'get_or_none', AsyncMock(return_value=None))
    monkeypatch.setattr(
        drms.DiscordRoleMappingService, 'provision_member', AsyncMock(return_value=None),
    )
    member = GuildMember(id=5, username='guildie', avatar=None, role_ids=frozenset({222}))

    await dsvc.sync_member_roles(guild_id=42, discord_user_id=5, member=member)

    sync_spy.assert_not_awaited()


async def test_live_update_provisions_and_grants_against_real_db(db, monkeypatch):
    from application.repositories.discord_role_mapping_repository import (
        DiscordRoleMappingRepository,
    )
    from models import Role, Tenant, UserRole

    await tsvc.TenantService.set_discord_guild_id(await Tenant.get(id=1), 42)
    await DiscordRoleMappingRepository.create(42, 111, 'Mods', Role.PROCTOR, event_id=DEFAULT_TEST_EVENT_ID)
    monkeypatch.setattr(drms, 'DiscordService', lambda: SimpleNamespace(
        get_member_role_ids=AsyncMock(return_value=(True, {111})),
    ))
    member = GuildMember(id=5, username='guildie', avatar='abc', role_ids=frozenset({111}))

    await dsvc.sync_member_roles(guild_id=42, discord_user_id=5, member=member)

    user = await User.get(discord_id=5)
    assert user.username == 'guildie'
    assert await UserRole.filter(user=user, role=Role.PROCTOR).exists()


async def test_syncs_for_the_routed_tenant_when_user_known(monkeypatch, sync_spy):
    tenant = SimpleNamespace(id=1, discord_guild_id=42)
    _route_guild_to(monkeypatch, [tenant])
    user = SimpleNamespace(id=7, discord_id=5)
    monkeypatch.setattr(User, 'get_or_none', AsyncMock(return_value=user))
    await dsvc.sync_member_roles(guild_id=42, discord_user_id=5)
    sync_spy.assert_awaited_once_with(user, tenant)


async def test_syncs_every_tenant_sharing_the_guild(monkeypatch, sync_spy):
    # A shared server backs several tenants; each one is synced for the user.
    a = SimpleNamespace(id=1, discord_guild_id=42)
    b = SimpleNamespace(id=2, discord_guild_id=42)
    _route_guild_to(monkeypatch, [a, b])
    user = SimpleNamespace(id=7, discord_id=5)
    monkeypatch.setattr(User, 'get_or_none', AsyncMock(return_value=user))
    await dsvc.sync_member_roles(guild_id=42, discord_user_id=5)
    assert sync_spy.await_args_list == [call(user, a), call(user, b)]


async def test_never_raises_on_internal_error(monkeypatch, sync_spy):
    # A failure resolving the guild's tenants must be swallowed (best-effort).
    monkeypatch.setattr(
        tsvc.TenantService, 'list_tenants_for_guild',
        AsyncMock(side_effect=RuntimeError('db down')),
    )
    await dsvc.sync_member_roles(guild_id=42, discord_user_id=5)
    sync_spy.assert_not_awaited()


class TestAvatarSync:
    """The cached avatar hash, refreshed from GUILD_MEMBER_UPDATE."""

    async def test_stores_the_members_global_avatar_hash(self, monkeypatch):
        spy = AsyncMock(return_value=True)
        monkeypatch.setattr(UserService, 'sync_discord_avatar', spy)
        member = SimpleNamespace(id=5, avatar=SimpleNamespace(key='abc123'))

        await dsvc.sync_member_avatar(member)

        spy.assert_awaited_once_with(5, 'abc123')

    async def test_a_removed_avatar_clears_the_stored_hash(self, monkeypatch):
        spy = AsyncMock(return_value=True)
        monkeypatch.setattr(UserService, 'sync_discord_avatar', spy)

        await dsvc.sync_member_avatar(SimpleNamespace(id=5, avatar=None))

        spy.assert_awaited_once_with(5, None)

    async def test_never_raises_into_the_gateway(self, monkeypatch):
        monkeypatch.setattr(
            UserService, 'sync_discord_avatar', AsyncMock(side_effect=RuntimeError('db down')),
        )
        await dsvc.sync_member_avatar(SimpleNamespace(id=5, avatar=None))
