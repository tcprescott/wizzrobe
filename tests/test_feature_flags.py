"""Feature-flag system tests: registry, two-tier semantics, and isolation.

The ``db`` fixture pre-seeds the default tenant (id 1) with every flag fully on
(see conftest), so these tests exercise the OFF/partial states under freshly
created tenants that start with no flag rows — a missing row is the
disabled-by-default posture.
"""

import pytest

from application.feature_flags import FEATURE_FLAG_REGISTRY, established_flags
from application.repositories.feature_flag_group_repository import FeatureFlagGroupRepository
from application.repositories.feature_flag_repository import TenantFeatureFlagRepository
from application.services.feature_flag_service import FeatureFlagService
from application.tenant_context import reset_tenant_id, set_tenant_id, tenant_scope
from models import AuditLog, FeatureFlag, Role, Tenant, TenantFeatureFlag, User, UserRole


@pytest.fixture
async def tenants(db):
    """(tenant_a, tenant_b). A is the pre-seeded default (all flags on); B is fresh (no rows)."""
    a = await Tenant.get(id=1)
    b = await Tenant.create(name='Tenant B', slug='tenant-b')
    return a, b


async def _staff(discord_id: int, tenant_id: int) -> User:
    user = await User.create(discord_id=discord_id, username=f'u{discord_id}')
    await UserRole.create(user=user, role=Role.STAFF, tenant_id=tenant_id)
    return user


async def _super_admin(discord_id: int) -> User:
    user = await User.create(discord_id=discord_id, username=f'sa{discord_id}')
    await UserRole.create(user=user, role=Role.SUPER_ADMIN, tenant=None)
    return user


async def _flag(tenant_id: int, flag: FeatureFlag, *, available: bool, enabled: bool) -> None:
    await TenantFeatureFlag.create(
        tenant_id=tenant_id, flag=flag.value, available=available, enabled=enabled,
    )


# --- registry ---------------------------------------------------------------

def test_registry_covers_every_flag():
    assert len(FEATURE_FLAG_REGISTRY) == 21
    assert set(FEATURE_FLAG_REGISTRY) == set(FeatureFlag)


def test_established_flags_are_the_in_use_features():
    assert set(established_flags()) == {
        FeatureFlag.CHALLONGE, FeatureFlag.EQUIPMENT,
        FeatureFlag.VOLUNTEERS, FeatureFlag.TRIFORCE_TEXTS,
        # Gated after the fact (migration 51), so it backfills rather than
        # disappearing for communities already collecting feedback.
        FeatureFlag.FEEDBACK,
    }
    # New/unreleased features ship dark (not established).
    assert FeatureFlag.ASYNC_QUALIFIERS not in established_flags()
    assert FeatureFlag.RACETIME_ROOMS not in established_flags()
    # Native brackets ship dark — a brand-new, unreleased subsystem.
    assert FeatureFlag.BRACKETS not in established_flags()
    # Event Information ships dark too: no community had one before the flag
    # existed, so there is nothing to backfill and nothing to preserve.
    assert FeatureFlag.EVENT_INFO not in established_flags()
    # Event check-in is new, so it ships dark as well.
    assert FeatureFlag.EVENT_CHECK_IN not in established_flags()


# --- effective state: available AND enabled ---------------------------------

async def test_missing_row_is_off(tenants):
    a, b = tenants
    svc = FeatureFlagService()
    with tenant_scope(b.id):
        assert await svc.is_enabled(FeatureFlag.EQUIPMENT) is False
        assert await svc.enabled_flags() == set()


async def test_available_but_not_enabled_is_off(tenants):
    a, b = tenants
    with tenant_scope(b.id):
        await _flag(b.id, FeatureFlag.EQUIPMENT, available=True, enabled=False)
        assert await FeatureFlagService().is_enabled(FeatureFlag.EQUIPMENT) is False


