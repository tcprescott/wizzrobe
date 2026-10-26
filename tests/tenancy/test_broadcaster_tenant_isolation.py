"""Broadcaster signups never cross communities."""

from application.repositories import BroadcasterRepository
from application.tenant_context import tenant_scope
from models import Match, Tournament
from tests.factories import make_user


async def test_broadcasters_are_isolated(two_tenants):
    a, b = two_tenants
    user = await make_user(discord_id=7101, username='broadcaster')
    ids = {}
    for tenant in (a, b):
        with tenant_scope(tenant.id):
            t = await Tournament.create(name=f'{tenant.slug} Cup', tenant=tenant)
            m = await Match.create(tournament=t, tenant=tenant)
            row = await BroadcasterRepository.create(m, user)
            ids[tenant.id] = (m, row.id)

    (ma, ra), (mb, rb) = ids[a.id], ids[b.id]
    with tenant_scope(a.id):
        assert await BroadcasterRepository.get_by_match(mb) == []
        assert await BroadcasterRepository.get_by_id(rb) is None
        assert [r.id for r in await BroadcasterRepository.get_for_user(user)] == [ra]
    with tenant_scope(b.id):
        assert [r.id for r in await BroadcasterRepository.get_by_match(mb)] == [rb]
        assert await BroadcasterRepository.get_by_id(ra) is None
        assert await BroadcasterRepository.get_by_match_and_user(ma, user) is None
