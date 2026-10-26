"""Personal webhooks never cross communities.

A PersonalWebhook belongs to one member in one community: the same person in
another community sees none of it, can't edit it from there, and an event in
one community never reaches a webhook set up in the other.
"""

import pytest

from application.events import Event, EventType
from application.services import personal_webhook_service as pws
from application.services.personal_webhook_service import PersonalWebhookService
from application.tenant_context import tenant_scope
from models import PersonalWebhook, TenantMembership
from tests.factories import make_user


@pytest.fixture(autouse=True)
def _reset_limits():
    pws.reset_rate_limits()
    yield
    pws.reset_rate_limits()


async def test_webhooks_do_not_leak_across_tenants(two_tenants, monkeypatch):
    a, b = two_tenants
    member = await make_user(discord_id=760, username='both-member')
    for tenant in (a, b):
        await TenantMembership.create(user=member, tenant=tenant)

    service = PersonalWebhookService()
    with tenant_scope(a.id):
        hook_a, _ = await service.create(
            member, name='A hook', url='https://a.example/hook',
            event_types=[EventType.MATCH_STARTED], only_mine=False,
        )

    with tenant_scope(b.id):
        assert await service.list_mine(member) == []
        with pytest.raises(ValueError):
            await service.delete(member, hook_a.id)
        queued = []
        monkeypatch.setattr(pws.webhook_queue, 'enqueue', lambda coro: queued.append(coro.close()))
        await service.deliver_event(Event.create(EventType.MATCH_STARTED, {'match_id': 1}))
        assert queued == []

    with tenant_scope(a.id):
        assert [w.name for w in await service.list_mine(member)] == ['A hook']
    assert await PersonalWebhook.filter(user=member).count() == 1