async def test_available_and_enabled_is_on(tenants):
    a, b = tenants
    with tenant_scope(b.id):
        await _flag(b.id, FeatureFlag.EQUIPMENT, available=True, enabled=True)
        svc = FeatureFlagService()
        assert await svc.is_enabled(FeatureFlag.EQUIPMENT) is True
        assert FeatureFlag.EQUIPMENT in await svc.enabled_flags()


async def test_enabled_without_available_is_off(tenants):
    """Defensive: a stray enabled=True/available=False row still reads off."""
    a, b = tenants
    with tenant_scope(b.id):
        await _flag(b.id, FeatureFlag.EQUIPMENT, available=False, enabled=True)
        assert await FeatureFlagService().is_enabled(FeatureFlag.EQUIPMENT) is False


async def test_off_tenant_reads_disabled(db):
    """No tenant in scope (the platform surface) → off, never a raise."""
    token = set_tenant_id(None)
    try:
        assert await FeatureFlagService().is_enabled(FeatureFlag.EQUIPMENT) is False
        assert await FeatureFlagService().enabled_flags() == set()
    finally:
        reset_tenant_id(token)


# --- ensure_enabled (service guard) -----------------------------------------

async def test_ensure_enabled_raises_when_off(tenants):
    a, b = tenants
    with tenant_scope(b.id):
        with pytest.raises(ValueError):
            await FeatureFlagService().ensure_enabled(FeatureFlag.EQUIPMENT)


async def test_ensure_enabled_passes_when_on(tenants):
    a, b = tenants
    with tenant_scope(b.id):
        await _flag(b.id, FeatureFlag.EQUIPMENT, available=True, enabled=True)
        await FeatureFlagService().ensure_enabled(FeatureFlag.EQUIPMENT)  # no raise


# --- tenant tier: STAFF toggles enabled within availability -----------------

async def test_set_tenant_enabled_requires_availability(tenants):
    a, b = tenants
    with tenant_scope(b.id):
        staff = await _staff(5001, b.id)
        with pytest.raises(ValueError):
            await FeatureFlagService().set_tenant_enabled(staff, FeatureFlag.EQUIPMENT, True)


async def test_set_tenant_enabled_toggles_when_available(tenants):
    a, b = tenants
    with tenant_scope(b.id):
        staff = await _staff(5002, b.id)
        await _flag(b.id, FeatureFlag.EQUIPMENT, available=True, enabled=False)
        svc = FeatureFlagService()
        await svc.set_tenant_enabled(staff, FeatureFlag.EQUIPMENT, True)
        assert await svc.is_enabled(FeatureFlag.EQUIPMENT) is True
        await svc.set_tenant_enabled(staff, FeatureFlag.EQUIPMENT, False)
        assert await svc.is_enabled(FeatureFlag.EQUIPMENT) is False


async def test_set_tenant_enabled_requires_staff(tenants):
    a, b = tenants
    with tenant_scope(b.id):
        nonstaff = await User.create(discord_id=5003, username='ns')
        await _flag(b.id, FeatureFlag.EQUIPMENT, available=True, enabled=False)
        with pytest.raises(PermissionError):
            await FeatureFlagService().set_tenant_enabled(nonstaff, FeatureFlag.EQUIPMENT, True)


async def test_set_tenant_enabled_writes_audit(tenants):
    a, b = tenants
    with tenant_scope(b.id):
        staff = await _staff(5006, b.id)
        await _flag(b.id, FeatureFlag.EQUIPMENT, available=True, enabled=False)
        await FeatureFlagService().set_tenant_enabled(staff, FeatureFlag.EQUIPMENT, True)
        rows = await AuditLog.filter(tenant_id=b.id, action='feature_flag.enabled')
        assert len(rows) == 1


# --- platform tier: super-admin grants availability -------------------------

async def test_set_availability_requires_super_admin(tenants):
    a, b = tenants
    staff = await _staff(5004, b.id)  # tenant staff is NOT a super-admin
    with pytest.raises(PermissionError):
        await FeatureFlagService().set_availability(staff, b.id, FeatureFlag.EQUIPMENT, True)


