"""Open membership and the one join-mode choice behind the door.

Open membership is the no-strings sibling of Discord auto-join: anyone who signs
in is a member on the spot. The three stored switches are only ever written as
one ``JoinMode``, so staff can't set a combination nobody meant.
"""

import pytest

from application.services.system_config_service import (
    KEY_DISCORD_AUTO_JOIN,
    KEY_JOIN_REQUESTS,
    KEY_OPEN_MEMBERSHIP,
    SystemConfigService,
)
from application.services.tenant_membership_service import JoinMode, TenantMembershipService
from application.tenant_context import tenant_scope
from application.utils.discord_messages_tenant import member_removed_dm
from models import (
    AuditLog,
    JoinRequestStatus,
    MembershipSource,
    Role,
    SystemConfiguration,
    Tenant,
    TenantJoinRequest,
    TenantMembership,
    User,
    UserRole,
)
from theme.join_page import JoinPreview, _non_member_message, _signed_out_message, resolve_join_preview


async def _open(value: bool = True) -> None:
    await SystemConfiguration.create(
        name=KEY_OPEN_MEMBERSHIP, value='true' if value else 'false', tenant_id=1,
    )


async def _stranger(discord_id: int = 9300) -> User:
    return await User.create(discord_id=discord_id, username=f'stranger{discord_id}')


async def _staff() -> User:
    staff = await User.create(discord_id=9399, username='staffer')
    await TenantMembership.create(user=staff, tenant_id=1)
    await UserRole.create(user=staff, role=Role.STAFF, tenant_id=1)
    return staff


class TestJoinOpenly:
    async def test_anyone_signed_in_is_let_in_and_audited(self, db):
        await _open()
        user = await _stranger()

        assert await TenantMembershipService().join_openly(user, 1) is True

        membership = await TenantMembership.get(user=user, tenant_id=1)
        assert membership.source is MembershipSource.OPEN
        assert await AuditLog.filter(action='tenant.member_added', tenant_id=1).exists()

    async def test_a_closed_community_lets_nobody_in(self, db):
        await _open(False)
        user = await _stranger()

        assert await TenantMembershipService().join_openly(user, 1) is False
        assert not await TenantMembership.exists(user=user, tenant_id=1)

    async def test_it_closes_a_pending_request_as_approved(self, db):
        user = await _stranger()
        request = await TenantJoinRequest.create(
            user=user, tenant_id=1, status=JoinRequestStatus.PENDING,
        )
        await _open()

        await TenantMembershipService().join_openly(user, 1)

        await request.refresh_from_db()
        assert request.status is JoinRequestStatus.APPROVED

    async def test_another_communitys_door_stays_shut(self, db):
        await _open()
        other = await Tenant.create(name='Other', slug='other')
        user = await _stranger()

        assert await TenantMembershipService().join_openly(user, other.id) is False


class TestAdmit:
    async def test_admit_lets_a_first_contact_in_when_open(self, db):
        await _open()
        user = await _stranger()
        with tenant_scope(1):
            assert await TenantMembershipService().admit(user) is True
        assert await TenantMembership.exists(user=user, tenant_id=1)

    async def test_admit_refuses_when_nothing_lets_them_in(self, db):
        user = await _stranger()
        with tenant_scope(1):
            assert await TenantMembershipService().admit(user) is False


class TestJoinMode:
    @pytest.mark.parametrize('mode', list(JoinMode))
    def test_every_mode_round_trips_through_its_switches(self, mode):
        assert JoinMode.from_flags(*mode.flags) is mode

    async def test_the_default_is_ask_staff(self, db):
        assert await TenantMembershipService.get_join_mode(1) is JoinMode.REQUEST

    async def test_setting_a_mode_writes_all_three_switches(self, db):
        staff = await _staff()
        with tenant_scope(1):
            await TenantMembershipService.set_join_mode(staff, JoinMode.OPEN)
            assert await SystemConfigService.get_bool(KEY_OPEN_MEMBERSHIP) is True
            assert await SystemConfigService.get_bool(KEY_DISCORD_AUTO_JOIN) is False
            assert await SystemConfigService.get_bool(KEY_JOIN_REQUESTS, default=True) is False
        assert await TenantMembershipService.get_join_mode(1) is JoinMode.OPEN

    async def test_a_discord_mode_needs_a_linked_server(self, db):
        staff = await _staff()
        await Tenant.filter(id=1).update(discord_guild_id=None)
        with tenant_scope(1), pytest.raises(ValueError, match='Link a Discord server'):
            await TenantMembershipService.set_join_mode(staff, JoinMode.DISCORD)

    async def test_only_staff_can_change_it(self, db):
        nobody = await _stranger()
        with tenant_scope(1), pytest.raises(PermissionError):
            await TenantMembershipService.set_join_mode(nobody, JoinMode.OPEN)


class TestDoorCopy:
    async def test_the_door_knows_it_is_open(self, db):
        await _open()
        preview = await resolve_join_preview(1)
        assert preview.open_membership is True

    def test_signed_out_visitors_are_told_signing_in_is_enough(self):
        text = _signed_out_message(JoinPreview(open_membership=True, join_requests=False))
        assert 'Sign in' in text and 'you’re in' in text

    def test_a_failed_admit_says_to_reload_rather_than_to_ask(self):
        text = _non_member_message(JoinPreview(open_membership=True, join_requests=False))
        assert 'Reload' in text and 'Ask to join' not in text

    def test_a_removal_dm_says_how_to_come_back(self):
        assert 'ask to join again' in member_removed_dm('SGL', can_ask=True)
        assert 'ask to join' not in member_removed_dm('SGL', can_ask=False)


class TestRemovalHolds:
    """Removing someone used to last until their next page view under open join."""

    async def test_a_removed_member_is_not_let_back_in_openly(self, db):
        await _open()
        staff = await _staff()
        user = await _stranger()
        service = TenantMembershipService()
        assert await service.join_openly(user, 1) is True

        await service.remove_member(staff, user)

        assert await service.join_openly(user, 1) is False
        assert await service.admit(user, 1) is False
        assert await service.was_removed(user, 1) is True
        assert not await TenantMembership.exists(user=user, tenant_id=1)

    async def test_staff_adding_them_back_lifts_the_block(self, db):
        await _open()
        staff = await _staff()
        user = await _stranger()
        service = TenantMembershipService()
        await service.join_openly(user, 1)
        await service.remove_member(staff, user)

        await service.add_member(staff, user)
        await service.remove_member(staff, user)
        assert await service.was_removed(user, 1) is True

        await service.add_member(staff, user)
        assert await service.was_removed(user, 1) is False

    async def test_an_approved_request_lifts_the_block(self, db):
        staff = await _staff()
        user = await _stranger()
        service = TenantMembershipService()
        await TenantMembership.create(user=user, tenant_id=1)
        await service.remove_member(staff, user)

        request = await service.request_to_join(user, 1, 'sorry')
        await service.approve_request(staff, request.id)

        assert await service.was_removed(user, 1) is False
        await _open()
        await service.remove_member(staff, user)
        assert await service.join_openly(user, 1) is False

    async def test_a_block_is_per_community(self, db):
        await _open()
        other = await Tenant.create(name='Elsewhere', slug='elsewhere', is_active=True)
        await SystemConfiguration.create(name=KEY_OPEN_MEMBERSHIP, value='true', tenant_id=other.id)
        staff = await _staff()
        user = await _stranger()
        service = TenantMembershipService()
        await service.join_openly(user, 1)
        await service.remove_member(staff, user)

        assert await service.join_openly(user, other.id) is True
