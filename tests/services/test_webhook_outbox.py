"""The webhook outbox: a delivery is a row before it is a POST, so a restart can't lose it."""

from datetime import datetime, timedelta, timezone

import pytest

from application.events import Event, EventType
from application.services import webhook_outbox_worker
from application.services.webhook_service import WebhookService
from models import Webhook, WebhookDelivery
from tests.services.test_webhook_service import FakeClientFactory


@pytest.fixture
def queued(monkeypatch):
    items = []
    monkeypatch.setattr('application.services.webhook_service.webhook_queue.enqueue', items.append)
    return items


@pytest.fixture
def no_sleep(monkeypatch):
    async def _noop(_seconds):
        return None
    monkeypatch.setattr('application.services.webhook_service.asyncio.sleep', _noop)


@pytest.fixture
def client(monkeypatch):
    fake = FakeClientFactory([200] * 10)
    monkeypatch.setattr('application.services.webhook_service.httpx.AsyncClient', fake)
    return fake


async def _hook(**kwargs) -> Webhook:
    return await Webhook.create(name='w', url='https://example.com/hook', secret='s',
                                event_types=['*'], **kwargs)


async def _age(delivery: WebhookDelivery, **delta) -> None:
    await WebhookDelivery.filter(id=delivery.id).update(
        created_at=datetime.now(timezone.utc) - timedelta(**delta),
    )


async def test_the_row_exists_before_the_send_and_is_finished_by_it(db, queued, client):
    webhook = await _hook()
    await WebhookService().deliver_event(Event.create(EventType.MATCH_CREATED, {'match_id': 1}))

    row = await WebhookDelivery.get(webhook=webhook)
    assert row.pending and row.delivery_uuid and not client.calls

    await queued.pop()
    row = await WebhookDelivery.get(id=row.id)
    assert not row.pending and row.success and row.claimed_until is None
    assert client.calls[0]['headers']['X-Wizzrobe-Delivery'] == row.delivery_uuid


async def test_a_claimed_row_is_left_to_its_sender(db, client):
    webhook = await _hook()
    row = await WebhookDelivery.create(
        tenant_id=1, webhook=webhook, event_type='match.created', payload='{}', pending=True,
        delivery_uuid='u', claimed_until=datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    assert await WebhookService().attempt(webhook, row) is False
    assert not client.calls
    assert (await WebhookDelivery.get(id=row.id)).pending


async def test_the_sweeper_sends_what_a_dead_process_left(db, client, no_sleep):
    webhook = await _hook()
    stuck = await WebhookDelivery.create(
        tenant_id=1, webhook=webhook, event_type='match.created', payload='{"a": 1}', pending=True,
        delivery_uuid='same-id', attempt_count=1,
        claimed_until=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    fresh = await WebhookDelivery.create(
        tenant_id=1, webhook=webhook, event_type='match.created', payload='{}', pending=True, delivery_uuid='f',
    )
    await _age(stuck, minutes=5)

    await webhook_outbox_worker._tick()

    assert [c['headers']['X-Wizzrobe-Delivery'] for c in client.calls] == ['same-id']
    assert client.calls[0]['content'] == '{"a": 1}'
    stuck = await WebhookDelivery.get(id=stuck.id)
    assert stuck.success and not stuck.pending and stuck.attempt_count == 2
    assert (await WebhookDelivery.get(id=fresh.id)).pending, 'a row this young may still be in the queue'


async def test_the_sweeper_closes_rows_that_will_never_go(db, client):
    off = await _hook(is_active=False)
    on = await _hook()
    turned_off = await WebhookDelivery.create(
        tenant_id=1, webhook=off, event_type='match.created', payload='{}', pending=True, delivery_uuid='a',
    )
    too_old = await WebhookDelivery.create(
        tenant_id=1, webhook=on, event_type='match.created', payload='{}', pending=True, delivery_uuid='b',
    )
    await _age(turned_off, minutes=5)
    await _age(too_old, days=2)

    await webhook_outbox_worker._tick()

    assert not client.calls
    for row_id, reason in ((turned_off.id, 'turned off'), (too_old.id, 'within a day')):
        row = await WebhookDelivery.get(id=row_id)
        assert not row.pending and not row.success and reason in row.error


async def test_pending_rows_stay_out_of_the_delivery_log(db):
    webhook = await _hook()
    await WebhookDelivery.create(tenant_id=1, webhook=webhook, event_type='x', payload='{}', pending=True)
    done = await WebhookDelivery.create(tenant_id=1, webhook=webhook, event_type='x', payload='{}', success=True)
    from application.repositories import WebhookDeliveryRepository
    assert [d.id for d in await WebhookDeliveryRepository().list_for_webhook(webhook)] == [done.id]
