"""Membership is real: the invariant, the community user list, and the pickers.

The invariant is *"holding any role in a tenant implies membership in that
tenant"*. Membership was a frozen backfill until this wave — the migration wrote
a row per existing user and nothing wrote one since, so a staff member granted
through the Users tab was not a member of the community they administered.
Scoping a picker on that hides a community's own staff from its own selects;
gating on it locks them out entirely.
"""

import ast
from pathlib import Path

import pytest

from application.services.tenant_membership_service import TenantMembershipService
from application.services.user_service import UserService
from application.tenant_context import tenant_scope
from models import Role, TenantMembership, User, UserRole

REPO = Path(__file__).resolve().parents[2]


async def _staff(discord_id=6000):
    boss = await User.create(discord_id=discord_id, username=f'boss{discord_id}')
    await UserRole.create(user=boss, role=Role.STAFF, tenant_id=1)
    await TenantMembership.create(user=boss, tenant_id=1)
    return boss


async def test_no_tenant_scoped_role_exists_without_a_membership(db):
    """Grant roles by every service path this suite can reach; the join is empty.

    This is the test that catches the *next* role-granting path someone adds.
    """
    boss = await _staff()
    service = UserService()
    granted = await User.create(discord_id=6001, username='granted')
    created = await service.create_user(username='fresh', actor=boss)

    await service.grant_role(granted, Role.PROCTOR, boss)
    await service.grant_role(granted, Role.STREAM_MANAGER, boss)

    memberships = {
        (m.user_id, m.tenant_id) for m in await TenantMembership.all()
    }
    gaps = [
        (r.user_id, r.tenant_id)
        for r in await UserRole.filter(tenant_id__not_isnull=True)
        if (r.user_id, r.tenant_id) not in memberships
    ]
    assert gaps == []
    assert (created.id, 1) in memberships


async def test_super_admin_grant_makes_nobody_a_member(db):
    from application.services.tenant_service import TenantService

    root = await User.create(discord_id=6002, username='root')
    await UserRole.create(user=root, role=Role.SUPER_ADMIN, tenant=None)
    target = await User.create(discord_id=6003, username='future_root')

    await TenantService.grant_super_admin(root, target)
    assert await TenantMembership.filter(user=target).count() == 0


async def test_a_role_in_one_tenant_does_not_make_a_member_of_another(two_tenants, db):
    tenant_a, tenant_b = two_tenants
    boss = await _staff(6004)
    target = await User.create(discord_id=6005, username='target')
    with tenant_scope(tenant_a.id):
        await UserService().grant_role(target, Role.PROCTOR, boss)

    assert await TenantMembership.filter(user=target, tenant_id=tenant_a.id).count() == 1
    assert await TenantMembership.filter(user=target, tenant_id=tenant_b.id).count() == 0


async def test_community_user_list_never_crosses_tenants(two_tenants, db):
    tenant_a, tenant_b = two_tenants
    here = await User.create(discord_id=6006, username='here')
    there = await User.create(discord_id=6007, username='there')
    await TenantMembership.create(user=here, tenant=tenant_a)
    await TenantMembership.create(user=there, tenant=tenant_b)

    with tenant_scope(tenant_a.id):
        names_a = {u.username for u in await UserService().get_community_people()}
    with tenant_scope(tenant_b.id):
        names_b = {u.username for u in await UserService().get_community_people()}

    assert 'here' in names_a and 'there' not in names_a
    assert 'there' in names_b and 'here' not in names_b


async def test_get_community_people_excludes_non_members(db):
    member = await User.create(discord_id=6008, username='member')
    await TenantMembership.create(user=member, tenant_id=1)
    await User.create(discord_id=6009, username='stranger')

    names = {u.username for u in await UserService().get_community_people()}
    assert names == {'member'}


async def test_get_community_people_honours_the_role_filter(db):
    boss = await _staff(6010)
    plain = await User.create(discord_id=6011, username='plain')
    await TenantMembership.create(user=plain, tenant_id=1)

    staffers = await UserService().get_community_people(role=Role.STAFF)
    assert [u.id for u in staffers] == [boss.id]


