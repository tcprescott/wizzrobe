"""Cross-tenant isolation (leak) tests for the PR 7 SpeedGaming models.

``SpeedGamingEventLink`` and ``SpeedGamingEpisode`` are tenant-scoped, so each
tenant must see only its own rows. The worker's ``list_active_all`` scan is
deliberately **unscoped** (a timer has no ambient tenant), so it resolves
cross-tenant on purpose — that behavior is asserted too.
"""

import pytest

from application.repositories.speedgaming_episode_repository import SpeedGamingEpisodeRepository
from application.repositories.speedgaming_event_link_repository import SpeedGamingEventLinkRepository
from application.tenant_context import tenant_scope
from models import SpeedGamingEpisode, SpeedGamingEventLink, Tournament


@pytest.fixture
async def tenants_with_tournaments(two_tenants):
    """Builds on the canonical ``two_tenants``, giving each tenant a tournament."""
    a, b = two_tenants
    with tenant_scope(a.id):
        ta = await Tournament.create(name='TA')
    with tenant_scope(b.id):
        tb = await Tournament.create(name='TB')
    return (a, ta), (b, tb)


async def test_event_link_reads_are_isolated(tenants_with_tournaments):
    (a, ta), (b, tb) = tenants_with_tournaments
    repo = SpeedGamingEventLinkRepository()
    with tenant_scope(a.id):
        la = await SpeedGamingEventLink.create(tournament=ta, event_slug='ev')
    with tenant_scope(b.id):
        lb = await SpeedGamingEventLink.create(tournament=tb, event_slug='ev')

    with tenant_scope(a.id):
        assert [x.id for x in await repo.list_all()] == [la.id]
        assert await repo.get_by_id(lb.id) is None
    with tenant_scope(b.id):
        assert [x.id for x in await repo.list_all()] == [lb.id]
        assert await repo.get_by_id(la.id) is None


async def test_episode_reads_are_isolated(tenants_with_tournaments):
    (a, ta), (b, tb) = tenants_with_tournaments
    repo = SpeedGamingEpisodeRepository()
    with tenant_scope(a.id):
        ea = await SpeedGamingEpisode.create(sg_episode_id='1', tenant_id=a.id)
    with tenant_scope(b.id):
        eb = await SpeedGamingEpisode.create(sg_episode_id='1', tenant_id=b.id)

    with tenant_scope(a.id):
        assert (await repo.get_by_sg_id('1')).id == ea.id
        assert await repo.get_by_id(eb.id) is None
    with tenant_scope(b.id):
        assert (await repo.get_by_sg_id('1')).id == eb.id


async def test_active_scan_is_unscoped_cross_tenant(tenants_with_tournaments):
    """The worker resolves due links across every tenant in one query."""
    (a, ta), (b, tb) = tenants_with_tournaments
    repo = SpeedGamingEventLinkRepository()
    with tenant_scope(a.id):
        la = await SpeedGamingEventLink.create(tournament=ta, event_slug='ea', active=True)
    with tenant_scope(b.id):
        lb = await SpeedGamingEventLink.create(tournament=tb, event_slug='eb', active=True)
        await SpeedGamingEventLink.create(tournament=tb, event_slug='inactive', active=False)

    ids = {x.id for x in await repo.list_active_all()}
    assert ids == {la.id, lb.id}
