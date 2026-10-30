"""Cross-tenant isolation (leak) tests for the PR 9 AsyncQualifier* models.

All five models are tenant-scoped, so each tenant must see only its own rows
through the scoped repositories.
"""

from application.repositories.async_qualifier_repository import (
    AsyncQualifierPoolRepository,
    AsyncQualifierRepository,
    AsyncQualifierRunRepository,
)
from application.tenant_context import tenant_scope
from models import (
    AsyncQualifier,
    AsyncQualifierPermalink,
    AsyncQualifierPool,
    AsyncQualifierRun,
    AsyncQualifierRunStatus,
    User,
)


async def test_qualifier_reads_are_isolated(two_tenants):
    a, b = two_tenants
    repo = AsyncQualifierRepository()
    with tenant_scope(a.id):
        qa = await AsyncQualifier.create(name='QA')
    with tenant_scope(b.id):
        qb = await AsyncQualifier.create(name='QB')

    with tenant_scope(a.id):
        assert [q.id for q in await repo.list_all()] == [qa.id]
        assert await repo.get_by_id(qb.id) is None
    with tenant_scope(b.id):
        assert [q.id for q in await repo.list_all()] == [qb.id]
        assert await repo.get_by_id(qa.id) is None


async def test_pool_reads_are_isolated(two_tenants):
    a, b = two_tenants
    repo = AsyncQualifierPoolRepository()
    with tenant_scope(a.id):
        qa = await AsyncQualifier.create(name='QA')
        pa = await AsyncQualifierPool.create(qualifier=qa, name='P')
    with tenant_scope(b.id):
        qb = await AsyncQualifier.create(name='QB')
        pb = await AsyncQualifierPool.create(qualifier=qb, name='P')  # same name, other tenant

    with tenant_scope(a.id):
        assert [p.id for p in await repo.list_for_qualifier(qa.id)] == [pa.id]
        assert await repo.get_by_id(pb.id) is None
    with tenant_scope(b.id):
        assert [p.id for p in await repo.list_for_qualifier(qb.id)] == [pb.id]


async def test_run_reads_are_isolated(two_tenants):
    a, b = two_tenants
    repo = AsyncQualifierRunRepository()
    with tenant_scope(a.id):
        qa = await AsyncQualifier.create(name='QA')
        pa = await AsyncQualifierPool.create(qualifier=qa, name='P')
        pla = await AsyncQualifierPermalink.create(pool=pa, url='u')
        ua = await User.create(discord_id=811, username='ua')
        ra = await AsyncQualifierRun.create(
            qualifier=qa, user=ua, permalink=pla, status=AsyncQualifierRunStatus.IN_PROGRESS,
        )
    with tenant_scope(b.id):
        qb = await AsyncQualifier.create(name='QB')
        pb = await AsyncQualifierPool.create(qualifier=qb, name='P')
        plb = await AsyncQualifierPermalink.create(pool=pb, url='u')
        ub = await User.create(discord_id=812, username='ub')
        rb = await AsyncQualifierRun.create(
            qualifier=qb, user=ub, permalink=plb, status=AsyncQualifierRunStatus.IN_PROGRESS,
        )

    with tenant_scope(a.id):
        assert [r.id for r in await repo.list_for_qualifier(qa.id)] == [ra.id]
        assert await repo.get_by_id(rb.id) is None
        assert await repo.get_active_for_user(qb.id, ub.id) is None
    with tenant_scope(b.id):
        assert [r.id for r in await repo.list_for_qualifier(qb.id)] == [rb.id]
        assert await repo.get_by_id(ra.id) is None
