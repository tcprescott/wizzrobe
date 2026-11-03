"""Cross-tenant isolation (leak) test for the PR 10 AsyncQualifierLiveRace model.

Live races are tenant-scoped, so each tenant sees only its own through the
scoped repository (the by-slug routing lookup stays scoped — the room lookup
resolves the tenant first).
"""

from application.repositories.async_qualifier_repository import (
    AsyncQualifierLiveRaceRepository,
)
from application.tenant_context import tenant_scope
from models import (
    AsyncQualifier,
    AsyncQualifierLiveRace,
    AsyncQualifierPool,
    Tenant,
)


async def _pool(name: str) -> AsyncQualifierPool:
    q = await AsyncQualifier.create(name=name)
    return await AsyncQualifierPool.create(qualifier=q, name='P')


async def test_live_race_reads_are_isolated(db):
    a = await Tenant.get(id=1)
    b = await Tenant.create(name='Tenant B', slug='tenant-b')
    repo = AsyncQualifierLiveRaceRepository()

    with tenant_scope(a.id):
        pa = await _pool('QA')
        la = await AsyncQualifierLiveRace.create(
            pool=pa, match_title='A', racetime_slug='cat/qualifier-live-a',
        )
    with tenant_scope(b.id):
        pb = await _pool('QB')
        lb = await AsyncQualifierLiveRace.create(
            pool=pb, match_title='B', racetime_slug='cat/qualifier-live-b',
        )

    with tenant_scope(a.id):
        assert [lr.id for lr in await repo.list_for_pool(pa.id)] == [la.id]
        assert await repo.get_by_id(lb.id) is None
        assert await repo.get_by_racetime_slug('cat/qualifier-live-b') is None
    with tenant_scope(b.id):
        assert [lr.id for lr in await repo.list_for_pool(pb.id)] == [lb.id]
        assert await repo.get_by_id(la.id) is None
