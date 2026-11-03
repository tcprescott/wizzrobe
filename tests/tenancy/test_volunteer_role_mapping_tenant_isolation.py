"""Cross-tenant isolation for volunteer-position role mappings.

A mapping, an assignment, and the role it confers all belong to one community:
reconciling in tenant A must neither read B's mappings nor touch B's roles.
"""

from application.repositories.volunteer_assignment_repository import (
    VolunteerAssignmentRepository,
)
from application.repositories.volunteer_role_mapping_repository import (
    VolunteerRoleMappingRepository,
)
from application.repositories.volunteer_shift_repository import VolunteerShiftRepository
from application.services.volunteer.volunteer_role_mapping_service import (
    VolunteerRoleMappingService,
)
from application.tenant_context import tenant_scope
from models import (
    Role,
    RoleSource,
    TenantMembership,
    UserRole,
    VolunteerAssignment,
    VolunteerPosition,
    VolunteerRoleMapping,
    VolunteerShift,
)
from tests.factories import make_user, utc


async def test_mapping_reads_are_isolated(two_tenants):
    a, b = two_tenants
    with tenant_scope(a.id):
        pa = await VolunteerPosition.create(name='Proctor Desk')
        ma = await VolunteerRoleMapping.create(position=pa, app_role=Role.PROCTOR)
    with tenant_scope(b.id):
        pb = await VolunteerPosition.create(name='Proctor Desk')
        mb = await VolunteerRoleMapping.create(position=pb, app_role=Role.PROCTOR)

    with tenant_scope(a.id):
        assert [m.id for m in await VolunteerRoleMappingRepository.list_all()] == [ma.id]
        assert await VolunteerRoleMappingRepository.get_by_id(mb.id) is None
        assert not await VolunteerRoleMappingRepository.exists(pb.id, Role.PROCTOR)
    with tenant_scope(b.id):
        assert [m.id for m in await VolunteerRoleMappingRepository.list_all()] == [mb.id]
        assert await VolunteerRoleMappingRepository.get_by_id(ma.id) is None


async def test_reconcile_stays_in_its_tenant(two_tenants):
    a, b = two_tenants
    staff = await make_user(9001, 'staff')
    vol = await make_user(9002, 'vol')
    with tenant_scope(a.id):
        pos = await VolunteerPosition.create(name='Proctor Desk')
        await VolunteerRoleMapping.create(position=pos, app_role=Role.PROCTOR)
        shift = await VolunteerShift.create(
            position=pos, starts_at=utc(2026, 10, 4, 8), ends_at=utc(2026, 10, 4, 12),
        )
        await VolunteerAssignment.create(shift=shift, user=vol)
        await TenantMembership.create(user=vol, tenant_id=a.id)
    with tenant_scope(b.id):
        await UserRole.create(user=vol, role=Role.STREAM_MANAGER, source=RoleSource.VOLUNTEER)

    with tenant_scope(a.id):
        await VolunteerRoleMappingService().reconcile_users(staff, [vol.id])

    assert await UserRole.filter(user=vol, tenant_id=a.id, role=Role.PROCTOR).exists()
    assert not await UserRole.filter(user=vol, tenant_id=b.id, role=Role.PROCTOR).exists()
    assert await UserRole.filter(user=vol, tenant_id=b.id, role=Role.STREAM_MANAGER).exists()


async def test_assignment_and_shift_reads_are_isolated(two_tenants):
    """The reads the reconcile leans on: whose assignment, on which position."""
    a, b = two_tenants
    vol = await make_user(9003, 'vol')
    shifts = {}
    positions = {}
    for tenant in (a, b):
        with tenant_scope(tenant.id):
            positions[tenant.id] = await VolunteerPosition.create(name='Desk')
            shifts[tenant.id] = await VolunteerShift.create(
                position=positions[tenant.id],
                starts_at=utc(2026, 10, 4, 8), ends_at=utc(2026, 10, 4, 12),
            )
            await VolunteerAssignment.create(shift=shifts[tenant.id], user=vol)

    with tenant_scope(a.id):
        assert await VolunteerAssignmentRepository.published_position_ids_for_user(vol.id) == {
            positions[a.id].id
        }
        assert await VolunteerAssignmentRepository.published_user_ids(shifts[b.id].id) == set()
        assert await VolunteerShiftRepository.get_by_id(shifts[b.id].id) is None
    with tenant_scope(b.id):
        assert await VolunteerAssignmentRepository.published_position_ids_for_user(vol.id) == {
            positions[b.id].id
        }
        assert await VolunteerShiftRepository.get_by_id(shifts[a.id].id) is None


async def test_reconcile_does_not_readmit_a_removed_member(two_tenants):
    """Their old shifts stay published after removal; a later reconcile must
    not hand the role, and with it the membership, back."""
    a, _ = two_tenants
    staff = await make_user(9011, 'staff')
    vol = await make_user(9012, 'vol')
    with tenant_scope(a.id):
        pos = await VolunteerPosition.create(name='Check-in Desk')
        await VolunteerRoleMapping.create(position=pos, app_role=Role.PROCTOR)
        shift = await VolunteerShift.create(
            position=pos, starts_at=utc(2026, 10, 4, 8), ends_at=utc(2026, 10, 4, 12),
        )
        await VolunteerAssignment.create(shift=shift, user=vol)

        await VolunteerRoleMappingService().reconcile_users(staff, [vol.id])

    assert not await UserRole.filter(user=vol, tenant_id=a.id).exists()
    assert not await TenantMembership.filter(user=vol, tenant_id=a.id).exists()
