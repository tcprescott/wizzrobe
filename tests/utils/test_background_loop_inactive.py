"""A deactivated community gets no worker runs: no syncs, no reminders."""

from application.tenant_context import get_current_tenant_id
from application.utils.background_loop import for_each_tenant_scoped
from models import Tenant


async def test_inactive_communities_are_skipped(db):
    paused = await Tenant.create(name='Paused', slug='paused', is_active=False)
    seen: list = []

    async def handle(tenant_id: int) -> None:
        seen.append(get_current_tenant_id())

    await for_each_tenant_scoped([1, paused.id, None], handle, tenant_id_of=lambda t: t)

    assert seen == [1]
