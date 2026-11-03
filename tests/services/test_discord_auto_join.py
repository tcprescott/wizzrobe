"""Discord auto-join: a member of the linked server walks through the door.

The membership gate calls ``join_via_discord`` for every signed-in non-member,
so it has to be cheap to say no (switch off, no guild) and must never turn a
Discord failure into either an error page or a membership.
"""

import json
from typing import ClassVar

import pytest

import application.services.discord as discord_pkg
from application.services.system_config_service import (
    KEY_DISCORD_AUTO_JOIN,
    KEY_DISCORD_INVITE_URL,
    KEY_JOIN_REQUESTS,
    SystemConfigService,
)
from application.services.tenant_membership_service import TenantMembershipService
from application.tenant_context import tenant_scope
from models import (
    AuditLog,
    JoinRequestStatus,
    SystemConfiguration,
    Tenant,
    TenantJoinRequest,
    TenantMembership,
    User,
)
from theme.join_page import (
    JoinPreview,
    _non_member_message,
    _signed_out_message,
    resolve_join_preview,
)

GUILD_ID = 555_000_000_000_000_001


class _FakeDiscord:
    """Stands in for ``DiscordService``; ``answer`` is what the bot reports."""

    answer: ClassVar[object] = (True, True)
    calls: ClassVar[list] = []

    async def is_guild_member(self, guild_id, user_id):
        _FakeDiscord.calls.append((guild_id, user_id))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.fixture
def fake_discord(monkeypatch):
    _FakeDiscord.answer = (True, True)
    _FakeDiscord.calls = []
    monkeypatch.setattr(discord_pkg, 'DiscordService', _FakeDiscord)
    return _FakeDiscord


async def _configure(*, auto_join: bool = True, guild: bool = True) -> None:
    await SystemConfiguration.create(
        name=KEY_DISCORD_AUTO_JOIN, value='true' if auto_join else 'false', tenant_id=1,
    )
    await Tenant.filter(id=1).update(discord_guild_id=GUILD_ID if guild else None)


async def _stranger(discord_id: int = 9100) -> User:
    return await User.create(discord_id=discord_id, username=f'stranger{discord_id}')


async def _is_member(user: User) -> bool:
    return await TenantMembership.exists(user=user, tenant_id=1)


async def test_a_server_member_is_let_in_and_audited(fake_discord, db):
    await _configure()
    user = await _stranger()

    assert await TenantMembershipService().join_via_discord(user, 1) is True

    assert await _is_member(user)
    assert fake_discord.calls == [(GUILD_ID, 9100)]
    row = await AuditLog.get(action='tenant.member_added', user=user)
    details = json.loads(row.details)
    assert details['target_user_id'] == user.id
    assert details['source'] == 'discord_auto_join'


async def test_someone_outside_the_server_stays_out(fake_discord, db):
    await _configure()
    fake_discord.answer = (True, False)
    user = await _stranger()

    assert await TenantMembershipService().join_via_discord(user, 1) is False
    assert not await _is_member(user)


async def test_switched_off_never_asks_discord(fake_discord, db):
    await _configure(auto_join=False)
    user = await _stranger()

    assert await TenantMembershipService().join_via_discord(user, 1) is False
    assert fake_discord.calls == []
    assert not await _is_member(user)


async def test_no_linked_guild_never_asks_discord(fake_discord, db):
    await _configure(guild=False)
    user = await _stranger()

    assert await TenantMembershipService().join_via_discord(user, 1) is False
    assert fake_discord.calls == []


async def test_a_bot_that_cannot_tell_is_not_a_yes(fake_discord, db):
    await _configure()
    fake_discord.answer = (False, 'Discord bot is not connected.')
    user = await _stranger()

    assert await TenantMembershipService().join_via_discord(user, 1) is False
    assert not await _is_member(user)


async def test_a_discord_exception_is_swallowed(fake_discord, db):
    await _configure()
    fake_discord.answer = RuntimeError('gateway gone')
    user = await _stranger()

    assert await TenantMembershipService().join_via_discord(user, 1) is False
    assert not await _is_member(user)


async def test_an_account_without_discord_is_not_checked(fake_discord, db):
    await _configure()
    user = await User.create(discord_id=None, username='no-discord')

    assert await TenantMembershipService().join_via_discord(user, 1) is False
    assert fake_discord.calls == []


async def test_a_pending_request_is_closed_as_approved(fake_discord, db):
    await _configure()
    user = await _stranger()
    await TenantMembershipService().request_to_join(user, 1, 'hello')

    assert await TenantMembershipService().join_via_discord(user, 1) is True

    request = await TenantJoinRequest.get(user=user, tenant_id=1)
    assert request.status is JoinRequestStatus.APPROVED
    assert request.decided_by_id is None
    assert request.decided_at is not None
    with tenant_scope(1):
        assert await TenantMembershipService().list_pending() == []


async def test_an_existing_member_is_not_rechecked(fake_discord, db):
    await _configure()
    user = await _stranger()
    await TenantMembership.create(user=user, tenant_id=1)

    assert await TenantMembershipService().join_via_discord(user, 1) is True
    assert fake_discord.calls == []


