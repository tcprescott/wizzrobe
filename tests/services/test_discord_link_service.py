"""Tests for DiscordLinkService — the verified tenant↔Discord-server link.

The security-critical guarantees: only STAFF/super-admin may link, and a link
only lands when the acting user provably administers the target guild (the bot's
authority check). The bot check is stubbed here; test_discord_service.py covers
the real permission computation.
"""

from unittest.mock import AsyncMock

import pytest

from application.services.discord import discord_link_service as dls
from application.services.discord.discord_link_service import DiscordLinkService
from application.tenant_context import tenant_scope
from models import Role, Tenant, User, UserRole


@pytest.fixture
async def super_admin(db):
    su = await User.create(discord_id=1000, username='root')
    await UserRole.create(user=su, role=Role.SUPER_ADMIN, tenant=None)
    return su


@pytest.fixture
async def tenant(db):
    return await Tenant.get(id=1)


def _stub_authority(monkeypatch, ok, can_manage):
    """Force DiscordService().member_can_manage_guild to a fixed answer."""
    svc = AsyncMock()
    svc.member_can_manage_guild = AsyncMock(return_value=(ok, can_manage))
    monkeypatch.setattr(dls, 'DiscordService', lambda: svc)
    return svc


def test_authorize_url_requests_bot_scope():
    url = DiscordLinkService.authorize_url('st8')
    assert url.startswith('https://discord.com/api/oauth2/authorize?')
    assert 'scope=bot' in url
    assert 'response_type=code' in url
    assert 'state=st8' in url


async def test_can_manage_link(super_admin, db):
    assert await DiscordLinkService.can_manage_link(None) is False
    assert await DiscordLinkService.can_manage_link(super_admin) is True
    nobody = await User.create(discord_id=2000, username='nobody')
    assert await DiscordLinkService.can_manage_link(nobody) is False


async def test_link_guild_requires_authorization(monkeypatch, tenant, db):
    _stub_authority(monkeypatch, True, True)
    nobody = await User.create(discord_id=2000, username='nobody')
    with pytest.raises(PermissionError):
        await DiscordLinkService.link_guild(nobody, tenant, 555)
    await tenant.refresh_from_db()
    assert tenant.discord_guild_id is None


async def test_link_guild_rejects_non_admin_of_guild(monkeypatch, super_admin, tenant, db):
    _stub_authority(monkeypatch, True, False)
    with pytest.raises(ValueError, match='Manage Server'):
        await DiscordLinkService.link_guild(super_admin, tenant, 555)
    await tenant.refresh_from_db()
    assert tenant.discord_guild_id is None


async def test_link_guild_fails_closed_when_bot_indeterminate(monkeypatch, super_admin, tenant, db):
    _stub_authority(monkeypatch, False, 'bot not ready')
    with pytest.raises(ValueError, match='verify'):
        await DiscordLinkService.link_guild(super_admin, tenant, 555)
    await tenant.refresh_from_db()
    assert tenant.discord_guild_id is None


async def test_link_guild_sets_guild_when_authorized(monkeypatch, super_admin, tenant, db):
    _stub_authority(monkeypatch, True, True)
    with tenant_scope(tenant.id):
        await DiscordLinkService.link_guild(super_admin, tenant, 555)
    await tenant.refresh_from_db()
    assert tenant.discord_guild_id == 555


async def test_complete_link_exchanges_then_links(monkeypatch, super_admin, tenant, db):
    _stub_authority(monkeypatch, True, True)
    monkeypatch.setattr(DiscordLinkService, '_exchange_code', AsyncMock(return_value=777))
    with tenant_scope(tenant.id):
        await DiscordLinkService.complete_link(super_admin, tenant, 'good-code')
    await tenant.refresh_from_db()
    assert tenant.discord_guild_id == 777


async def test_complete_link_fails_when_exchange_fails(monkeypatch, super_admin, tenant, db):
    _stub_authority(monkeypatch, True, True)
    monkeypatch.setattr(DiscordLinkService, '_exchange_code', AsyncMock(return_value=None))
    with pytest.raises(ValueError, match='confirm'):
        await DiscordLinkService.complete_link(super_admin, tenant, 'bad-code')
    await tenant.refresh_from_db()
    assert tenant.discord_guild_id is None


async def test_disconnect_clears_guild(monkeypatch, super_admin, tenant, db):
    _stub_authority(monkeypatch, True, True)
    with tenant_scope(tenant.id):
        await DiscordLinkService.link_guild(super_admin, tenant, 555)
        await DiscordLinkService.disconnect(super_admin, tenant)
    await tenant.refresh_from_db()
    assert tenant.discord_guild_id is None