async def test_the_role_filter_does_not_admit_another_tenants_grant(two_tenants, db):
    tenant_a, tenant_b = two_tenants
    person = await User.create(discord_id=6012, username='person')
    await TenantMembership.create(user=person, tenant=tenant_a)
    await TenantMembership.create(user=person, tenant=tenant_b)
    with tenant_scope(tenant_b.id):
        await UserRole.create(user=person, role=Role.PROCTOR, tenant=tenant_b)

    with tenant_scope(tenant_a.id):
        assert await UserService().get_community_people(role=Role.PROCTOR) == []


async def test_get_community_people_excludes_the_system_actor(db):
    from application.repositories.user_repository import UserRepository

    system = await UserRepository.get_or_create_system_user()
    await TenantMembership.create(user=system, tenant_id=1)
    member = await User.create(discord_id=6013, username='member')
    await TenantMembership.create(user=member, tenant_id=1)

    names = {u.username for u in await UserService().get_community_people()}
    assert names == {'member'}


async def test_get_community_people_raises_without_a_tenant(db):
    from application.tenant_context import reset_tenant_id, set_tenant_id

    token = set_tenant_id(None)
    try:
        with pytest.raises(RuntimeError):
            await UserService().get_community_people()
    finally:
        reset_tenant_id(token)


async def test_get_all_users_is_unchanged(db):
    """The regression guard for design decision 7.

    ``get_all_users`` still answers "every account on the platform" — the
    ``/platform`` first-admin picker depends on it, and silently narrowing it is
    how a leak becomes a lockout.
    """
    await User.create(discord_id=6014, username='member2')
    await User.create(discord_id=6015, username='stranger2')
    names = {u.username for u in await UserService().get_all_users()}
    assert {'member2', 'stranger2'} <= names


_GLOBAL_LIST_ALLOWLIST = {
    'pages/platform_tenant_admins.py',
    'theme/dialog/checkout_dialog.py',
}

_PICKER_DIRS = ['pages', 'theme', 'api', 'mcpserver']


def _calls_get_all_users(path: Path) -> bool:
    tree = ast.parse(path.read_text())
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == 'get_all_users'
        for node in ast.walk(tree)
    )


def test_every_person_picker_is_member_scoped():
    """No new picker may be written against the global user list.

    Cheaper than a test per dialog, and it is what stops the eighth picker from
    re-opening the leak this wave closed.
    """
    offenders = []
    for directory in _PICKER_DIRS:
        for path in sorted((REPO / directory).rglob('*.py')):
            rel = path.relative_to(REPO).as_posix()
            if rel in _GLOBAL_LIST_ALLOWLIST:
                continue
            if _calls_get_all_users(path):
                offenders.append(rel)
    assert offenders == [], (
        f'These call UserService.get_all_users (every account on the platform): '
        f'{offenders}. Per-community pickers want get_community_people; if this '
        f'one really is platform-level, add it to _GLOBAL_LIST_ALLOWLIST with a '
        f'reason.'
    )


def test_the_allowlisted_caller_still_exists():
    """A stale allowlist entry hides the rule it is exempting."""
    for rel in _GLOBAL_LIST_ALLOWLIST:
        path = REPO / rel
        assert path.exists(), f'{rel} is allowlisted but gone'
        assert _calls_get_all_users(path), f'{rel} no longer calls get_all_users'


async def test_membership_is_what_the_picker_offers(two_tenants, db):
    """End to end for the converted pickers: the service they now call.

    Their own rendering needs a live NiceGUI client, so this pins the data they
    are handed — including the case the invariant exists for, a community's own
    staff appearing in its own selects.
    """
    tenant_a, _ = two_tenants
    boss = await _staff(6016)
    outsider = await User.create(discord_id=6017, username='outsider')

    offered = {u.username for u in await UserService().get_community_people()}
    assert boss.username in offered
    assert outsider.username not in offered

    await TenantMembershipService().add_member(boss, outsider)
    offered = {u.username for u in await UserService().get_community_people()}
    assert outsider.username in offered


async def test_the_seed_has_a_member_of_one_community_and_not_another(seeded_db):
    """The fixture the scoped pickers are reviewed against.

    Every other seeded user is a member everywhere, so without this one a
    reviewer opening the second community's pickers sees the same faces and
    cannot tell scoped from unscoped.
    """
    from models import Tenant
    default = await Tenant.get(slug='default')
    second = await Tenant.get(slug='second')

    with tenant_scope(default.id):
        in_default = {u.username for u in await UserService().get_community_people()}
    with tenant_scope(second.id):
        in_second = {u.username for u in await UserService().get_community_people()}

    assert 'local_only' in in_default
    assert 'local_only' not in in_second
    # Check the cast really is shared, or the assertion above proves nothing.
    assert 'staff_user' in in_default and 'staff_user' in in_second


