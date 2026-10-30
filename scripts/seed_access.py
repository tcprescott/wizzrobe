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
        # Every scoped user is a member of this tenant, bar the two fixtures
        # that exist to be absent: local_only is the "member of one community
        # and not another" case, outsider the "member of nothing at all" one,
        # which is what the membership gate's join page needs to be reachable
        # in dev.
        if uname == 'removed_member':
            await TenantMembership.filter(user=u, tenant=tenant).delete()
            await TenantMemberBlock.get_or_create(
                user=u, tenant=tenant, defaults={'blocked_by': users.get('staff_user')},
            )
            continue
        if uname == 'outsider' or (uname == 'local_only' and tenant.slug != 'default'):
            # Deleted, not merely skipped: these two fixtures are defined by
            # an *absence*, and a create-only seed can never converge one —
            # a stale row from an earlier fixture layout would leave them
            # members here and quietly stop proving anything.
            await TenantMembership.filter(user=u, tenant=tenant).delete()
            continue
        await TenantMembership.get_or_create(user=u, tenant=tenant)

    # How and when each joined, for the Users tab's Joined/Via columns and its
    # "Joined in the last 7 days" filter. Re-applied every run, relative to now:
    # everyone joined a month ago by staff hand, bar volunteer_only (an approved
    # join request, three days ago) and local_only on 'default' (Discord
    # auto-join, today). So a fresh seed shows the Discord marker and a filter
    # that narrows to two people rather than everyone.
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

    # The VOLUNTEER grants below mirror the qualified +
    # available pool seeded further down so the Vol. Roster tab and
    # the auto-scheduler actually have an assignable pool to show
    # (VolunteerPoolService.assignable_volunteers filters on Role.VOLUNTEER).
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
        # Deliberately the only grant vc_user gets: a coordinator with no
        # staff role. cc_user gets no role row at all — crew coordination is
        # a per-tournament relation, granted below.
        ("vc_user", Role.VOLUNTEER_COORDINATOR),
        # One holder each for the three online-tournament admin roles, and
        # nothing else: the Presets tab, the sync config (SpeedGaming /
        # racetime / Discord events) and the qualifier review queue each gate
        # on one of these, and a surface that gates on staff-ness instead
        # looks correct until the delegate it was written for logs in.
        ("preset_mgr", Role.PRESET_MANAGER),
        ("sync_user", Role.SYNC_ADMIN),
        ("qual_admin", Role.QUALIFIER_ADMIN),
        # The same rule applied to the four roles whose only holders also held
        # VOLUNTEER (proctor_user, sm_user, player_one): with nothing holding
        # them alone, a surface that means to gate on PROCTOR but gates on
        # VOLUNTEER — or the reverse — passed in dev either way.
        ("proctor_only", Role.PROCTOR),
        ("sm_only", Role.STREAM_MANAGER),
        ("triforce_sub", Role.TRIFORCE_SUBMITTER),
        ("volunteer_only", Role.VOLUNTEER),
        # Works the check-in desk and nothing else, so the desk is exercised
        # by someone who can check people in but not add walk-ups.
        ("checkin_desk", Role.CHECK_IN_DESK),
    ]
    if tenant.slug == "default":
        # Deliberately one tenant only — a role grant implies membership, so
        # a user who holds nothing in tenant B and is a member of nothing
        # there must be absent from tenant B's pickers.
        role_grants.append(("local_only", Role.VOLUNTEER))
    # Roles the guild-role sync granted rather than a staff member: the
    # DiscordRoleMapping fixtures below map this guild's "Volunteers" role
    # onto VOLUNTEER, and these are the rows that mapping would produce.
    # ``RoleSource`` is what the role table's "granted by" column reads, and
    # what a re-sync is allowed to revoke — a seed where every grant is
    # MANUAL cannot show either.
    discord_sourced = {("player_three", Role.VOLUNTEER), ("player_four", Role.VOLUNTEER)}
    for uname, role in role_grants:
        await UserRole.get_or_create(
            user=users[uname], role=role, tenant=tenant,
            # The default event's grant; seed_events adds grants in other events.
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
