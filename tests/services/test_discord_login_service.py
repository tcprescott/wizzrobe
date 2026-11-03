"""DiscordLoginService — the bot's one-time login links and the panel gate.

The security-critical guarantees: a link signs in exactly the Discord user it
was minted for, once, before it expires, on the host and community it names;
a deactivated account gets no link; only community STAFF may post a panel.
"""

import json
from urllib.parse import parse_qs, urlparse

import pytest

from application.services import tenant_service
from application.services.audit_service import AuditActions
from application.services.discord import discord_login_service as dls
from application.services.discord.discord_login_service import DiscordLoginService
from models import AuditLog, Role, Tenant, User, UserRole
from tests.factories import make_user

GUILD = 4242


@pytest.fixture(autouse=True)
def _secret_and_reset(monkeypatch):
    monkeypatch.setenv('STORAGE_SECRET', 'x' * 40)
    monkeypatch.setenv('BASE_URL', 'https://wizzrobe.test')
    dls.reset()
    yield
    dls.reset()


@pytest.fixture
async def tenant(db):
    t = await Tenant.get(id=1)
    t.discord_guild_id = GUILD
    await t.save()
    tenant_service._clear_cache()
    return t


async def _mint(tenant, discord_id=777, username='pressy'):
    return await DiscordLoginService.mint_link(
        tenant, discord_id=discord_id, username=username,
        avatar_hash='', avatar_url='https://cdn.test/a.png',
    )


def _token_and_host(url):
    parsed = urlparse(url)
    return parse_qs(parsed.query)['token'][0], parsed.netloc


async def test_link_points_at_the_communitys_claim_page(tenant):
    url = await _mint(tenant)
    assert url.startswith('https://wizzrobe.test/t/default/login/discord?token=')


async def test_mint_provisions_a_first_time_user(tenant):
    await _mint(tenant, discord_id=9001, username='newcomer')
    user = await User.get(discord_id=9001)
    assert user.username == 'newcomer'


async def test_claim_roundtrips_identity_once(tenant):
    token, host = _token_and_host(await _mint(tenant))
    payload = DiscordLoginService.claim(token, host, tenant.id)
    assert payload is not None
    assert payload['discord_id'] == 777
    assert payload['username'] == 'pressy'
    assert DiscordLoginService.claim(token, host, tenant.id) is None


async def test_claim_rejects_wrong_host_and_burns_the_token(tenant):
    token, host = _token_and_host(await _mint(tenant))
    assert DiscordLoginService.claim(token, 'evil.test', tenant.id) is None
    assert DiscordLoginService.claim(token, host, tenant.id) is None


async def test_claim_rejects_another_community_on_the_same_host(tenant):
    other = await Tenant.create(name='Other', slug='other')
    token, host = _token_and_host(await _mint(tenant))
    assert DiscordLoginService.claim(token, host, other.id) is None


async def test_claim_rejects_expired_and_tampered(tenant, monkeypatch):
    token, host = _token_and_host(await _mint(tenant))
    assert DiscordLoginService.claim(token + 'x', host, tenant.id) is None

    token, host = _token_and_host(await _mint(tenant))
    real_time = dls.time.time
    monkeypatch.setattr(dls.time, 'time', lambda: real_time() + dls.LINK_TTL_SECONDS + 1)
    assert DiscordLoginService.claim(token, host, tenant.id) is None


async def test_inactive_account_gets_no_link(tenant):
    await make_user(discord_id=555, is_active=False)
    with pytest.raises(ValueError, match='inactive'):
        await _mint(tenant, discord_id=555)
    assert dls._pending == {}


async def test_communities_for_guild_skips_inactive(tenant):
    await Tenant.create(name='Gone', slug='gone', discord_guild_id=GUILD, is_active=False)
    tenant_service._clear_cache()
    assert [t.id for t in await DiscordLoginService.communities_for_guild(GUILD)] == [tenant.id]
    assert await DiscordLoginService.communities_for_guild(None) == []


async def test_panel_for_an_unlinked_community_is_refused(tenant):
    assert (await DiscordLoginService.community_for_panel(GUILD, tenant.id)).id == tenant.id
    with pytest.raises(ValueError, match='no longer linked'):
        await DiscordLoginService.community_for_panel(GUILD + 1, tenant.id)


async def test_only_staff_may_post_the_panel(tenant):
    member = await make_user(discord_id=10)
    with pytest.raises(PermissionError):
        await DiscordLoginService.authorize_panel(10, tenant)
    with pytest.raises(PermissionError):
        await DiscordLoginService.authorize_panel(11, tenant)

    await UserRole.create(user=member, role=Role.STAFF, tenant=tenant)
    assert (await DiscordLoginService.authorize_panel(10, tenant)).id == member.id


async def test_staff_elsewhere_may_not_post_here(tenant):
    other = await Tenant.create(name='Other', slug='other')
    member = await make_user(discord_id=12)
    await UserRole.create(user=member, role=Role.STAFF, tenant=other)
    with pytest.raises(PermissionError):
        await DiscordLoginService.authorize_panel(12, tenant)


async def test_posting_the_panel_is_audited(tenant):
    staff = await make_user(discord_id=13)
    await DiscordLoginService.record_panel_posted(staff, tenant, 9999)
    row = await AuditLog.get(action=AuditActions.DISCORD_LOGIN_PANEL_POSTED)
    assert row.tenant_id == tenant.id
    details = json.loads(row.details)
    assert details['guild_id'] == GUILD
    assert details['channel_id'] == 9999
