"""Regression tests for the October 2026 follow-up security pass.

Each class pins one fix: staff overrides of a shared account's Challonge link,
links and mentions in user names (they render inside bot DMs), the crew-signup
DM throttle, and binding a web session to the host it was first used on.
"""

import pytest
import sentry_sdk

from application.services.challonge_service import ChallongeService
from application.services.crew_service import _signup_notice_due
from application.services.user_service import UserService
from discordbot._tenant import RowScope
from middleware.auth import SESSION_HOST_KEY, session_host_matches
from models import Role, Tenant, TenantMembership, User, UserRole
from tests.api_helpers import create_community_member


async def _staff(username: str = 'boss') -> User:
    user = await create_community_member(username=username)
    await UserRole.create(user=user, role=Role.STAFF, tenant_id=1)
    return user


class TestChallongeOverride:
    async def test_staff_can_link_someone_only_in_this_community(self, db):
        staff = await _staff()
        member = await create_community_member(username='local')
        await ChallongeService().link_player_manually(member, '2002', 'local_c', actor=staff)
        await member.refresh_from_db()
        assert member.challonge_user_id == '2002'

    async def test_staff_cannot_link_someone_who_also_belongs_elsewhere(self, db):
        # The id is global and every community's bracket sync resolves through
        # it, so this would decide who that person is in the other community.
        staff = await _staff()
        shared = await create_community_member(username='shared')
        other = await Tenant.create(name='Elsewhere', slug='elsewhere', is_active=True)
        await TenantMembership.create(user=shared, tenant=other)
        with pytest.raises(PermissionError, match='another community'):
            await ChallongeService().link_player_manually(shared, '3003', None, actor=staff)
        await shared.refresh_from_db()
        assert shared.challonge_user_id is None

    async def test_staff_cannot_rename_a_shared_account_link(self, db):
        staff = await _staff()
        shared = await create_community_member(username='shared', challonge_user_id='4004')
        other = await Tenant.create(name='Elsewhere', slug='elsewhere', is_active=True)
        await TenantMembership.create(user=shared, tenant=other)
        with pytest.raises(PermissionError):
            await ChallongeService().set_player_username(shared, 'renamed', actor=staff)
        # Saving the dialog with the name unchanged is not an edit, and passes.
        await ChallongeService().set_player_username(shared, None, actor=staff)

    async def test_a_non_staff_member_cannot_link_someone_else(self, db):
        actor = await create_community_member(username='plain')
        member = await create_community_member(username='local')
        with pytest.raises(PermissionError, match='Only Staff'):
            await ChallongeService().link_player_manually(member, '5005', None, actor=actor)


class TestNameMarkup:
    @pytest.mark.parametrize('name', [
        '[Review the signup](https://evil.example)',
        'see https://evil.example',
        'www.evil.example',
        'discord.gg/abcdef',
        '<@&1234>',
        '@everyone',
    ])
    async def test_a_name_with_a_link_or_mention_is_refused(self, db, name):
        user = await create_community_member(username='mallory')
        with pytest.raises(ValueError, match='links or Discord mentions'):
            await UserService().update_user_personal_info(user, user, display_name=name)
        with pytest.raises(ValueError, match='links or Discord mentions'):
            await UserService().update_user_profile(user, display_name=name, actor=user)

    @pytest.mark.parametrize('name', ['Tom (he/him)', 'Mr_Bones', 'A.B. [JP]', 'Zoë'])
    async def test_ordinary_names_still_save(self, db, name):
        user = await create_community_member(username='tom')
        await UserService().update_user_personal_info(user, user, display_name=name)
        await user.refresh_from_db()
        assert user.display_name == name


class TestCrewSignupNoticeThrottle:
    def test_one_notice_per_person_match_and_slot_per_window(self):
        assert _signup_notice_due(1, 7, 'commentator') is True
        assert _signup_notice_due(1, 7, 'commentator') is False
        assert _signup_notice_due(1, 7, 'tracker') is True
        assert _signup_notice_due(2, 7, 'commentator') is True
        assert _signup_notice_due(1, 8, 'commentator') is True


class TestSessionHostBinding:
    def test_a_session_binds_to_the_first_host_it_is_used_on(self):
        storage: dict = {}
        assert session_host_matches(storage, 'wizzrobe.example') is True
        assert storage[SESSION_HOST_KEY] == 'wizzrobe.example'
        assert session_host_matches(storage, 'wizzrobe.example') is True

    def test_a_session_replayed_on_another_host_is_refused(self):
        storage = {SESSION_HOST_KEY: 'event.example'}
        assert session_host_matches(storage, 'wizzrobe.example') is False

    def test_no_host_header_is_left_alone(self):
        storage: dict = {}
        assert session_host_matches(storage, None) is True
        assert SESSION_HOST_KEY not in storage


class TestDiscordButtonsRefuseDeactivatedAccounts:
    """DM buttons never time out, so a deactivated account kept using them."""

    async def _press(self, user: User, handle) -> list[str]:
        from types import SimpleNamespace

        from discordbot._ack_common import _run_parsed

        sent: list[str] = []

        async def send(message: str) -> None:
            sent.append(message)

        async def resolve_tenant(_parsed):
            return RowScope(1, 1)

        # _run_parsed tags the Sentry scope; keep that off the worker's scope.
        with sentry_sdk.isolation_scope():
            await _run_parsed(
                SimpleNamespace(user=SimpleNamespace(id=int(user.discord_id))), 'x:1',
                send=send, log_label='test', parse=lambda cid: cid,
                resolve_tenant=resolve_tenant, not_found_message='gone',
                handle=handle, unexpected_error_message='boom',
            )
        return sent

    async def test_a_deactivated_account_gets_the_no_account_reply(self, db):
        from discordbot._ack_common import MSG_NO_ACCOUNT

        user = await create_community_member(username='gone', is_active=False)
        ran = []

        async def handle(*_args):
            ran.append(True)

        assert await self._press(user, handle) == [MSG_NO_ACCOUNT]
        assert ran == []

    async def test_a_refusal_reads_as_its_message_not_a_crash(self, db):
        user = await create_community_member(username='here')

        async def handle(*_args):
            raise PermissionError('Only the other player can agree to this.')

        assert await self._press(user, handle) == ['Only the other player can agree to this.']

    async def test_an_inactive_account_is_not_admitted_by_a_button(self, db):
        from application.services.tenant_membership_service import TenantMembershipService
        from models import SystemConfiguration

        await SystemConfiguration.create(name='open_membership', value='true', tenant_id=1)
        user = await User.create(discord_id=91234, username='inactive', is_active=False)
        assert await TenantMembershipService().admit(user, 1) is False