async def test_the_setting_is_per_tenant(fake_discord, two_tenants, db):
    """Tenant A opening its door to Discord says nothing about tenant B."""
    tenant_a, tenant_b = two_tenants
    await SystemConfiguration.create(
        name=KEY_DISCORD_AUTO_JOIN, value='true', tenant_id=tenant_a.id,
    )
    await Tenant.filter(id__in=[tenant_a.id, tenant_b.id]).update(discord_guild_id=GUILD_ID)
    user = await _stranger()

    service = TenantMembershipService()
    assert await service.join_via_discord(user, tenant_b.id) is False
    assert await service.join_via_discord(user, tenant_a.id) is True
    assert not await TenantMembership.exists(user=user, tenant_id=tenant_b.id)


class TestInviteUrl:
    @pytest.mark.parametrize('raw, expected', [
        ('https://discord.gg/abc123', 'https://discord.gg/abc123'),
        ('discord.gg/abc123', 'https://discord.gg/abc123'),
        ('  https://discord.com/invite/Ab-C1/ ', 'https://discord.gg/Ab-C1'),
        ('https://discordapp.com/invite/xyz', 'https://discord.gg/xyz'),
        ('', ''),
        (None, ''),
    ])
    def test_normalizes_discord_invites(self, raw, expected):
        assert SystemConfigService.normalize_discord_invite_url(raw) == expected

    @pytest.mark.parametrize('raw', [
        'https://example.com/discord.gg/abc',
        'https://discord.gg/',
        'https://discord.gg/abc?evil=1',
        'javascript:alert(1)',
        'https://discord.com/channels/1/2',
    ])
    def test_refuses_anything_else(self, raw):
        with pytest.raises(ValueError):
            SystemConfigService.normalize_discord_invite_url(raw)

    async def test_set_requires_staff(self, db):
        nobody = await _stranger(9200)
        with pytest.raises(PermissionError):
            await SystemConfigService.set_discord_invite_url('discord.gg/abc', nobody)


class TestJoinPreview:
    async def test_the_door_carries_the_invite_and_auto_join(self, db):
        await _configure()
        await SystemConfiguration.create(
            name=KEY_DISCORD_INVITE_URL, value='https://discord.gg/abc', tenant_id=1,
        )
        preview = await resolve_join_preview(1)
        assert preview.discord_invite_url == 'https://discord.gg/abc'
        assert preview.discord_auto_join is True

    async def test_auto_join_without_a_guild_is_not_promised(self, db):
        await _configure(guild=False)
        preview = await resolve_join_preview(1)
        assert preview.discord_auto_join is False
        assert preview.discord_invite_url is None


class TestJoinRequestsOff:
    async def test_auto_join_still_lets_a_server_member_in(self, fake_discord, db):
        await _configure()
        await SystemConfiguration.create(
            name=KEY_JOIN_REQUESTS, value='false', tenant_id=1,
        )
        user = await _stranger(9300)

        assert await TenantMembershipService().join_via_discord(user, 1) is True
        assert await _is_member(user)

    async def test_the_door_reads_the_switch(self, db):
        assert (await resolve_join_preview(1)).join_requests is True
        await SystemConfiguration.create(
            name=KEY_JOIN_REQUESTS, value='false', tenant_id=1,
        )
        assert (await resolve_join_preview(1)).join_requests is False


class TestDoorCopy:
    @pytest.mark.parametrize('auto_join,requests', [
        (True, True), (True, False), (False, True), (False, False),
    ])
    def test_the_door_only_offers_ways_in_that_are_open(self, auto_join, requests):
        preview = JoinPreview(discord_auto_join=auto_join, join_requests=requests)
        for text in (_signed_out_message(preview), _non_member_message(preview)):
            assert ('Discord server' in text) is auto_join
            assert ('ask' in text.lower()) is requests


async def test_an_auto_join_dms_nobody_and_marks_the_membership(fake_discord, monkeypatch, db):
    from models import MembershipSource, Role, UserRole

    queued: list = []
    monkeypatch.setattr(discord_pkg.discord_queue, 'enqueue', queued.append)

    async def no_dm(self, *_a, **_kw):
        return True, ''

    fake_discord.send_dm = no_dm
    await _configure()
    boss = await User.create(discord_id=9200, username='boss')
    await UserRole.create(user=boss, role=Role.STAFF, tenant_id=1)
    user = await _stranger()
    await TenantMembershipService().request_to_join(user, 1, 'hi')
    for coro in queued:
        coro.close()
    queued.clear()

    assert await TenantMembershipService().join_via_discord(user, 1) is True

    assert [coro.__name__ for coro in queued] == ['_edit_all']
    for coro in queued:
        coro.close()
    membership = await TenantMembership.get(user=user, tenant_id=1)
    assert membership.source is MembershipSource.DISCORD_AUTO_JOIN
    request = await TenantJoinRequest.get(user=user, tenant_id=1)
    row = await AuditLog.filter(action='tenant.member_added', user=user).first()
    assert json.loads(row.details)['closed_request_id'] == request.id


async def test_auto_join_active_needs_the_switch_and_a_guild(db):
    assert await TenantMembershipService.auto_join_active(1) is False
    await _configure(guild=False)
    assert await TenantMembershipService.auto_join_active(1) is False
    await Tenant.filter(id=1).update(discord_guild_id=GUILD_ID)
    assert await TenantMembershipService.auto_join_active(1) is True