async def test_forcing_availability_makes_feature_live_by_default(tenants):
    # New semantics: availability ⇒ enabled ON by default (community may opt out).
    a, b = tenants
    sa = await _super_admin(5005)
    await FeatureFlagService().set_availability(sa, b.id, FeatureFlag.ASYNC_QUALIFIERS, True)
    row = await TenantFeatureFlagRepository.get_for_tenant(b.id, 'async_qualifiers')
    assert row is not None and row.available is True and row.enabled is None  # enabled inherits
    with tenant_scope(b.id):
        assert await FeatureFlagService().is_enabled(FeatureFlag.ASYNC_QUALIFIERS) is True


async def test_clearing_availability_override_returns_to_inherit(tenants):
    a, b = tenants
    sa = await _super_admin(5006)
    svc = FeatureFlagService()
    # Force off, then clear (None) → the override row is deleted (back to inherit).
    await svc.set_availability(sa, b.id, FeatureFlag.EQUIPMENT, False)
    assert await TenantFeatureFlagRepository.get_for_tenant(b.id, 'equipment') is not None
    await svc.set_availability(sa, b.id, FeatureFlag.EQUIPMENT, None)
    assert await TenantFeatureFlagRepository.get_for_tenant(b.id, 'equipment') is None


async def test_revoking_availability_preserves_enabled_choice(tenants):
    a, b = tenants
    sa = await _super_admin(5007)
    await _flag(b.id, FeatureFlag.EQUIPMENT, available=True, enabled=True)
    await FeatureFlagService().set_availability(sa, b.id, FeatureFlag.EQUIPMENT, False)
    row = await TenantFeatureFlagRepository.get_for_tenant(b.id, 'equipment')
    # enabled untouched, but effective is off because available is now False.
    assert row.enabled is True and row.available is False
    with tenant_scope(b.id):
        assert await FeatureFlagService().is_enabled(FeatureFlag.EQUIPMENT) is False


# --- isolation --------------------------------------------------------------

async def test_flags_do_not_leak_across_tenants(db):
    b = await Tenant.create(name='B', slug='tb')
    c = await Tenant.create(name='C', slug='tc')
    with tenant_scope(b.id):
        await _flag(b.id, FeatureFlag.EQUIPMENT, available=True, enabled=True)
    svc = FeatureFlagService()
    with tenant_scope(b.id):
        assert await svc.is_enabled(FeatureFlag.EQUIPMENT) is True
    with tenant_scope(c.id):
        # C never had a row — B's flag must not bleed across.
        assert await svc.is_enabled(FeatureFlag.EQUIPMENT) is False
        assert await svc.enabled_flags() == set()


# --- groups (live tiers) ----------------------------------------------------

async def test_group_grants_availability_and_enables_by_default(tenants):
    a, b = tenants
    sa = await _super_admin(6001)
    svc = FeatureFlagService()
    g = await svc.create_group(sa, name='Online', flags=['async_qualifiers', 'racetime_rooms'])
    await svc.assign_tenant_group(sa, b.id, g.id)
    with tenant_scope(b.id):
        assert await svc.is_enabled(FeatureFlag.ASYNC_QUALIFIERS) is True
        assert await svc.is_enabled(FeatureFlag.RACETIME_ROOMS) is True
        assert await svc.is_enabled(FeatureFlag.EQUIPMENT) is False  # not in the group
        assert await svc.enabled_flags() == {
            FeatureFlag.ASYNC_QUALIFIERS, FeatureFlag.RACETIME_ROOMS,
        }


async def test_default_group_is_live_fallback_for_ungrouped(tenants):
    a, b = tenants  # b is never assigned a group
    sa = await _super_admin(6002)
    svc = FeatureFlagService()
    await svc.create_group(sa, name='Base', flags=['equipment'], is_default=True)
    with tenant_scope(b.id):
        assert await svc.is_enabled(FeatureFlag.EQUIPMENT) is True   # from default
        assert await svc.is_enabled(FeatureFlag.CHALLONGE) is False


