"""Read-path isolation for the service/repository reads that bypass the primary
repos (config, audit, telemetry) — the paths scoped in the events/telemetry pass.

These cover the nullable-tenant models (SystemConfiguration is NOT-NULL scoped;
AuditLog / TelemetryEvent are nullable but their per-tenant reads must still
exclude other tenants' rows)."""

import pytest

from application.repositories.audit_repository import AuditRepository
from application.repositories.telemetry_repository import TelemetryRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.system_config_service import SystemConfigService
from application.services.telemetry_service import TelemetryService
from application.tenant_context import tenant_scope
from models import Role, User, UserRole


@pytest.fixture
async def tenants_with_staff(two_tenants):
    a, b = two_tenants
    staff = await User.create(discord_id=700, username='staff')
    # STAFF in both tenants so the staff-gated writers/readers work in each scope.
    await UserRole.create(user=staff, role=Role.STAFF, tenant=a)
    await UserRole.create(user=staff, role=Role.STAFF, tenant=b)
    return a, b, staff


async def test_system_config_is_tenant_isolated(tenants_with_staff):
    a, b, staff = tenants_with_staff
    with tenant_scope(a.id):
        await SystemConfigService.set_raw('greeting', 'hello-A', staff)
        assert await SystemConfigService.get_raw('greeting') == 'hello-A'
    with tenant_scope(b.id):
        # B has no such config; the same name in A must not bleed through.
        assert await SystemConfigService.get_raw('greeting') is None
        await SystemConfigService.set_raw('greeting', 'hello-B', staff)
        assert await SystemConfigService.get_raw('greeting') == 'hello-B'
    with tenant_scope(a.id):
        # A's value is unchanged by B's write to the same key.
        assert await SystemConfigService.get_raw('greeting') == 'hello-A'


async def test_audit_reads_are_tenant_isolated(tenants_with_staff):
    a, b, staff = tenants_with_staff
    with tenant_scope(a.id):
        await AuditService().write_log(staff, AuditActions.MATCH_CREATED, {'x': 1})
        assert len(await AuditRepository.list()) == 1
    with tenant_scope(b.id):
        # B's trail excludes A's row.
        assert await AuditRepository.list() == []


async def test_telemetry_reads_are_tenant_isolated(tenants_with_staff):
    a, b, staff = tenants_with_staff
    with tenant_scope(a.id):
        await TelemetryService().track_interaction(
            event_type='report.exported', path='/a', discord_id=str(staff.discord_id),
        )
        assert len(await TelemetryRepository.list()) == 1
    with tenant_scope(b.id):
        assert await TelemetryRepository.list() == []
