"""Cross-tenant isolation for ticket stores.

A store row holds a community's own payment key, so another community must
never read it, and a sealed key copied onto another community's row must not
open there.
"""

import pytest

from application.repositories import TicketStoreRepository
from application.services.ticket_store_service import STORE_SEAL_PURPOSE, store_gateway
from application.tenant_context import tenant_scope
from application.utils.secret_box import seal
from models import TicketProvider, TicketStore


async def _store(tenant_id: int, key: str, token: str) -> TicketStore:
    return await TicketStore.create(
        tenant_id=tenant_id, provider=TicketProvider.STRIPE, webhook_token=token,
        secret=seal(key, purpose=STORE_SEAL_PURPOSE, context=str(tenant_id)),
    )


async def test_store_reads_are_isolated(two_tenants):
    a, b = two_tenants
    sa = await _store(a.id, 'rk_test_a', 'tok-a')
    sb = await _store(b.id, 'rk_test_b', 'tok-b')

    repo = TicketStoreRepository()
    with tenant_scope(a.id):
        assert [s.id for s in await repo.list_all()] == [sa.id]
        assert (await repo.get_by_provider(TicketProvider.STRIPE)).id == sa.id
    with tenant_scope(b.id):
        assert [s.id for s in await repo.list_all()] == [sb.id]
    assert (await repo.get_by_webhook_token('tok-b')).tenant_id == b.id


async def test_a_key_copied_to_another_community_does_not_open(two_tenants):
    a, b = two_tenants
    sa = await _store(a.id, 'rk_test_a', 'tok-a')
    sb = await _store(b.id, 'rk_test_b', 'tok-b')
    sb.secret = sa.secret
    with pytest.raises(ValueError):
        store_gateway(sb)
