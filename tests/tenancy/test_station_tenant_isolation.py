"""Cross-tenant isolation for the venue station pool and station occupancy.

``Station.name`` is unique *per tenant*, so two communities routinely name a
station ``1``. Two things must therefore be tenant-scoped:

- the pool itself (``StationRepository``), or one community's admin surface
  would list another's seats and its validation would accept them; and
- **occupancy** (``MatchRepository.occupied_stations``), which is derived from
  live matches. Unscoped, two communities running events at the same time would
  block each other's station assignments — the sharpest leak here, since the
  rejection would look like a legitimate double-booking.
"""

from application.repositories.match_repository import MatchRepository
from application.repositories.station_repository import StationRepository
from application.services.match.match_service import MatchService
from application.tenant_context import tenant_scope
from models import (
    Match,
    MatchPlayers,
    Role,
    Station,
    StationSide,
    Tenant,
    Tournament,
    User,
    UserRole,
)
from tests.factories import make_user, utc


async def _staff(tenant: Tenant, discord_id: int) -> User:
    user = await make_user(discord_id=discord_id, username=f'staff-{discord_id}')
    await UserRole.create(user=user, role=Role.STAFF, tenant=tenant)
    return user


async def _seated_match_at(tenant: Tenant, station: str) -> Match:
    """A seated, unfinished match with one player sitting at ``station``."""
    tournament = await Tournament.create(name=f'{tenant.slug} Cup')
    match = await Match.create(tournament=tournament, seated_at=utc(2025, 1, 15, 19))
    player = await make_user(discord_id=tenant.id * 1000 + 1, username=f'p-{tenant.slug}')
    await MatchPlayers.create(match=match, user=player, assigned_station=station)
    return match


async def test_station_pool_reads_are_isolated(two_tenants):
    a, b = two_tenants
    with tenant_scope(a.id):
        sa = await Station.create(name='1', section='North wall')
    with tenant_scope(b.id):
        sb = await Station.create(name='1', section='Back room')
        await Station.create(name='2')

    with tenant_scope(a.id):
        assert [s.id for s in await StationRepository.get_all()] == [sa.id]
        assert [s.id for s in await StationRepository.get_active()] == [sa.id]
        assert await StationRepository.active_names() == {'1'}
        assert await StationRepository.get_by_id(sb.id) is None
    with tenant_scope(b.id):
        assert {s.id for s in await StationRepository.get_all()} == {sb.id} | {
            s.id for s in await Station.filter(tenant_id=b.id, name='2')
        }
        assert await StationRepository.active_names() == {'1', '2'}
        assert await StationRepository.get_by_id(sa.id) is None


async def test_occupied_stations_are_isolated(two_tenants):
    a, b = two_tenants
    with tenant_scope(b.id):
        b_match = await _seated_match_at(b, '1')

    with tenant_scope(a.id):
        assert await MatchRepository.occupied_stations() == {}
    with tenant_scope(b.id):
        assert await MatchRepository.occupied_stations() == {'1': b_match.id}


async def test_the_seating_draw_never_reaches_another_tenants_pool(two_tenants):
    """The draw picks from ``get_active()``, so an unscoped read would seat a
    player at a station standing in someone else's venue."""
    a, b = two_tenants
    with tenant_scope(b.id):
        await Station.create(name='B-LEFT', side=StationSide.LEFT, position=1)
        await Station.create(name='B-RIGHT', side=StationSide.RIGHT, position=1)

    with tenant_scope(a.id):
        await Station.create(name='A-LEFT', side=StationSide.LEFT, position=1)
        await Station.create(name='A-RIGHT', side=StationSide.RIGHT, position=1)
        actor = await _staff(a, 4244)
        tournament = await Tournament.create(name='A Cup')
        match = await Match.create(tournament=tournament)
        for n in (4245, 4246):
            user = await make_user(discord_id=n, username=f'a-{n}')
            await MatchPlayers.create(match=match, user=user)

        suggestion = await MatchService().suggest_stations(match.id, actor=actor)
        assert sorted(suggestion.assignments.values()) == ['A-LEFT', 'A-RIGHT']


async def test_another_tenants_live_match_does_not_block_an_assignment(two_tenants):
    """The case that actually matters: two communities running at the same time.

    Tenant B has a player seated at *its* station 1. Tenant A assigning *its*
    station 1 must succeed — an unscoped occupancy lookup would reject it with
    "in use by match #N", naming a match in a community the actor cannot see.
    """
    a, b = two_tenants
    with tenant_scope(b.id):
        await Station.create(name='1')
        await _seated_match_at(b, '1')

    with tenant_scope(a.id):
        await Station.create(name='1')
        actor = await _staff(a, 4242)
        a_tournament = await Tournament.create(name='A Cup')
        a_match = await Match.create(tournament=a_tournament)
        a_player_user = await make_user(discord_id=4243, username='a-player')
        a_player = await MatchPlayers.create(match=a_match, user=a_player_user)

        await MatchService().assign_stations(
            a_match.id, {a_player.id: '1'}, actor=actor,
        )
        await a_player.refresh_from_db()
        assert a_player.assigned_station == '1'
