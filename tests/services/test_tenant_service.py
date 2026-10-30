"""Tests for TenantService (resolution, CRUD, super-admin grants) and the
per-tenant role semantics of AuthService/UserRoleRepository."""

import pytest

from application.repositories.user_role_repository import UserRoleRepository
from application.services.auth_service import AuthService
from application.services.tenant_service import TenantService, slugify
from application.tenant_context import tenant_scope
from models import Role, Tenant, User, UserRole


@pytest.fixture
async def super_admin(db):
    su = await User.create(discord_id=1000, username='root')
    # SUPER_ADMIN row carries tenant=NULL (explicit so the harness doesn't stamp).
    await UserRole.create(user=su, role=Role.SUPER_ADMIN, tenant=None)
    return su


async def test_slugify_makes_url_safe():
    assert slugify('Wizzrobe Live!') == 'wizzrobe-live'
    assert slugify('  Multiple   spaces  ') == 'multiple-spaces'
    assert slugify('') == 'tenant'


async def test_is_super_admin_true_across_any_tenant(super_admin, db):
    # A second tenant exists; the super-admin row (tenant NULL) is visible in
    # either tenant's context and with no context.
    b = await Tenant.create(name='B', slug='b')
    assert await AuthService.is_super_admin(super_admin) is True
    with tenant_scope(b.id):
        assert await AuthService.is_super_admin(super_admin) is True


async def test_create_tenant_requires_super_admin(db):
    nobody = await User.create(discord_id=2000, username='nobody')
    with pytest.raises(PermissionError):
        await TenantService.create_tenant(nobody, name='X', slug='x')


async def test_create_tenant_validates_slug(super_admin, db):
    with pytest.raises(ValueError):
        await TenantService.create_tenant(super_admin, name='X', slug='Bad Slug')
    with pytest.raises(ValueError):
        await TenantService.create_tenant(super_admin, name='X', slug='platform')  # reserved
    await TenantService.create_tenant(super_admin, name='X', slug='good-slug')
    with pytest.raises(ValueError):
        await TenantService.create_tenant(super_admin, name='Y', slug='good-slug')  # dup


async def test_create_tenant_resolves_and_invalidates_cache(super_admin, db):
    # Prime the cache with a miss.
    assert await TenantService.get_by_slug('acme') is None
    tenant = await TenantService.create_tenant(super_admin, name='Acme', slug='acme')
    # Cache was cleared on create, so the new tenant resolves.
    resolved = await TenantService.get_by_slug('acme')
    assert resolved is not None and resolved.id == tenant.id


async def test_guild_resolution(super_admin, db):
    tenant = await TenantService.create_tenant(
        super_admin, name='Guilded', slug='guilded', discord_guild_id=42424242,
    )
    resolved = await TenantService.list_tenants_for_guild(42424242)
    assert [t.id for t in resolved] == [tenant.id]
    assert await TenantService.list_tenants_for_guild(999) == []


async def test_guild_resolution_returns_all_sharing_tenants(super_admin, db):
    # Several communities can share one Discord server; every linked tenant is
    # resolved (stable id order) so the bot fans out over all of them.
    a = await TenantService.create_tenant(
        super_admin, name='A', slug='ga', discord_guild_id=42424242,
    )
    b = await TenantService.create_tenant(
        super_admin, name='B', slug='gb', discord_guild_id=42424242,
    )
    resolved = await TenantService.list_tenants_for_guild(42424242)
    assert [t.id for t in resolved] == [a.id, b.id]


async def test_grant_and_revoke_super_admin(super_admin, db):
    target = await User.create(discord_id=3000, username='promote')
    await TenantService.grant_super_admin(super_admin, target)
    assert await AuthService.is_super_admin(target) is True
    assert await UserRole.filter(user=target, role=Role.SUPER_ADMIN, tenant=None).exists()

    await TenantService.revoke_super_admin(super_admin, target)
    assert await AuthService.is_super_admin(target) is False