async def test_an_imported_speedgaming_player_becomes_a_member(db):
    """Otherwise an SG-synced roster is invisible in its own community.

    The ETL creates placeholder accounts for entrants it cannot resolve to a
    Discord identity. Those are people on this community's schedule, so they have
    to join the community's person lists like anyone else.

    A placeholder is deliberately ``is_active=False`` — nobody signs in as one —
    so the picker that has to offer them is the match dialog's, which asks for
    inactive accounts too. Membership is what decides *which* community's dialog
    that is; without the row the placeholder is offered by every community or
    none, depending on which way the read is scoped.
    """
    from application.services.speedgaming_etl_service import SpeedGamingETLService

    actor = await User.create(discord_id=6100, username='sg_actor')
    service = SpeedGamingETLService()

    resolved = await service._find_or_create_user({'id': 'sg-42', 'displayName': 'Someone'}, actor=actor)
    assert resolved.is_placeholder is True
    assert await TenantMembership.filter(user=resolved, tenant_id=1).count() == 1

    service = UserService()
    assert resolved.username not in {u.username for u in await service.get_community_people()}
    offered = {u.username for u in await service.get_community_people(include_inactive=True)}
    assert resolved.username in offered


class TestJoinRequestIsolation:
    async def test_join_requests_do_not_cross_communities(self, two_tenants, db):
        tenant_a, tenant_b = two_tenants
        from models import TenantJoinRequest

        here = await User.create(discord_id=8314, username='here')
        there = await User.create(discord_id=8315, username='there')
        service = TenantMembershipService()
        await service.request_to_join(here, tenant_a.id)
        await service.request_to_join(there, tenant_b.id)

        with tenant_scope(tenant_a.id):
            assert [r.user.username for r in await service.list_pending()] == ['here']
        with tenant_scope(tenant_b.id):
            assert [r.user.username for r in await service.list_pending()] == ['there']
        assert await TenantJoinRequest.all().count() == 2

    async def test_approving_grants_membership_only_where_it_was_asked(
        self, two_tenants, db,
    ):
        tenant_a, tenant_b = two_tenants
        boss = await User.create(discord_id=8316, username='boss8316')
        await TenantMembership.create(user=boss, tenant=tenant_a)
        with tenant_scope(tenant_a.id):
            await UserRole.create(user=boss, role=Role.STAFF, tenant=tenant_a)
        hopeful = await User.create(discord_id=8317, username='hopeful')

        service = TenantMembershipService()
        request = await service.request_to_join(hopeful, tenant_a.id)
        with tenant_scope(tenant_a.id):
            await service.approve_request(boss, request.id)

        assert await TenantMembership.filter(user=hopeful, tenant=tenant_a).count() == 1
        assert await TenantMembership.filter(user=hopeful, tenant=tenant_b).count() == 0


async def test_memberships_by_user_is_this_communitys_only(two_tenants):
    from application.services.tenant_membership_service import TenantMembershipService
    from application.tenant_context import tenant_scope
    from models import TenantMembership
    from tests.factories import make_user

    a, b = two_tenants
    only_a = await make_user(discord_id=760, username='only-a')
    await TenantMembership.create(user=only_a, tenant=a)
    with tenant_scope(a.id):
        assert only_a.id in await TenantMembershipService().memberships_by_user()
    with tenant_scope(b.id):
        assert only_a.id not in await TenantMembershipService().memberships_by_user()


async def test_a_removal_block_holds_only_in_its_own_tenant(two_tenants, db):
    """A ``TenantMemberBlock`` from tenant A must not keep anyone out of tenant B."""
    from models import TenantMemberBlock

    tenant_a, tenant_b = two_tenants
    user = await User.create(discord_id=6100, username='removed-from-a')
    await TenantMemberBlock.create(user=user, tenant=tenant_a)
    service = TenantMembershipService()
    with tenant_scope(tenant_a.id):
        assert await service.was_removed(user, tenant_a.id) is True
    with tenant_scope(tenant_b.id):
        assert await service.was_removed(user, tenant_b.id) is False
