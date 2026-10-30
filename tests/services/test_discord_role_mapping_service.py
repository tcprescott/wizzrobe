"""Unit tests for DiscordRoleMappingService.

Covers the login-time role sync (grant/revoke/full-sync semantics, the
manual-vs-discord source guard, and fail-open behaviour) plus the mapping
CRUD permission gates and audit logging.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from application.services.discord import discord_role_mapping_service as drms
from application.services.discord.discord_guild_ops import GuildMember
from application.services.discord.discord_role_mapping_service import DiscordRoleMappingService
from models import MembershipSource, Role, RoleSource, TournamentGrant
from tests.conftest import DEFAULT_TEST_EVENT_ID
from tests.factories import make_audit_double

pytestmark = pytest.mark.usefixtures("bypass_auth")


def make_service():
    svc = object.__new__(DiscordRoleMappingService)
    svc.mapping_repository = MagicMock()
    svc.mapping_repository.list_for_guild = AsyncMock(return_value=[])
    svc.mapping_repository.get_match = AsyncMock(return_value=None)
    svc.mapping_repository.get_by_id = AsyncMock(return_value=None)
    svc.mapping_repository.create = AsyncMock()
    svc.mapping_repository.delete = AsyncMock()
    svc.role_repository = MagicMock()
    svc.role_repository.add = AsyncMock()
    svc.role_repository.remove = AsyncMock()
    svc.role_repository.list_for_user_by_source = AsyncMock(return_value=[])
    svc.role_repository.list_for_user = AsyncMock(return_value=[])
    svc.grant_repository = MagicMock()
    svc.grant_repository.list_for_user = AsyncMock(return_value=[])
    svc.grant_repository.add = AsyncMock()
    svc.grant_repository.remove = AsyncMock()
    svc.tournament_repository = MagicMock()
    svc.tournament_repository.get_by_id = AsyncMock(return_value=None)
    svc.tournament_repository.has_tournament_grant = AsyncMock(return_value=False)
    svc.tournament_repository.set_tournament_grant = AsyncMock()
    svc.sync_repository = MagicMock()
    svc.sync_repository.rule_role_ids = AsyncMock(return_value=set())
    svc.audit_service = make_audit_double()
    return svc


EVENT = 10


def grant(role, source=RoleSource.DISCORD):
    return SimpleNamespace(role=role, source=source, event_id=EVENT if role.is_per_event else None)


def make_user(user_id=1, discord_id=123):
    return SimpleNamespace(id=user_id, discord_id=discord_id)


def mapping(discord_role_id, app_role):
    return SimpleNamespace(
        discord_role_id=discord_role_id, app_role=app_role,
        tournament_grant=None, tournament_id=None, tournament=None,
        event_id=EVENT if app_role.is_per_event else None,
    )


def tournament_mapping(discord_role_id, grant, tournament_id=7, is_active=True):
    return SimpleNamespace(
        discord_role_id=discord_role_id, app_role=None,
        tournament_grant=grant, tournament_id=tournament_id,
        tournament=SimpleNamespace(id=tournament_id, is_active=is_active),
    )


@pytest.fixture
def fake_events(monkeypatch):
    """Unit tests without a database: one writable event, ``EVENT``."""
    monkeypatch.setattr(
        DiscordRoleMappingService, '_writable_event_ids', AsyncMock(return_value={EVENT}),
    )
    monkeypatch.setattr(
        DiscordRoleMappingService, '_mapping_event_id',
        AsyncMock(side_effect=lambda role, event_id: (event_id or EVENT) if role is not None and role.is_per_event else None),
    )


@pytest.fixture
def patch_deps(monkeypatch, fake_events):
    """Helper to configure the module-level dependencies of sync_user_roles."""

    def _apply(*, guild_id=42, member_result=(True, set()), current_roles=None):
        # The guild id now lives on the tenant; login sync fans out over every
        # tenant that has one. No tenant with a guild -> "no_guild_configured".
        if current_roles is None:
            current_roles = set()
        tenants = [] if guild_id is None else [SimpleNamespace(id=1, discord_guild_id=guild_id)]
        monkeypatch.setattr(
            drms.TenantService, 'list_tenants', AsyncMock(return_value=tenants),
        )
        monkeypatch.setattr(
            drms.AuthService, 'get_roles', AsyncMock(return_value=set(current_roles)),
        )
        fake_discord = SimpleNamespace(
            get_member_role_ids=AsyncMock(return_value=member_result)
        )
        monkeypatch.setattr(drms, 'DiscordService', lambda: fake_discord)
        # "A role implies membership" writes a real row; this suite has no DB.
        membership = AsyncMock(return_value=None)
        monkeypatch.setattr(
            drms.TenantMembershipService, 'ensure_member', membership,
        )
        return membership

    return _apply


class TestSyncUserRoles:
    async def test_grants_mapped_role(self, patch_deps):
        membership = patch_deps(member_result=(True, {111}), current_roles=set())
        svc = make_service()
        svc.mapping_repository.list_for_guild = AsyncMock(
            return_value=[mapping(111, Role.PROCTOR)]
        )
        user = make_user()

        summary = await svc.sync_user_roles(user)

        svc.role_repository.add.assert_awaited_once_with(
            user, Role.PROCTOR, granted_by=None, source=RoleSource.DISCORD, event_id=EVENT,
        )
        # A role in a tenant implies membership in it — the sync is a role-grant
        # path like any other.
        membership.assert_awaited_once_with(user, source=MembershipSource.DISCORD_ROLE)
        svc.role_repository.remove.assert_not_awaited()
        assert summary['granted'] == ['proctor']
        assert summary['revoked'] == []
        action = svc.audit_service.write_log.await_args.args[1]
        assert action == 'role.discord_sync_granted'

    async def test_revokes_discord_role_when_absent(self, patch_deps):
        # Member no longer has the Discord role; mapping still exists.
        patch_deps(member_result=(True, set()), current_roles={Role.PROCTOR})
        svc = make_service()
        svc.mapping_repository.list_for_guild = AsyncMock(
            return_value=[mapping(111, Role.PROCTOR)]
        )
        svc.role_repository.list_for_user = AsyncMock(return_value=[grant(Role.PROCTOR)])
        user = make_user()

        summary = await svc.sync_user_roles(user)

        svc.role_repository.remove.assert_awaited_once_with(user, Role.PROCTOR, event_id=EVENT)
        svc.role_repository.add.assert_not_awaited()
        assert summary['revoked'] == ['proctor']
        action = svc.audit_service.write_log.await_args.args[1]
        assert action == 'role.discord_sync_revoked'

    async def test_manual_role_never_revoked(self, patch_deps):
        # User holds STAFF manually; it is absent from Discord and from the
        # discord-sourced rows, so it must be left untouched.
        patch_deps(member_result=(True, set()), current_roles={Role.STAFF})
        svc = make_service()
        svc.mapping_repository.list_for_guild = AsyncMock(
            return_value=[mapping(999, Role.STAFF)]
        )
        svc.role_repository.list_for_user = AsyncMock(return_value=[grant(Role.STAFF, RoleSource.MANUAL)])
        user = make_user()

        summary = await svc.sync_user_roles(user)

        svc.role_repository.remove.assert_not_awaited()
        svc.role_repository.add.assert_not_awaited()
        assert summary['revoked'] == []
        assert summary['granted'] == []

    async def test_no_guild_configured_is_noop(self, patch_deps):
        patch_deps(guild_id=None)
        svc = make_service()
        user = make_user()

        summary = await svc.sync_user_roles(user)

        assert summary['skipped'] == 'no_guild_configured'
        svc.role_repository.add.assert_not_awaited()
        svc.role_repository.remove.assert_not_awaited()

    async def test_discord_unavailable_fails_open(self, patch_deps):
        patch_deps(member_result=(False, 'bot not ready'), current_roles={Role.PROCTOR})
        svc = make_service()
        svc.mapping_repository.list_for_guild = AsyncMock(
            return_value=[mapping(111, Role.PROCTOR)]
        )
        svc.role_repository.list_for_user = AsyncMock(return_value=[grant(Role.PROCTOR)])
        user = make_user()

        summary = await svc.sync_user_roles(user)

        assert summary['skipped'] == 'discord_unavailable'
        svc.role_repository.add.assert_not_awaited()
        svc.role_repository.remove.assert_not_awaited()

    async def test_already_held_role_not_regranted(self, patch_deps):
        patch_deps(member_result=(True, {111}), current_roles={Role.PROCTOR})
        svc = make_service()
        svc.mapping_repository.list_for_guild = AsyncMock(
            return_value=[mapping(111, Role.PROCTOR)]
        )
        svc.role_repository.list_for_user = AsyncMock(return_value=[grant(Role.PROCTOR)])
        user = make_user()

        summary = await svc.sync_user_roles(user)

        svc.role_repository.add.assert_not_awaited()
        svc.role_repository.remove.assert_not_awaited()
        assert summary == {
            'granted': [], 'revoked': [],
            'tournament_granted': [], 'tournament_revoked': [],
            'skipped': None,
        }


class TestSyncTournamentGrants:
    async def test_grants_mapped_tournament_admin(self, patch_deps):
        membership = patch_deps(member_result=(True, {222}))
        svc = make_service()
        svc.mapping_repository.list_for_guild = AsyncMock(
            return_value=[tournament_mapping(222, TournamentGrant.TOURNAMENT_ADMIN)]
        )
        tournament = SimpleNamespace(id=7, is_active=True)
        svc.tournament_repository.get_by_id = AsyncMock(return_value=tournament)
        user = make_user()

        summary = await svc.sync_user_roles(user)

        svc.tournament_repository.set_tournament_grant.assert_awaited_once_with(
            tournament, user, TournamentGrant.TOURNAMENT_ADMIN, granted=True,
        )
        svc.grant_repository.add.assert_awaited_once_with(
            user, tournament, TournamentGrant.TOURNAMENT_ADMIN,
        )
        membership.assert_awaited_once_with(user, source=MembershipSource.DISCORD_ROLE)
        assert summary['tournament_granted'] == ['tournament_admin:7']
        action = svc.audit_service.write_log.await_args.args[1]
        assert action == 'tournament.admin_granted'

    async def test_hand_made_grant_is_not_claimed_by_the_sync(self, patch_deps):
        # Already a Tournament Admin with no provenance row: staff granted it.
        # Recording provenance now would let a later sync revoke their grant.
        patch_deps(member_result=(True, {222}))
        svc = make_service()
        svc.mapping_repository.list_for_guild = AsyncMock(
            return_value=[tournament_mapping(222, TournamentGrant.TOURNAMENT_ADMIN)]
        )
        svc.tournament_repository.has_tournament_grant = AsyncMock(return_value=True)

        summary = await svc.sync_user_roles(make_user())

        svc.grant_repository.add.assert_not_awaited()
        svc.tournament_repository.set_tournament_grant.assert_not_awaited()
        assert summary['tournament_granted'] == []

    async def test_revokes_when_the_discord_role_is_gone(self, patch_deps):
        patch_deps(member_result=(True, set()))
        svc = make_service()
        svc.mapping_repository.list_for_guild = AsyncMock(
            return_value=[tournament_mapping(222, TournamentGrant.CREW_COORDINATOR)]
        )
        tournament = SimpleNamespace(id=7, is_active=True)
        svc.tournament_repository.get_by_id = AsyncMock(return_value=tournament)
        svc.grant_repository.list_for_user = AsyncMock(return_value=[
            SimpleNamespace(tournament_id=7, grant=TournamentGrant.CREW_COORDINATOR),
        ])
        user = make_user()

        summary = await svc.sync_user_roles(user)

        svc.tournament_repository.set_tournament_grant.assert_awaited_once_with(
            tournament, user, TournamentGrant.CREW_COORDINATOR, granted=False,
        )
        svc.grant_repository.remove.assert_awaited_once_with(
            user, 7, TournamentGrant.CREW_COORDINATOR,
        )
        assert summary['tournament_revoked'] == ['crew_coordinator:7']
        action = svc.audit_service.write_log.await_args.args[1]
        assert action == 'tournament.crew_coordinator_revoked'

    async def test_inactive_tournament_revokes_rather_than_grants(self, patch_deps):
        # The guild role is still held; the event is over. Authority ends with it.
        patch_deps(member_result=(True, {222}))
        svc = make_service()
        svc.mapping_repository.list_for_guild = AsyncMock(
            return_value=[
                tournament_mapping(222, TournamentGrant.TOURNAMENT_ADMIN, is_active=False)
            ]
        )
        svc.tournament_repository.get_by_id = AsyncMock(
            return_value=SimpleNamespace(id=7, is_active=False)
        )
        svc.grant_repository.list_for_user = AsyncMock(return_value=[
            SimpleNamespace(tournament_id=7, grant=TournamentGrant.TOURNAMENT_ADMIN),
        ])

        summary = await svc.sync_user_roles(make_user())

        assert summary['tournament_granted'] == []
        assert summary['tournament_revoked'] == ['tournament_admin:7']

    async def test_role_mappings_do_not_touch_tournament_grants(self, patch_deps):
        patch_deps(member_result=(True, {111}))
        svc = make_service()
        svc.mapping_repository.list_for_guild = AsyncMock(
            return_value=[mapping(111, Role.PROCTOR)]
        )

        summary = await svc.sync_user_roles(make_user())

        svc.grant_repository.add.assert_not_awaited()
        svc.tournament_repository.set_tournament_grant.assert_not_awaited()
        assert summary['granted'] == ['proctor']
        assert summary['tournament_granted'] == []


@pytest.mark.usefixtures('fake_events')
class TestMappingCrud:
    async def test_add_mapping_creates_and_audits(self):
        svc = make_service()
        actor = make_user()

        await svc.add_mapping(
            guild_id=42, discord_role_id=111, discord_role_name='Mods',
            app_role=Role.PROCTOR, actor=actor,
        )

        svc.mapping_repository.create.assert_awaited_once_with(
            42, 111, 'Mods', app_role=Role.PROCTOR, tournament_grant=None, tournament_id=None,
            event_id=EVENT,
        )
        action = svc.audit_service.write_log.await_args.args[1]
        assert action == 'discord_role.mapping_added'

    async def test_add_tournament_mapping_creates_and_audits(self):
        svc = make_service()
        svc.tournament_repository.get_by_id = AsyncMock(
            return_value=SimpleNamespace(id=7, is_active=True)
        )
        svc.mapping_repository.get_tournament_match = AsyncMock(return_value=None)

        await svc.add_mapping(
            guild_id=42, discord_role_id=222, discord_role_name='Event Admins',
            tournament_grant=TournamentGrant.TOURNAMENT_ADMIN, tournament_id=7,
            actor=make_user(),
        )

        svc.mapping_repository.create.assert_awaited_once_with(
            42, 222, 'Event Admins', app_role=None,
            tournament_grant=TournamentGrant.TOURNAMENT_ADMIN, tournament_id=7, event_id=None,
        )
        details = svc.audit_service.write_log.await_args.args[2]
        assert details['tournament_id'] == 7

    async def test_add_mapping_rejects_both_kinds_at_once(self):
        svc = make_service()
        with pytest.raises(ValueError):
            await svc.add_mapping(
                guild_id=42, discord_role_id=222, discord_role_name='Mods',
                app_role=Role.PROCTOR,
                tournament_grant=TournamentGrant.TOURNAMENT_ADMIN, tournament_id=7,
                actor=make_user(),
            )
        svc.mapping_repository.create.assert_not_awaited()

    async def test_add_mapping_rejects_neither_kind(self):
        svc = make_service()
        with pytest.raises(ValueError):
            await svc.add_mapping(
                guild_id=42, discord_role_id=222, discord_role_name='Mods',
                actor=make_user(),
            )
        svc.mapping_repository.create.assert_not_awaited()

    async def test_tournament_grant_requires_a_tournament(self):
        # A guild role is guild-wide, so there is nothing to scope it to without one.
        svc = make_service()
        with pytest.raises(ValueError):
            await svc.add_mapping(
                guild_id=42, discord_role_id=222, discord_role_name='Event Admins',
                tournament_grant=TournamentGrant.CREW_COORDINATOR,
                actor=make_user(),
            )
        svc.mapping_repository.create.assert_not_awaited()

    async def test_app_role_rejects_a_tournament(self):
        svc = make_service()
        with pytest.raises(ValueError):
            await svc.add_mapping(
                guild_id=42, discord_role_id=111, discord_role_name='Mods',
                app_role=Role.PROCTOR, tournament_id=7, actor=make_user(),
            )
        svc.mapping_repository.create.assert_not_awaited()

    async def test_tournament_from_another_tenant_is_not_found(self):
        svc = make_service()
        svc.tournament_repository.get_by_id = AsyncMock(return_value=None)
        with pytest.raises(ValueError):
            await svc.add_mapping(
                guild_id=42, discord_role_id=222, discord_role_name='Event Admins',
                tournament_grant=TournamentGrant.TOURNAMENT_ADMIN, tournament_id=999,
                actor=make_user(),
            )
        svc.mapping_repository.create.assert_not_awaited()

    async def test_add_mapping_rejects_duplicate(self):
        svc = make_service()
        svc.mapping_repository.get_match = AsyncMock(return_value=SimpleNamespace(id=1))

        with pytest.raises(ValueError):
            await svc.add_mapping(
                guild_id=42, discord_role_id=111, discord_role_name='Mods',
                app_role=Role.PROCTOR, actor=make_user(),
            )
        svc.mapping_repository.create.assert_not_awaited()

    async def test_remove_mapping_deletes_and_audits(self):
        svc = make_service()
        existing = SimpleNamespace(
            id=5, guild_id=42, discord_role_id=111,
            discord_role_name='Mods', app_role=Role.PROCTOR,
        )
        svc.mapping_repository.get_by_id = AsyncMock(return_value=existing)

        await svc.remove_mapping(5, actor=make_user())

        svc.mapping_repository.delete.assert_awaited_once_with(existing)
        action = svc.audit_service.write_log.await_args.args[1]
        assert action == 'discord_role.mapping_removed'

    async def test_remove_mapping_missing_raises(self):
        svc = make_service()
        svc.mapping_repository.get_by_id = AsyncMock(return_value=None)

        with pytest.raises(ValueError):
            await svc.remove_mapping(999, actor=make_user())
        svc.mapping_repository.delete.assert_not_awaited()

    async def test_add_mappings_creates_every_pair_and_skips_existing(self):
        svc = make_service()
        svc.tournament_repository.get_by_id = AsyncMock(return_value=SimpleNamespace(id=7))
        svc.mapping_repository.get_tournament_match = AsyncMock(return_value=None)

        async def get_match(_guild, role_id, app_role, _event_id=None):
            return SimpleNamespace(id=1) if (role_id, app_role) == (111, Role.PROCTOR) else None
        svc.mapping_repository.get_match = AsyncMock(side_effect=get_match)

        created, skipped = await svc.add_mappings(
            guild_id=42,
            discord_roles=[(111, 'Mods'), (222, 'Helpers')],
            app_roles=[Role.PROCTOR, Role.VOLUNTEER],
            tournament_grants=[TournamentGrant.TOURNAMENT_ADMIN],
            tournament_id=7,
            actor=make_user(),
        )

        assert (created, skipped) == (5, 1)
        assert svc.mapping_repository.create.await_count == 5
        assert svc.audit_service.write_log.await_count == 5
        tournament_calls = [
            c for c in svc.mapping_repository.create.await_args_list
            if c.kwargs['tournament_grant'] is not None
        ]
        assert {c.kwargs['tournament_id'] for c in tournament_calls} == {7}
        assert all(
            c.kwargs['tournament_id'] is None
            for c in svc.mapping_repository.create.await_args_list
            if c.kwargs['app_role'] is not None
        )

    async def test_add_mappings_checks_everything_before_writing(self):
        # SUPER_ADMIN last in the list: the first role must not land before the
        # bad one is noticed.
        svc = make_service()
        with pytest.raises(ValueError):
            await svc.add_mappings(
                guild_id=42, discord_roles=[(111, 'Mods')],
                app_roles=[Role.PROCTOR, Role.SUPER_ADMIN], actor=make_user(),
            )
        svc.mapping_repository.create.assert_not_awaited()

    async def test_add_mappings_tournament_grant_needs_a_real_tournament(self):
        svc = make_service()
        with pytest.raises(ValueError):
            await svc.add_mappings(
                guild_id=42, discord_roles=[(111, 'Mods')],
                app_roles=[Role.PROCTOR],
                tournament_grants=[TournamentGrant.CREW_COORDINATOR], tournament_id=999,
                actor=make_user(),
            )
        svc.mapping_repository.create.assert_not_awaited()

    async def test_add_mappings_requires_both_sides(self):
        svc = make_service()
        with pytest.raises(ValueError):
            await svc.add_mappings(guild_id=42, discord_roles=[], app_roles=[Role.PROCTOR], actor=make_user())
        with pytest.raises(ValueError):
            await svc.add_mappings(guild_id=42, discord_roles=[(111, 'Mods')], actor=make_user())

    async def test_remove_mappings_skips_ids_already_gone(self):
        svc = make_service()
        rows = {
            5: SimpleNamespace(id=5, guild_id=42, discord_role_id=111,
                               discord_role_name='Mods', app_role=Role.PROCTOR),
            6: SimpleNamespace(id=6, guild_id=42, discord_role_id=222,
                               discord_role_name='Helpers', app_role=Role.VOLUNTEER),
        }
        svc.mapping_repository.get_by_id = AsyncMock(side_effect=rows.get)

        removed = await svc.remove_mappings([5, 99, 6], actor=make_user())

        assert removed == 2
        assert svc.mapping_repository.delete.await_count == 2
        assert svc.audit_service.write_log.await_count == 2

    async def test_non_staff_cannot_add_mapping(self, monkeypatch):
        async def deny(*_a, **_kw):
            return False

        async def real_ensure(allowed, message=None):
            if not allowed:
                raise PermissionError(message or 'denied')

        monkeypatch.setattr(drms.AuthService, 'can_grant_roles', deny)
        monkeypatch.setattr(drms.AuthService, 'ensure', real_ensure)

        svc = make_service()
        with pytest.raises(PermissionError):
            await svc.add_mapping(
                guild_id=42, discord_role_id=111, discord_role_name='Mods',
                app_role=Role.PROCTOR, actor=make_user(),
            )


class TestSyncAllUsers:
    async def test_aggregates_names_and_audits(self, monkeypatch):
        svc = make_service()
        member = SimpleNamespace(id=1, discord_id=100, display_name='Member', username='m')
        holder = SimpleNamespace(id=2, discord_id=200, display_name=None, username='holder')
        newbie = SimpleNamespace(id=3, discord_id=300, display_name=None, username='newbie')
        # The holder is also listed as a member: synced once, not twice.
        svc._provision_current_guild_members = AsyncMock(
            return_value=([newbie], [holder, newbie]),
        )
        monkeypatch.setattr(
            drms.TenantMembershipService, 'list_members',
            AsyncMock(return_value=[member, holder]),
        )
        tenant = SimpleNamespace(id=1, discord_guild_id=42)
        monkeypatch.setattr(drms, 'get_current_tenant_id', lambda: 1)
        monkeypatch.setattr(drms.TenantService, 'get_by_id', AsyncMock(return_value=tenant))
        svc.sync_user_roles_for_tenant = AsyncMock(side_effect=[
            {'granted': ['proctor'], 'revoked': [], 'skipped': None},
            {'granted': [], 'revoked': ['proctor', 'staff'], 'skipped': None},
            {'granted': [], 'revoked': [], 'skipped': 'discord_unavailable'},
        ])

        summary = await svc.sync_all_users(actor=make_user())

        assert summary == {
            'users_processed': 3, 'users_created': 1,
            'granted': 1, 'revoked': 2, 'skipped': 1,
            'created': ['newbie'],
            'changed': [
                {'name': 'Member', 'granted': ['proctor'], 'revoked': []},
                {'name': 'holder', 'granted': [], 'revoked': ['proctor', 'staff']},
            ],
        }
        # Only this community: the per-tenant sync, with this tenant.
        assert svc.sync_user_roles_for_tenant.await_count == 3
        assert all(c.args[1] is tenant for c in svc.sync_user_roles_for_tenant.await_args_list)
        action, details = svc.audit_service.write_log.await_args.args[1:3]
        assert action == 'role.discord_sync_bulk'
        assert details['created_user_ids'] == [3]
        assert details['changed_user_ids'] == [1, 2]

    async def test_non_staff_cannot_sync(self, monkeypatch):
        async def deny(*_a, **_kw):
            return False

        async def real_ensure(allowed, message=None):
            if not allowed:
                raise PermissionError(message or 'denied')

        monkeypatch.setattr(drms.AuthService, 'can_grant_roles', deny)
        monkeypatch.setattr(drms.AuthService, 'ensure', real_ensure)
        svc = make_service()
        svc._provision_current_guild_members = AsyncMock(return_value=([], []))
        with pytest.raises(PermissionError):
            await svc.sync_all_users(actor=make_user())
        svc._provision_current_guild_members.assert_not_awaited()


def guild_member(member_id=900, role_ids=(111,)):
    return GuildMember(
        id=member_id, username='guildie', avatar='hash', role_ids=frozenset(role_ids),
    )


class TestProvisionMember:
    TENANT = SimpleNamespace(id=1, discord_guild_id=42)

    @pytest.fixture
    def provision(self, monkeypatch):
        spy = AsyncMock(side_effect=lambda did, *_a: (SimpleNamespace(id=5, discord_id=did), True))
        monkeypatch.setattr(drms.UserService, 'provision_from_discord_role_sync', spy)
        return spy

    async def test_mapped_role_creates_the_account(self, provision):
        svc = make_service()
        svc.mapping_repository.list_for_guild = AsyncMock(return_value=[mapping(111, Role.PROCTOR)])

        user = await svc.provision_member([self.TENANT], guild_member())

        assert user.discord_id == 900
        provision.assert_awaited_once_with(900, 'guildie', 'hash')

    async def test_unmapped_role_creates_nothing(self, provision):
        svc = make_service()
        svc.mapping_repository.list_for_guild = AsyncMock(return_value=[mapping(111, Role.PROCTOR)])

        assert await svc.provision_member([self.TENANT], guild_member(role_ids={222})) is None
        provision.assert_not_awaited()

    async def test_mapping_the_sync_would_ignore_creates_nothing(self, provision):
        # A platform role, or a grant on an ended tournament, confers nothing on
        # sync, so it must not conjure an account either.
        svc = make_service()
        svc.mapping_repository.list_for_guild = AsyncMock(return_value=[
            mapping(111, Role.SUPER_ADMIN),
            tournament_mapping(112, TournamentGrant.TOURNAMENT_ADMIN, is_active=False),
        ])

        member = guild_member(role_ids={111, 112})
        assert await svc.provision_member([self.TENANT], member) is None
        provision.assert_not_awaited()

    async def test_active_tournament_grant_creates_the_account(self, provision):
        svc = make_service()
        svc.mapping_repository.list_for_guild = AsyncMock(return_value=[
            tournament_mapping(112, TournamentGrant.CREW_COORDINATOR),
        ])

        assert await svc.provision_member([self.TENANT], guild_member(role_ids={112}))
        provision.assert_awaited_once()


class TestSyncIntegration:
    async def test_full_sync_against_real_db(self, db, monkeypatch):
        from application.repositories.discord_role_mapping_repository import (
            DiscordRoleMappingRepository,
        )
        from application.repositories.user_role_repository import UserRoleRepository
        from models import Tenant, User, UserRole

        guild_id = 42
        default = await Tenant.get(id=1)
        default.discord_guild_id = guild_id
        await default.save()
        user = await User.create(discord_id=555, username='bob')
        await DiscordRoleMappingRepository.create(guild_id, 111, 'Mods', Role.PROCTOR, event_id=DEFAULT_TEST_EVENT_ID)
        # A manually-granted role that must survive the sync.
        await UserRoleRepository.add(user, Role.STAFF, source=RoleSource.MANUAL)

        member_roles = {111}
        fake = SimpleNamespace(
            get_member_role_ids=AsyncMock(side_effect=lambda g, u: (True, set(member_roles)))
        )
        monkeypatch.setattr(drms, 'DiscordService', lambda: fake)

        svc = DiscordRoleMappingService()

        # 1) Member holds the mapped Discord role -> PROCTOR granted as `discord`.
        summary = await svc.sync_user_roles(user)
        assert summary['granted'] == ['proctor']
        proctor = await UserRole.get(user=user, role=Role.PROCTOR)
        assert proctor.source == RoleSource.DISCORD

        # 2) Member loses the Discord role -> PROCTOR revoked, STAFF (manual) kept.
        member_roles.clear()
        summary = await svc.sync_user_roles(user)
        assert summary['revoked'] == ['proctor']
        assert not await UserRole.filter(user=user, role=Role.PROCTOR).exists()
        assert await UserRole.filter(user=user, role=Role.STAFF).exists()

    async def test_sync_all_creates_accounts_for_mapped_role_holders(self, db, monkeypatch):
        from application.repositories.discord_role_mapping_repository import (
            DiscordRoleMappingRepository,
        )
        from models import AuditLog, Tenant, TenantMembership, User, UserRole

        guild_id = 42
        default = await Tenant.get(id=1)
        default.discord_guild_id = guild_id
        await default.save()
        await DiscordRoleMappingRepository.create(guild_id, 111, 'Mods', Role.PROCTOR, event_id=DEFAULT_TEST_EVENT_ID)
        staff = await User.create(discord_id=1, username='staff')
        existing = await User.create(discord_id=556, username='chosen_name')

        roster = [
            GuildMember(id=555, username='newcomer', avatar='abc', role_ids=frozenset({111})),
            GuildMember(id=556, username='guild_name', avatar=None, role_ids=frozenset({111})),
        ]
        list_members = AsyncMock(return_value=(True, roster))
        fake = SimpleNamespace(
            list_members_with_roles=list_members,
            get_member_role_ids=AsyncMock(side_effect=lambda g, u: (
                (True, {111}) if u in (555, 556) else (True, set())
            )),
        )
        monkeypatch.setattr(drms, 'DiscordService', lambda: fake)

        summary = await DiscordRoleMappingService().sync_all_users(actor=staff)

        assert summary['users_created'] == 1
        list_members.assert_awaited_once_with(guild_id, {111})
        newcomer = await User.get(discord_id=555)
        assert (newcomer.username, newcomer.discord_avatar) == ('newcomer', 'abc')
        assert await UserRole.filter(user=newcomer, role=Role.PROCTOR).exists()
        assert await TenantMembership.filter(user=newcomer, tenant_id=1).exists()
        audit = await AuditLog.get(action='user.provisioned', user=newcomer)
        assert json.loads(audit.details)['source'] == 'discord_role_sync'
        # An existing account keeps its name; sync only hands it the role.
        await existing.refresh_from_db()
        assert existing.username == 'chosen_name'
        assert await UserRole.filter(user=existing, role=Role.PROCTOR).exists()

    async def test_sync_all_survives_an_unreachable_guild(self, db, monkeypatch):
        from application.repositories.discord_role_mapping_repository import (
            DiscordRoleMappingRepository,
        )
        from models import Tenant, User

        default = await Tenant.get(id=1)
        default.discord_guild_id = 42
        await default.save()
        await DiscordRoleMappingRepository.create(42, 111, 'Mods', Role.PROCTOR)
        staff = await User.create(discord_id=1, username='staff')
        fake = SimpleNamespace(
            list_members_with_roles=AsyncMock(return_value=(False, 'not connected')),
            get_member_role_ids=AsyncMock(return_value=(False, 'not connected')),
        )
        monkeypatch.setattr(drms, 'DiscordService', lambda: fake)

        summary = await DiscordRoleMappingService().sync_all_users(actor=staff)

        assert summary['users_created'] == 0
        assert await User.all().count() == 1