async def test_override_forces_off_a_group_granted_flag(tenants):
    a, b = tenants
    sa = await _super_admin(6003)
    svc = FeatureFlagService()
    g = await svc.create_group(sa, name='Online', flags=['async_qualifiers'])
    await svc.assign_tenant_group(sa, b.id, g.id)
    await svc.set_availability(sa, b.id, FeatureFlag.ASYNC_QUALIFIERS, False)  # exception
    with tenant_scope(b.id):
        assert await svc.is_enabled(FeatureFlag.ASYNC_QUALIFIERS) is False


async def test_override_forces_on_an_ungrouped_flag(tenants):
    a, b = tenants
    sa = await _super_admin(6004)
    svc = FeatureFlagService()
    g = await svc.create_group(sa, name='Online', flags=['async_qualifiers'])
    await svc.assign_tenant_group(sa, b.id, g.id)
    await svc.set_availability(sa, b.id, FeatureFlag.EQUIPMENT, True)  # exception grant
    with tenant_scope(b.id):
        assert await svc.is_enabled(FeatureFlag.EQUIPMENT) is True


async def test_community_can_disable_a_group_granted_feature(tenants):
    a, b = tenants
    sa = await _super_admin(6005)
    svc = FeatureFlagService()
    g = await svc.create_group(sa, name='Online', flags=['async_qualifiers'])
    await svc.assign_tenant_group(sa, b.id, g.id)
    with tenant_scope(b.id):
        staff = await _staff(6006, b.id)
        await svc.set_tenant_enabled(staff, FeatureFlag.ASYNC_QUALIFIERS, False)
        assert await svc.is_enabled(FeatureFlag.ASYNC_QUALIFIERS) is False  # sticky opt-out
        assert FeatureFlag.ASYNC_QUALIFIERS not in await svc.enabled_flags()


async def test_editing_group_updates_assigned_tenants_live(tenants):
    a, b = tenants
    sa = await _super_admin(6007)
    svc = FeatureFlagService()
    g = await svc.create_group(sa, name='Online', flags=['async_qualifiers'])
    await svc.assign_tenant_group(sa, b.id, g.id)
    with tenant_scope(b.id):
        assert await svc.is_enabled(FeatureFlag.RACETIME_ROOMS) is False
    await svc.update_group(sa, g.id, flags=['async_qualifiers', 'racetime_rooms'])
    with tenant_scope(b.id):
        assert await svc.is_enabled(FeatureFlag.RACETIME_ROOMS) is True  # live update


async def test_single_default_is_enforced(tenants):
    a, b = tenants
    sa = await _super_admin(6008)
    svc = FeatureFlagService()
    g1 = await svc.create_group(sa, name='D1', flags=[], is_default=True)
    g2 = await svc.create_group(sa, name='D2', flags=[], is_default=True)
    default = await FeatureFlagGroupRepository.get_default()
    assert default is not None and default.id == g2.id
    g1_reloaded = await FeatureFlagGroupRepository.get_by_id(g1.id)
    assert g1_reloaded.is_default is False


async def test_deleting_group_reassigns_tenant_to_ungrouped(tenants):
    a, b = tenants
    sa = await _super_admin(6009)
    svc = FeatureFlagService()
    g = await svc.create_group(sa, name='Online', flags=['async_qualifiers'])
    await svc.assign_tenant_group(sa, b.id, g.id)
    await svc.delete_group(sa, g.id)
    tenant = await Tenant.get(id=b.id)
    assert tenant.feature_group_id is None


async def test_group_flags_are_validated_against_registry(tenants):
    a, b = tenants
    sa = await _super_admin(6010)
    g = await FeatureFlagService().create_group(sa, name='Weird', flags=['equipment', 'not_a_real_flag'])
    assert g.flags == ['equipment']  # unknown key dropped


async def test_group_management_requires_super_admin(tenants):
    a, b = tenants
    with tenant_scope(b.id):
        staff = await _staff(6011, b.id)
    with pytest.raises(PermissionError):
        await FeatureFlagService().create_group(staff, name='X', flags=[])
