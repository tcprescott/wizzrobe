"""Admin → Users › Import from Discord: everyone in the linked server, at once."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from application.services.discord import discord_member_import_service as dmis
from application.services.discord.discord_guild_ops import GuildMember
from application.services.discord.discord_member_import_service import DiscordMemberImportService
from models import (
    AuditLog,
    JoinRequestStatus,
    MembershipSource,
    Tenant,
    TenantJoinRequest,
    TenantMemberBlock,
    TenantMembership,
    User,
)

pytestmark = pytest.mark.usefixtures("bypass_auth")

GUILD_ID = 42


def _member(discord_id: int, username: str, *, bot: bool = False) -> GuildMember:
    return GuildMember(id=discord_id, username=username, avatar='hash', role_ids=frozenset(), bot=bot)


@pytest.fixture
async def linked(db):
    tenant = await Tenant.get(id=1)
    tenant.discord_guild_id = GUILD_ID
    await tenant.save()
    return tenant


@pytest.fixture
def guild(monkeypatch):
    """Serves ``roster`` as the server's members; tests fill it in."""
    roster: list = []
    list_members = AsyncMock(side_effect=lambda _gid: (True, list(roster)))
    fake = SimpleNamespace(
        list_members=list_members,
        get_guild_summary=AsyncMock(return_value=(True, {'id': GUILD_ID, 'name': 'SpeedGaming'})),
    )
    monkeypatch.setattr(dmis, 'DiscordService', lambda: fake)
    return SimpleNamespace(roster=roster, list_members=list_members)


async def test_preview_counts_without_writing(linked, guild):
    staff = await User.create(discord_id=1, username='staff')
    member = await User.create(discord_id=2, username='already_here')
    await TenantMembership.create(user=member, tenant_id=1)
    outsider = await User.create(discord_id=3, username='has_account')
    removed = await User.create(discord_id=4, username='removed')
    await TenantMemberBlock.create(user=removed, tenant_id=1)
    guild.roster[:] = [
        _member(2, 'already_here'), _member(3, 'has_account'), _member(4, 'removed'),
        _member(10, 'newcomer'), _member(11, 'another'), _member(99, 'carl-bot', bot=True),
    ]

    preview = await DiscordMemberImportService().preview(staff)

    assert preview.server_name == 'SpeedGaming'
    assert (preview.people, preview.new_accounts, preview.new_members) == (5, 2, 3)
    assert (preview.already_members, preview.bots, preview.removed) == (1, 1, 1)
    assert await User.filter(discord_id__in=[10, 11]).count() == 0
    assert not await TenantMembership.filter(user=outsider).exists()


async def test_run_creates_accounts_and_members_skipping_bots_and_removed(linked, guild):
    staff = await User.create(discord_id=1, username='staff')
    existing = await User.create(discord_id=3, username='chosen_name')
    removed = await User.create(discord_id=4, username='removed')
    await TenantMemberBlock.create(user=removed, tenant_id=1)
    guild.roster[:] = [
        _member(3, 'guild_name'), _member(4, 'removed'),
        _member(10, 'newcomer'), _member(99, 'carl-bot', bot=True),
    ]

    result = await DiscordMemberImportService().run(staff)

    assert (result.accounts_created, result.members_added) == (1, 2)
    newcomer = await User.get(discord_id=10)
    assert (newcomer.username, newcomer.discord_avatar) == ('newcomer', 'hash')
    for user in (newcomer, existing):
        membership = await TenantMembership.get(user=user, tenant_id=1)
        assert membership.source is MembershipSource.DISCORD_IMPORT
    await existing.refresh_from_db()
    assert existing.username == 'chosen_name'
    assert not await TenantMembership.filter(user=removed).exists()
    assert await TenantMemberBlock.filter(user=removed, tenant_id=1).exists()
    assert not await User.filter(discord_id=99).exists()

    audit = await AuditLog.get(action='tenant.members_imported')
    details = json.loads(audit.details)
    assert details['accounts_created'] == 1
    assert sorted(details['added_user_ids']) == sorted([newcomer.id, existing.id])


async def test_second_run_adds_nobody(linked, guild):
    staff = await User.create(discord_id=1, username='staff')
    guild.roster[:] = [_member(10, 'newcomer'), _member(11, 'another')]
    await DiscordMemberImportService().run(staff)

    preview = await DiscordMemberImportService().preview(staff)
    result = await DiscordMemberImportService().run(staff)

    assert (preview.new_members, preview.already_members) == (0, 2)
    assert (result.accounts_created, result.members_added) == (0, 0)
    assert await User.filter(discord_id__in=[10, 11]).count() == 2


async def test_inactive_account_is_not_added(linked, guild):
    staff = await User.create(discord_id=1, username='staff')
    inactive = await User.create(discord_id=5, username='banned', is_active=False)
    guild.roster[:] = [_member(5, 'banned')]

    preview = await DiscordMemberImportService().preview(staff)
    result = await DiscordMemberImportService().run(staff)

    assert preview.new_members == 0
    assert result.members_added == 0
    assert not await TenantMembership.filter(user=inactive).exists()


async def test_closes_a_pending_join_request(linked, guild):
    staff = await User.create(discord_id=1, username='staff')
    asker = await User.create(discord_id=6, username='asker')
    request = await TenantJoinRequest.create(user=asker, tenant_id=1)
    guild.roster[:] = [_member(6, 'asker')]

    await DiscordMemberImportService().run(staff)

    await request.refresh_from_db()
    assert request.status is JoinRequestStatus.APPROVED


async def test_no_linked_server_is_a_user_error(db, guild):
    staff = await User.create(discord_id=1, username='staff')
    with pytest.raises(ValueError, match='no Discord server linked'):
        await DiscordMemberImportService().preview(staff)
    guild.list_members.assert_not_awaited()


async def test_unreadable_server_is_a_user_error(linked, guild):
    staff = await User.create(discord_id=1, username='staff')
    guild.list_members.side_effect = None
    guild.list_members.return_value = (False, 'Discord bot is not connected.')
    with pytest.raises(ValueError, match='not connected'):
        await DiscordMemberImportService().run(staff)
    assert await User.all().count() == 1


async def test_non_staff_is_refused(linked, guild, monkeypatch):
    async def deny(*_a, **_kw):
        return False

    async def real_ensure(allowed, message=None):
        if not allowed:
            raise PermissionError(message or 'denied')

    monkeypatch.setattr(dmis.AuthService, 'can_grant_roles', deny)
    monkeypatch.setattr(dmis.AuthService, 'ensure', real_ensure)
    player = await User.create(discord_id=1, username='player')
    guild.roster[:] = [_member(10, 'newcomer')]

    with pytest.raises(PermissionError):
        await DiscordMemberImportService().run(player)
    assert not await User.filter(discord_id=10).exists()
