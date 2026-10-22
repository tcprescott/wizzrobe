"""The platform tools: only a super admin reaches them, and they really write.

A community's Staff is an outsider to the platform, however many communities
they run, so the role gate is asserted for every tool, reads included. The
read-only gate is asserted for every write. Then each kind of write is checked
against the row it should have changed.
"""

import logging

import pytest

from application.services.audit_service import AuditActions
from models import (
    AuditLog,
    FeatureFlag,
    RacetimeBot,
    RacetimeBotTenant,
    Role,
    Tenant,
    TenantFeatureFlag,
    User,
    UserRole,
)
from tests.mcp.conftest import create_oauth_token, mcp_session, rpc
from tests.mcp.test_mcp_catalogue import PLATFORM_TOOLS, PLATFORM_WRITE_TOOLS

_LOGGER = 'wizzrobe.test.platform_mcp'

# Every platform tool with arguments that get past argument validation, so the
# gate is what answers. They need not name rows that exist.
PLATFORM_CALLS = {
    'runtime_stats': {},
    'memory_census': {},
    'platform_list_communities': {},
    'platform_get_community': {'community': 'default'},
    'platform_list_users': {},
    'platform_get_user': {'user_id': 1},
    'platform_list_feature_groups': {},
    'platform_list_racetime_bots': {},
    'platform_list_log_levels': {},
    'platform_service_health': {},
    'platform_create_community': {'name': 'X', 'slug': 'x'},
    'platform_update_community': {'community': 'default', 'name': 'X'},
    'platform_set_community_active': {'community': 'default', 'active': True},
    'platform_grant_community_staff': {'community': 'default', 'user_id': 1},
    'platform_set_community_feature_group': {'community': 'default'},
    'platform_set_community_feature': {'community': 'default', 'feature': 'volunteers', 'availability': 'inherit'},
    'platform_create_feature_group': {'name': 'X', 'flags': []},
    'platform_update_feature_group': {'group_id': 1, 'name': 'X'},
    'platform_delete_feature_group': {'group_id': 1},
    'platform_set_user_active': {'user_id': 1, 'active': True},
    'platform_update_racetime_bot': {'bot_id': 1, 'name': 'X'},
    'platform_grant_racetime_bot': {'bot_id': 1, 'community': 'default'},
    'platform_revoke_racetime_bot': {'bot_id': 1, 'community': 'default'},
    'platform_restart_racetime_bot': {'bot_id': 1},
    'platform_set_log_level': {'logger': _LOGGER, 'level': 'INFO'},
    'platform_reset_log_level': {'logger': _LOGGER},
    'platform_refresh_service_health': {},
}


@pytest.fixture(autouse=True)
def _restore_logger():
    target = logging.getLogger(_LOGGER)
    saved = target.level
    yield
    target.setLevel(saved)


async def call_tool(client, raw, tool, **arguments):
    """``conftest.call_tool``, but free to pass a ``name`` argument through."""
    resp = await rpc(client, raw, 'tools/call', {'name': tool, 'arguments': arguments})
    assert resp.status_code == 200, resp.text
    result = resp.json()['result']
    text = result['content'][0]['text'] if result.get('content') else ''
    if result.get('isError'):
        return True, text
    return False, result.get('structuredContent', text)


def test_every_platform_tool_is_exercised_here():
    assert set(PLATFORM_CALLS) == PLATFORM_TOOLS


class TestTheGates:
    async def test_staff_is_refused_every_platform_tool(self, db):
        _, raw = await create_oauth_token(roles=[Role.STAFF], write=True)
        async with mcp_session() as client:
            for name, args in PLATFORM_CALLS.items():
                is_error, payload = await call_tool(client, raw, name, **args)
                assert is_error, name
                assert 'forbidden:' in payload and 'super admin' in payload.lower(), (name, payload)
        assert await Tenant.filter(slug='x').count() == 0

    async def test_a_read_only_super_admin_is_refused_every_platform_write(self, db):
        _, raw = await create_oauth_token(roles=[Role.SUPER_ADMIN])
        async with mcp_session() as client:
            for name in PLATFORM_WRITE_TOOLS:
                is_error, payload = await call_tool(client, raw, name, **PLATFORM_CALLS[name])
                assert is_error, name
                assert 'forbidden:' in payload and 'read' in payload.lower(), (name, payload)


