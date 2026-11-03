"""WatchLayoutVote must not leak across tenants: one community's layout-trial votes stay its own."""

from application.repositories._tenant import scoped
from application.tenant_context import tenant_scope
from models import WatchLayoutVote


async def test_votes_are_tenant_isolated(two_tenants):
    a, b = two_tenants
    await WatchLayoutVote.create(tenant=a, session_id='browser', layout='tabs', vote='up')

    with tenant_scope(b.id):
        assert await scoped(WatchLayoutVote.all()).count() == 0
    with tenant_scope(a.id):
        assert await scoped(WatchLayoutVote.all()).count() == 1
