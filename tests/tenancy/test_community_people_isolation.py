"""The borrower/owner picker offers this community's people, not the platform's.

``User`` is a **global** identity with no tenant column — that is deliberate and
stays that way. But a per-community *picker* built from `User.all()` offers every
person on the platform, service accounts included; the equipment checkout dialog
did exactly that, and the audit's own checkout landed on `System`.

So "belongs to this community" is derived, and ``TenantMembership`` is what
derives it. It was not always: this query used to union role-holders with
tournament entrants precisely *because* membership was a frozen backfill nothing
wrote to. The membership work closed that — a role in a tenant now implies
membership in it, staff can add a member directly, an approved join request
creates one, and an imported SpeedGaming player gets one — and made membership
the thing the access gate itself checks. So the picker and the door now agree:
a picker cannot offer someone the app would turn away.

The fixtures below therefore make their people members the way the app does.
These tests pin both halves: the scoping, and the exclusions that hold
regardless of tenant.
"""

from application.repositories.user_repository import UserRepository
from application.services.user_service import UserService
from application.tenant_context import tenant_scope
from models import (
    SYSTEM_USER_DISCORD_ID,
    Role,
    Tenant,
    TenantMembership,
    Tournament,
    TournamentPlayers,
    User,
    UserRole,
)
from tests.factories import make_user


async def _role_holder(tenant: Tenant, discord_id: int, username: str) -> User:
    """A role holder — and therefore, by the invariant, a member."""
    user = await make_user(discord_id=discord_id, username=username)
    await UserRole.create(user=user, role=Role.VOLUNTEER, tenant=tenant)
    await TenantMembership.create(user=user, tenant=tenant)
    return user


async def _entrant(tenant: Tenant, discord_id: int, username: str) -> User:
    """An entrant in one of the tenant's tournaments, and a member of it.

    Both writes: enrolment is what the tournament screen does, membership is
    what the picker and the gate read. The backfill script exists to reconcile
    any historical row where the two came apart.
    """
    user = await make_user(discord_id=discord_id, username=username)
    with tenant_scope(tenant.id):
        tournament = await Tournament.create(name=f'{tenant.slug} Cup', tenant=tenant)
        await TournamentPlayers.create(tournament=tournament, user=user, tenant=tenant)
    await TenantMembership.create(user=user, tenant=tenant)
    return user


async def test_a_role_holder_in_another_tenant_never_appears(two_tenants):
    a, b = two_tenants
    mine = await _role_holder(a, 9001, 'a-volunteer')
    theirs = await _role_holder(b, 9002, 'b-volunteer')

    with tenant_scope(a.id):
        ids = {u.id for u in await UserRepository.get_community_people()}
    assert mine.id in ids
    assert theirs.id not in ids

    with tenant_scope(b.id):
        ids = {u.id for u in await UserRepository.get_community_people()}
    assert theirs.id in ids
    assert mine.id not in ids


async def test_an_entrant_in_another_tenants_tournament_never_appears(two_tenants):
    a, b = two_tenants
    mine = await _entrant(a, 9011, 'a-player')
    theirs = await _entrant(b, 9012, 'b-player')

    with tenant_scope(a.id):
        ids = {u.id for u in await UserRepository.get_community_people()}
    assert ids == {mine.id}
    assert theirs.id not in ids


async def test_the_service_read_is_scoped_the_same_way(two_tenants):
    a, b = two_tenants
    mine = await _role_holder(a, 9021, 'a-staffer')
    theirs = await _role_holder(b, 9022, 'b-staffer')

    with tenant_scope(a.id):
        ids = {u.id for u in await UserService().get_community_people()}
    assert ids == {mine.id}
    assert theirs.id not in ids


async def test_a_user_with_no_grant_anywhere_is_in_neither(two_tenants):
    a, b = two_tenants
    stranger = await make_user(discord_id=9031, username='stranger')
    for tenant in (a, b):
        with tenant_scope(tenant.id):
            ids = {u.id for u in await UserRepository.get_community_people()}
        assert stranger.id not in ids


async def test_the_system_account_is_excluded_even_when_it_holds_a_role(two_tenants):
    a, _ = two_tenants
    system = await make_user(discord_id=SYSTEM_USER_DISCORD_ID, username='System')
    await UserRole.create(user=system, role=Role.STAFF, tenant=a)
    kept = await _role_holder(a, 9041, 'a-real-person')

    with tenant_scope(a.id):
        ids = {u.id for u in await UserRepository.get_community_people()}
    assert ids == {kept.id}


async def test_a_member_with_no_discord_id_survives_the_system_exclusion(two_tenants):
    """The system account is excluded by a sentinel id; NULL is not that id.

    Written as an `.exclude(discord_id=SENTINEL)` this fails: `NULL = <sentinel>`
    is NULL, NOT NULL is not true, so every placeholder disappears alongside the
    system row — and a SpeedGaming placeholder has a NULL discord id by
    construction, which is exactly the person a synced roster needs to offer.
    """
    a, _ = two_tenants
    placeholder = await make_user(discord_id=None, username='sg_placeholder', is_placeholder=True)
    await TenantMembership.create(user=placeholder, tenant=a)

    with tenant_scope(a.id):
        ids = {u.id for u in await UserRepository.get_community_people()}
    assert placeholder.id in ids


async def test_a_deactivated_account_is_excluded(two_tenants):
    a, _ = two_tenants
    gone = await _role_holder(a, 9051, 'left-the-community')
    gone.is_active = False
    await gone.save()
    kept = await _role_holder(a, 9052, 'still-here')

    with tenant_scope(a.id):
        ids = {u.id for u in await UserRepository.get_community_people()}
    assert ids == {kept.id}


async def test_include_user_ids_keeps_a_holder_who_fell_out_of_the_set(two_tenants):
    a, b = two_tenants
    holder = await _role_holder(b, 9061, 'walked-in-from-elsewhere')

    with tenant_scope(a.id):
        ids = {u.id for u in await UserRepository.get_community_people()}
        assert holder.id not in ids
        forced = {u.id for u in await UserRepository.get_community_people(
            include_user_ids=[holder.id],
        )}
    assert holder.id in forced


async def test_include_user_ids_does_not_defeat_the_hard_exclusions(two_tenants):
    a, _ = two_tenants
    system = await make_user(discord_id=SYSTEM_USER_DISCORD_ID, username='System')
    inactive = await make_user(discord_id=9071, username='inactive', is_active=False)

    with tenant_scope(a.id):
        ids = {u.id for u in await UserRepository.get_community_people(
            include_user_ids=[system.id, inactive.id],
        )}
    assert ids == set()


async def test_a_user_who_is_both_role_holder_and_entrant_appears_once(two_tenants):
    a, _ = two_tenants
    person = await _role_holder(a, 9081, 'double-counted')
    with tenant_scope(a.id):
        tournament = await Tournament.create(name='A Cup', tenant=a)
        await TournamentPlayers.create(tournament=tournament, user=person, tenant=a)
        people = await UserRepository.get_community_people()
    assert [u.id for u in people] == [person.id]


async def test_ordering_matches_the_pickers_existing_key(two_tenants):
    a, _ = two_tenants
    await _role_holder(a, 9091, 'zoe')
    await _role_holder(a, 9092, 'alice')
    await _role_holder(a, 9093, 'mallory')
    with tenant_scope(a.id):
        names = [u.username for u in await UserRepository.get_community_people()]
    assert names == sorted(names)


async def test_a_community_with_nobody_yet_returns_empty(two_tenants):
    _, b = two_tenants
    with tenant_scope(b.id):
        assert await UserRepository.get_community_people() == []
