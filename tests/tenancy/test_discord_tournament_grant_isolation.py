"""Tenant isolation for Discord-sourced per-tournament grants.

``DiscordTournamentGrant`` records which ``Tournament.admins`` /
``.crew_coordinators`` rows the guild-role sync created, so it decides what a
re-sync is allowed to take away. A read that crossed tenants would let one
community's sync revoke another's grants.
"""

import pytest

from application.repositories.discord_tournament_grant_repository import (
    DiscordTournamentGrantRepository,
)
from application.repositories.tournament_repository import TournamentRepository
from application.tenant_context import tenant_scope
from models import Tournament, TournamentGrant, User


@pytest.fixture
async def grants_in_both(two_tenants):
    a, b = two_tenants
    user = await User.create(discord_id=810, username='delegate')
    tournaments = {}
    for tenant in (a, b):
        with tenant_scope(tenant.id):
            tournaments[tenant.id] = await Tournament.create(
                name=f'Event {tenant.slug}', tenant=tenant,
            )
            await DiscordTournamentGrantRepository.add(
                user, tournaments[tenant.id], TournamentGrant.TOURNAMENT_ADMIN,
            )
    return a, b, user, tournaments


async def test_grant_reads_are_tenant_isolated(grants_in_both):
    a, b, user, tournaments = grants_in_both
    with tenant_scope(a.id):
        rows = await DiscordTournamentGrantRepository.list_for_user(user)
        assert [r.tournament_id for r in rows] == [tournaments[a.id].id]
    with tenant_scope(b.id):
        rows = await DiscordTournamentGrantRepository.list_for_user(user)
        assert [r.tournament_id for r in rows] == [tournaments[b.id].id]


async def test_get_match_does_not_cross_tenants(grants_in_both):
    a, b, user, tournaments = grants_in_both
    with tenant_scope(b.id):
        assert await DiscordTournamentGrantRepository.get_match(
            user, tournaments[a.id].id, TournamentGrant.TOURNAMENT_ADMIN,
        ) is None


async def test_remove_only_touches_the_scoped_tenants_row(grants_in_both):
    a, b, user, tournaments = grants_in_both
    with tenant_scope(a.id):
        await DiscordTournamentGrantRepository.remove(
            user, tournaments[a.id].id, TournamentGrant.TOURNAMENT_ADMIN,
        )
        assert await DiscordTournamentGrantRepository.list_for_user(user) == []
    with tenant_scope(b.id):
        assert len(await DiscordTournamentGrantRepository.list_for_user(user)) == 1


async def test_has_tournament_grant_does_not_cross_tenants(two_tenants):
    a, b = two_tenants
    user = await User.create(discord_id=811, username='ta')
    with tenant_scope(a.id):
        tournament = await Tournament.create(name='A Event', tenant=a)
        await tournament.admins.add(user)
        assert await TournamentRepository.has_tournament_grant(
            tournament.id, user, TournamentGrant.TOURNAMENT_ADMIN,
        )
    with tenant_scope(b.id):
        assert not await TournamentRepository.has_tournament_grant(
            tournament.id, user, TournamentGrant.TOURNAMENT_ADMIN,
        )