async def test_bootstrap_staff_is_per_tenant(super_admin, db):
    a = await Tenant.get(id=1)
    b = await TenantService.create_tenant(super_admin, name='B', slug='b')
    staffer = await User.create(discord_id=4000, username='staffer')

    await TenantService.bootstrap_staff(super_admin, a.id, staffer)

    with tenant_scope(a.id):
        assert await AuthService.is_staff(staffer) is True
        assert await TenantService.is_member(staffer.id, a.id) is True
    with tenant_scope(b.id):
        # STAFF in A must not carry into B.
        assert await AuthService.is_staff(staffer) is False


async def test_userrole_add_is_tenant_scoped(db):
    """A normal role grant lands in the current tenant only."""
    a = await Tenant.get(id=1)
    b = await Tenant.create(name='B', slug='b')
    user = await User.create(discord_id=5000, username='u')

    with tenant_scope(a.id):
        await UserRoleRepository.add(user, Role.PROCTOR)
        assert await AuthService.has_role(user, Role.PROCTOR) is True
    with tenant_scope(b.id):
        assert await AuthService.has_role(user, Role.PROCTOR) is False


async def test_create_tenant_allows_shared_guild_id(super_admin, db):
    # Multiple communities may share one Discord server, so a second tenant can
    # claim the same guild id — the bot fans out over every linked tenant.
    await TenantService.create_tenant(
        super_admin, name='First', slug='first', discord_guild_id=555,
    )
    await TenantService.create_tenant(
        super_admin, name='Second', slug='second', discord_guild_id=555,
    )
    resolved = await TenantService.list_tenants_for_guild(555)
    assert {t.slug for t in resolved} == {'first', 'second'}


async def test_update_tenant_allows_shared_guild_id(super_admin, db):
    a = await TenantService.create_tenant(
        super_admin, name='A', slug='ta', discord_guild_id=777,
    )
    b = await TenantService.create_tenant(
        super_admin, name='B', slug='tb', discord_guild_id=888,
    )
    # Re-pointing B at A's guild is allowed; both now resolve for that guild.
    await TenantService.update_tenant(super_admin, b, discord_guild_id=777)
    resolved = await TenantService.list_tenants_for_guild(777)
    assert {t.id for t in resolved} == {a.id, b.id}


async def test_slug_cache_is_bounded(db):
    # Negative lookups are cached keyed on the URL slug; a flood of distinct
    # unknown slugs must not grow the cache without limit.
    from application.services import tenant_service as ts
    ts._clear_cache()
    for i in range(ts._CACHE_MAX + 250):
        assert await TenantService.get_by_slug(f'nope{i}') is None
    assert len(ts._cache_by_slug) <= ts._CACHE_MAX
    ts._clear_cache()


async def test_list_staff_reads_the_named_tenant_not_the_ambient_one(super_admin, db):
    # The ambient tenant is the default one; asking for tenant B's staff must
    # not return A's. Written against the ambient tenant this returns A's staff.
    b = await Tenant.create(name='B', slug='b')
    staffer = await User.create(discord_id=3100, username='a_staff')
    await UserRoleRepository.add(staffer, Role.STAFF)  # in the ambient tenant A

    assert await TenantService.list_staff(super_admin, b.id) == []
    in_a = await TenantService.list_staff(super_admin, 1)
    assert [u.id for u in in_a] == [staffer.id]


async def test_list_staff_requires_super_admin(db):
    nobody = await User.create(discord_id=3101, username='nobody2')
    with pytest.raises(PermissionError):
        await TenantService.list_staff(nobody, 1)


async def test_list_staff_is_empty_for_a_fresh_tenant(super_admin, db):
    fresh = await TenantService.create_tenant(super_admin, name='Fresh', slug='fresh')
    assert await TenantService.list_staff(super_admin, fresh.id) == []


async def test_bootstrap_staff_is_what_list_staff_then_reports(super_admin, db):
    fresh = await TenantService.create_tenant(super_admin, name='New', slug='new-one')
    operator = await User.create(discord_id=3102, username='operator')
    await TenantService.bootstrap_staff(super_admin, fresh.id, operator)
    assert [u.id for u in await TenantService.list_staff(super_admin, fresh.id)] == [operator.id]
