"""Dev-seed fixtures for who belongs to one tenant and what they hold.

Split out of ``seed_dev.py`` when that module crossed the 800-line budget again,
following the ``seed_equipment_for_tenant`` / ``seed_volunteers_for_tenant``
convention already used there: one module per domain, called from inside the
caller's ``tenant_scope``. Idempotent and tenant-stamped like every other seeded
row.

Two fixtures are defined by an *absence* rather than a row — ``outsider`` is a
member of nothing, ``local_only`` a member of one community and not the other —
so this is the one seed module that deletes: a create-only pass can never
converge an absence, and a stale membership from an earlier fixture layout would
quietly stop them proving anything.
"""

from datetime import datetime, timedelta, timezone

from application.event_context import get_current_event_id
from models import (
    MembershipSource,
    Role,
    RoleSource,
    Tenant,
    TenantMemberBlock,
    TenantMembership,
    User,
    UserRole,
)


async def seed_access_for_tenant(tenant: Tenant, users: dict[str, User]) -> None:
    """Seed one tenant's memberships and per-tenant role grants."""
    for uname, u in users.items():
        if uname == 'removed_member':
            await TenantMembership.filter(user=u, tenant=tenant).delete()
            await TenantMemberBlock.get_or_create(
                user=u, tenant=tenant, defaults={'blocked_by': users.get('staff_user')},
            )
            continue
        if uname == 'outsider' or (uname == 'local_only' and tenant.slug != 'default'):
            # Delete, don't skip: these fixtures are defined by an absence, so a create-only seed can't converge them.
            await TenantMembership.filter(user=u, tenant=tenant).delete()
            continue
        await TenantMembership.get_or_create(user=u, tenant=tenant)

    now = datetime.now(timezone.utc)
    await TenantMembership.filter(tenant=tenant).update(
        source=MembershipSource.STAFF, created_at=now - timedelta(days=30),
    )
    if 'volunteer_only' in users:
        await TenantMembership.filter(tenant=tenant, user=users['volunteer_only']).update(
            source=MembershipSource.JOIN_REQUEST, created_at=now - timedelta(days=3),
        )
    for uname, source in (
        ('proctor_only', MembershipSource.ROLE_GRANT),
        ('sm_only', MembershipSource.DISCORD_ROLE),
    ):
        if uname in users:
            await TenantMembership.filter(tenant=tenant, user=users[uname]).update(source=source)
    if 'local_only' in users and tenant.slug == 'default':
        await TenantMembership.filter(tenant=tenant, user=users['local_only']).update(
            source=MembershipSource.DISCORD_AUTO_JOIN, created_at=now - timedelta(hours=2),
        )
    if 'racer_05' in users:
        await TenantMembership.filter(tenant=tenant, user=users['racer_05']).update(
            source=MembershipSource.OPEN, created_at=now - timedelta(hours=1),
        )
    if 'racer_06' in users:
        await TenantMembership.filter(tenant=tenant, user=users['racer_06']).update(
            source=MembershipSource.DISCORD_IMPORT, created_at=now - timedelta(days=1),
        )

    role_grants = [
        ("staff_user", Role.STAFF),
        ("desk_password", Role.CHECK_IN_DESK),
        ("proctor_user", Role.PROCTOR),
        ("sm_user", Role.STREAM_MANAGER),
        ("player_one", Role.TRIFORCE_SUBMITTER),
        ("proctor_user", Role.VOLUNTEER),
        ("sm_user", Role.VOLUNTEER),
        ("player_one", Role.VOLUNTEER),
        ("player_two", Role.VOLUNTEER),
        ("player_three", Role.VOLUNTEER),
        ("player_four", Role.VOLUNTEER),
        # The only grant vc_user gets (coordinator, no staff). cc_user gets no role row: crew coordination is per tournament.
        ("vc_user", Role.VOLUNTEER_COORDINATOR),
        # One holder each for the online-tournament admin roles, and nothing else: a surface that gates on
        # staff-ness instead must fail for them in dev.
        ("preset_mgr", Role.PRESET_MANAGER),
        ("sync_user", Role.SYNC_ADMIN),
        ("qual_admin", Role.QUALIFIER_ADMIN),
        # Same rule for roles whose holders also held VOLUNTEER, so a PROCTOR/VOLUNTEER gate mix-up fails in dev.
        ("proctor_only", Role.PROCTOR),
        ("sm_only", Role.STREAM_MANAGER),
        ("triforce_sub", Role.TRIFORCE_SUBMITTER),
        ("volunteer_only", Role.VOLUNTEER),
        # Desk only, no other role: can check people in but not add walk-ups.
        ("checkin_desk", Role.CHECK_IN_DESK),
    ]
    if tenant.slug == "default":
        # One tenant only: a role grant implies membership, so this user must stay absent from tenant B's pickers.
        role_grants.append(("local_only", Role.VOLUNTEER))
    discord_sourced = {("player_three", Role.VOLUNTEER), ("player_four", Role.VOLUNTEER)}
    for uname, role in role_grants:
        await UserRole.get_or_create(
            user=users[uname], role=role, tenant=tenant,
            event_id=get_current_event_id() if role.is_per_event else None,
            defaults={
                "granted_by": None,
                "source": (
                    RoleSource.DISCORD if (uname, role) in discord_sourced
                    else RoleSource.MANUAL
                ),
            },
        )
    print(
        f"    [{tenant.slug}] roles ok"
        + (" (local_only is a VOLUNTEER here and nowhere else)"
           if tenant.slug == "default" else " (local_only holds nothing here)")
    )
