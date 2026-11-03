"""Seed the half-provisioned ``fledgling`` tenant.

A sibling of the other ``seed_*`` fixture modules, called from
``seed_dev.seed_all`` — the same split that keeps ``seed_dev.py`` under the
800-line budget.
"""

from application.services.event_service import EventService
from application.tenant_context import tenant_scope
from models import (
    JoinRequestStatus,
    Role,
    Tenant,
    TenantJoinRequest,
    TenantMembership,
    Tournament,
    User,
    UserRole,
)


async def seed_fledgling_tenant(users: dict[str, User], groups: dict) -> None:
    """A third tenant deliberately stopped part-way through setup.

    The other two are fully provisioned, so the setup checklist is complete in
    both and the panel would be invisible in dev. This one has a staff member
    and one **empty tournament**: ``staff`` and ``tournament`` done, ``enrolment``
    and ``stage`` outstanding — the mixed state the checklist,
    ``/platform``'s readiness column and the empty entrants dialog all need in
    one login.
    """
    tenant, created = await Tenant.get_or_create(
        slug='fledgling',
        defaults={'name': 'Fledgling Community', 'discord_guild_id': 1000000000000000003},
    )
    tenant.feature_group = groups['default']
    await tenant.save()
    await EventService.ensure_first_event(tenant)
    with tenant_scope(tenant.id):
        await TenantMembership.get_or_create(user=users['staff_user'], tenant=tenant)
        await UserRole.get_or_create(
            user=users['staff_user'], role=Role.STAFF, tenant=tenant,
            defaults={'granted_by': None},
        )
        await Tournament.get_or_create(
            name='First Tournament', tenant=tenant,
            defaults={'is_active': True},
        )
        await TenantJoinRequest.get_or_create(
            user=users['outsider'], tenant=tenant,
            defaults={
                'status': JoinRequestStatus.PENDING,
                'message': 'I run commentary for a few events and would like to help out.',
            },
        )
    print(f"  tenant 'fledgling' ({'created' if created else 'exists'}, id={tenant.id}) — setup deliberately incomplete")
