"""Cross-tenant isolation for player self-service tournament signup.

``TournamentPlayers`` reached from a global ``User`` spans every community that
user plays in, so the two reads the Tournaments tab makes —
``enrolled_tournament_ids`` and ``entrants_by_tournament`` — are the sharp edges:
un-scoped, a player enrolled in tenant B's tournament would see themselves
marked "Signed up" on a same-named tournament in tenant A, and each card would
list the other community's entrants.
"""

from datetime import datetime, timedelta, timezone

import pytest

from application.repositories.tournament_repository import TournamentRepository
from application.repositories.user_role_repository import UserRoleRepository
from application.services.tenant_membership_service import TenantMembershipService
from application.services.tournament_service import TournamentService
from application.tenant_context import tenant_scope
from models import Role, Tenant, TenantMembership, Tournament, TournamentPlayers, User


async def _member(discord_id: int, username: str, tenant_id: int) -> User:
    """A user who belongs to ``tenant_id``, with a staff actor to admit them."""
    with tenant_scope(tenant_id):
        boss = await User.create(discord_id=discord_id + 500, username=f'{username}-boss')
        await UserRoleRepository.add(boss, Role.STAFF)
        await TenantMembership.create(user=boss, tenant_id=tenant_id)
        person = await User.create(discord_id=discord_id, username=username)
        await TenantMembershipService().add_member(boss, person)
    return person


@pytest.fixture
async def tenant_b(db):
    return await Tenant.create(name='Tenant B', slug='tenant-b')


async def test_signup_state_does_not_leak_across_tenants(db, tenant_b):
    window = dict(
        signups_open_at=datetime.now(timezone.utc) - timedelta(days=1),
        signups_close_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    tournament_a = await Tournament.create(name='Spring Open', **window)
    with tenant_scope(tenant_b.id):
        tournament_b = await Tournament.create(
            name='Spring Open', tenant_id=tenant_b.id, **window,
        )

    # One person in both communities, entered in tenant B's tournament only.
    player = await _member(8400, 'crossover', 1)
    with tenant_scope(tenant_b.id):
        await TenantMembership.create(user=player, tenant_id=tenant_b.id)
        await TournamentService().self_enroll(tournament_b.id, player)

    card = next(
        c for c in await TournamentService().list_signup_cards(player)
        if c.tournament.id == tournament_a.id
    )
    assert card.enrolled is False
    assert card.entrant_count == 0
    assert card.can_sign_up is True


async def test_entrant_lists_are_tenant_scoped(db, tenant_b):
    tournament_a = await Tournament.create(name='Cup')
    with tenant_scope(tenant_b.id):
        tournament_b = await Tournament.create(name='Cup', tenant_id=tenant_b.id)

    here = await _member(8401, 'local', 1)
    away = await _member(8402, 'remote', tenant_b.id)
    await TournamentPlayers.create(tournament=tournament_a, user=here, tenant_id=1)
    with tenant_scope(tenant_b.id):
        await TournamentPlayers.create(
            tournament=tournament_b, user=away, tenant_id=tenant_b.id,
        )

    entrants = await TournamentRepository.entrants_by_tournament(
        [tournament_a.id, tournament_b.id],
    )
    assert [u.id for u in entrants.get(tournament_a.id, [])] == [here.id]
    assert entrants.get(tournament_b.id, []) == []


async def test_another_tenants_tournament_cannot_be_signed_up_for(db, tenant_b):
    with tenant_scope(tenant_b.id):
        tournament_b = await Tournament.create(name='Theirs', tenant_id=tenant_b.id)
    player = await _member(8403, 'outsider-here', 1)

    with pytest.raises(ValueError):
        await TournamentService().self_enroll(tournament_b.id, player)
    assert await TournamentPlayers.filter(user=player).count() == 0


async def test_admin_lists_are_tenant_scoped(db, tenant_b):
    tournament_a = await Tournament.create(name='Cup')
    with tenant_scope(tenant_b.id):
        tournament_b = await Tournament.create(name='Cup', tenant_id=tenant_b.id)

    zed = await _member(8404, 'zed', 1)
    amy = await _member(8405, 'Amy', 1)
    away = await _member(8406, 'remote-admin', tenant_b.id)
    await tournament_a.admins.add(zed, amy)
    await tournament_b.admins.add(away)

    admins = await TournamentRepository.admins_by_tournament(
        [tournament_a.id, tournament_b.id],
    )
    assert [u.id for u in admins[tournament_a.id]] == [amy.id, zed.id]
    assert tournament_b.id not in admins

    card = next(
        c for c in await TournamentService().list_signup_cards(amy)
        if c.tournament.id == tournament_a.id
    )
    assert [u.id for u in card.admins] == [amy.id, zed.id]
