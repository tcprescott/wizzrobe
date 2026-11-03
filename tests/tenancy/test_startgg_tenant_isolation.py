"""start.gg mirror rows (StartggEntrant, StartggPhase, StartggPool, StartggSet) never cross communities."""

from application.repositories import StartggRepository
from application.tenant_context import tenant_scope
from models import (
    StartggEntrant,
    StartggPhase,
    StartggPool,
    StartggSet,
    StartggSetState,
    Tournament,
)
from tests.factories import make_user


async def test_startgg_mirror_is_isolated(two_tenants):
    a, b = two_tenants
    player = await make_user(7101, 'player')
    made = {}
    for tenant in (a, b):
        with tenant_scope(tenant.id):
            t = await Tournament.create(
                name=f'{tenant.slug} Cup', tenant=tenant, startgg_event_slug=f'tournament/{tenant.slug}/event/x',
            )
            phase = await StartggPhase.create(tenant=tenant, tournament=t, phase_id='1', name='Final')
            pool = await StartggPool.create(
                tenant=tenant, tournament=t, phase=phase, phase_group_id='2', identifier='1',
            )
            e1 = await StartggEntrant.create(tenant=tenant, tournament=t, entrant_id='3', name='A', user=player)
            e2 = await StartggEntrant.create(tenant=tenant, tournament=t, entrant_id='4', name='B')
            s = await StartggSet.create(
                tenant=tenant, tournament=t, pool=pool, set_id='5', state=StartggSetState.OPEN,
                entrant1=e1, entrant2=e2,
            )
            made[tenant.id] = (t, e1, s)

    (ta, ea, sa), (tb, eb, sb) = made[a.id], made[b.id]
    with tenant_scope(a.id):
        assert await StartggRepository.list_entrants(tb) == []
        assert await StartggRepository.list_phases(tb) == []
        assert await StartggRepository.list_pools(tb) == []
        assert await StartggRepository.list_sets(tb) == []
        assert await StartggRepository.get_entrant(eb.id) is None
        assert await StartggRepository.get_set(sb.id) is None
        assert [r.id for r in await StartggRepository.open_unbooked_sets_for_user(player)] == [sa.id]
        assert [t.id for t in await StartggRepository.linked_tournaments()] == [ta.id]
    with tenant_scope(b.id):
        assert [r.id for r in await StartggRepository.open_unbooked_sets_for_user(player)] == [sb.id]
        assert await StartggRepository.get_entrant(ea.id) is None


async def test_startgg_connection_is_per_community(two_tenants):
    """One community's StartggConnection (its start.gg sign-in) is invisible to another."""
    a, b = two_tenants
    with tenant_scope(a.id):
        await StartggRepository.save_connection(access_token='a-token', startgg_username='A')
    with tenant_scope(b.id):
        assert await StartggRepository.get_connection() is None
        await StartggRepository.save_connection(access_token='b-token', startgg_username='B')
        assert await StartggRepository.clear_connection() == 1
    with tenant_scope(a.id):
        connection = await StartggRepository.get_connection()
        assert connection is not None and connection.access_token == 'a-token'