class TestCommunities:
    async def test_lists_and_reaches_a_switched_off_community(self, db):
        await Tenant.create(name='Dormant', slug='dormant', is_active=False)
        _, raw = await create_oauth_token(roles=[Role.SUPER_ADMIN], write=True)
        async with mcp_session() as client:
            _, rows = await call_tool(client, raw, 'platform_list_communities')
            assert any(r['slug'] == 'dormant' and r['is_active'] is False for r in rows['result'])

            is_error, detail = await call_tool(
                client, raw, 'platform_set_community_active', community='dormant', active=True,
            )
        assert not is_error, detail
        assert detail['is_active'] is True
        assert (await Tenant.get(slug='dormant')).is_active is True

    async def test_create_edit_and_grant_staff(self, db):
        organizer = await User.create(discord_id=320395696098377729, username='organizer')
        _, raw = await create_oauth_token(roles=[Role.SUPER_ADMIN], write=True)
        async with mcp_session() as client:
            is_error, created = await call_tool(
                client, raw, 'platform_create_community',
                name='Fresh', slug='fresh', discord_guild_id='320395696098377729',
            )
            assert not is_error, created
            assert created['discord_guild_id'] == '320395696098377729'

            is_error, staff = await call_tool(
                client, raw, 'platform_grant_community_staff', community='fresh', user_id=organizer.id,
            )
            assert not is_error, staff
            assert [u['discord_id'] for u in staff['result']] == ['320395696098377729']

            is_error, edited = await call_tool(
                client, raw, 'platform_update_community', community='fresh', clear_discord_guild=True,
            )
            assert not is_error, edited
        tenant = await Tenant.get(slug='fresh')
        assert tenant.discord_guild_id is None
        assert await UserRole.filter(user=organizer, tenant=tenant, role=Role.STAFF).exists()

    async def test_feature_override(self, db):
        tenant = await Tenant.create(name='Flags', slug='flags')
        _, raw = await create_oauth_token(roles=[Role.SUPER_ADMIN], write=True)
        async with mcp_session() as client:
            is_error, detail = await call_tool(
                client, raw, 'platform_set_community_feature',
                community='flags', feature=FeatureFlag.VOLUNTEERS.value, availability='on',
            )
            assert not is_error, detail
            bad, payload = await call_tool(
                client, raw, 'platform_set_community_feature',
                community='flags', feature='nope', availability='on',
            )
        assert bad and 'invalid_request:' in payload
        row = next(f for f in detail['features'] if f['flag'] == FeatureFlag.VOLUNTEERS.value)
        assert row['override'] is True
        stored = await TenantFeatureFlag.get(tenant_id=tenant.id, flag=FeatureFlag.VOLUNTEERS.value)
        assert stored.available is True


class TestAccountsAndOps:
    async def test_deactivate_an_account(self, db):
        target = await User.create(discord_id=555, username='target')
        _, raw = await create_oauth_token(roles=[Role.SUPER_ADMIN], write=True)
        async with mcp_session() as client:
            is_error, row = await call_tool(
                client, raw, 'platform_set_user_active', user_id=target.id, active=False,
            )
        assert not is_error, row
        assert row['is_active'] is False
        assert (await User.get(id=target.id)).is_active is False
        assert await AuditLog.filter(action=AuditActions.USER_ACTIVATION_CHANGED).exists()

    async def test_search_users_returns_string_discord_ids(self, db):
        await User.create(discord_id=320395696098377729, username='needle')
        _, raw = await create_oauth_token(roles=[Role.SUPER_ADMIN])
        async with mcp_session() as client:
            _, rows = await call_tool(client, raw, 'platform_list_users', search='needl')
        assert [r['discord_id'] for r in rows['result']] == ['320395696098377729']

    async def test_log_level_round_trip(self, db):
        _, raw = await create_oauth_token(roles=[Role.SUPER_ADMIN], write=True)
        async with mcp_session() as client:
            is_error, rows = await call_tool(
                client, raw, 'platform_set_log_level', logger=_LOGGER, level='DEBUG',
            )
            assert not is_error, rows
            assert logging.getLogger(_LOGGER).level == logging.DEBUG
            await call_tool(client, raw, 'platform_reset_log_level', logger=_LOGGER)
        assert logging.getLogger(_LOGGER).level == logging.NOTSET

    async def test_grant_and_restart_a_bot(self, db, monkeypatch):
        restarted = []

        class _Manager:
            async def restart(self, actor, bot_id):
                restarted.append(bot_id)

        monkeypatch.setattr('racetimebot.get_racetime_manager', lambda: _Manager())
        bot = await RacetimeBot.create(category='alttp', client_id='c', client_secret='hush', name='Bot')
        _, raw = await create_oauth_token(roles=[Role.SUPER_ADMIN], write=True)
        async with mcp_session() as client:
            is_error, row = await call_tool(
                client, raw, 'platform_grant_racetime_bot', bot_id=bot.id, community='default',
            )
            assert not is_error, row
            assert row['communities'] == ['default']
            is_error, row = await call_tool(client, raw, 'platform_restart_racetime_bot', bot_id=bot.id)
            assert not is_error, row
            _, listed = await call_tool(client, raw, 'platform_list_racetime_bots')
        assert restarted == [bot.id]
        assert await RacetimeBotTenant.filter(bot=bot, tenant_id=1).exists()
        assert 'hush' not in str(listed)
