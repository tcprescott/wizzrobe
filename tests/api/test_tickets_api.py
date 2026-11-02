"""The ticket sales REST surface: staff setup, a member buying, the store webhook, the flag."""

import json

import pytest

from application.services.ticket_store_service import HOOK_SEAL_PURPOSE
from application.utils.clients.stripe_client import sign
from application.utils.secret_box import open_
from models import FeatureFlag, Role, TenantFeatureFlag, TicketOrder, TicketOrderStatus, TicketStore
from tests.api_helpers import build_api_app, client_for, create_user_token

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture(autouse=True)
def mock_stripe(monkeypatch):
    monkeypatch.setenv('MOCK_STRIPE', '1')


async def test_staff_set_up_a_badge_and_a_member_buys_it():
    app = build_api_app()
    _, staff_token = await create_user_token(username='staff', roles=[Role.STAFF])
    _, member_token = await create_user_token(username='member')

    async with client_for(app, staff_token) as staff:
        r = await staff.put('/api/tickets/stores/stripe', json={'secret': 'rk_test_123'})
        assert r.status_code == 200, r.text
        assert 'rk_test' not in r.text and r.json()['test_mode'] is True
        r = await staff.post('/api/tickets/types', json={'name': 'Weekend badge', 'price_cents': 6000, 'quantity': 10})
        assert r.status_code == 201, r.text
        type_id = r.json()['id']

    async with client_for(app, member_token) as member:
        r = await member.get('/api/tickets/on-sale')
        assert [t['name'] for t in r.json()['types']] == ['Weekend badge'] and r.json()['providers'] == ['stripe']
        assert (await member.get('/api/tickets/types')).status_code == 403
        r = await member.post('/api/tickets/checkout', json={'provider': 'stripe', 'quantities': {str(type_id): 2}})
        assert r.status_code == 200, r.text
        order_id = r.json()['order_id']
        assert (await member.get(f'/api/me/tickets/orders/{order_id}')).json()['status'] == 'pending'

    store = await TicketStore.get(provider='stripe')
    order = await TicketOrder.get(id=order_id)
    body = json.dumps({'id': 'evt_1', 'type': 'checkout.session.completed', 'data': {'object': {
        'id': order.provider_ref, 'payment_status': 'paid', 'payment_intent': 'pi_1'}}}).encode()
    secret = open_(store.webhook_secret, purpose=HOOK_SEAL_PURPOSE, context=str(store.tenant_id))
    async with client_for(app) as anonymous:
        url = f'/api/ticket-webhooks/stripe/{store.webhook_token}'
        bad = await anonymous.post(url, content=body, headers={'Stripe-Signature': sign(body, 'whsec_wrong')})
        assert bad.status_code == 400
        assert (await anonymous.post('/api/ticket-webhooks/stripe/unknown', content=body)).status_code == 404
        ok = await anonymous.post(url, content=body, headers={'Stripe-Signature': sign(body, secret)})
        assert ok.status_code == 200 and ok.json()['outcome'] == 'paid'
    assert (await TicketOrder.get(id=order_id)).status is TicketOrderStatus.PAID

    async with client_for(app, member_token) as member:
        mine = (await member.get('/api/me/tickets')).json()
        assert len(mine['own']) == 1 and len(mine['extras']) == 1


async def test_the_routes_404_with_the_flag_off():
    await TenantFeatureFlag.filter(flag=FeatureFlag.TICKET_SALES.value).update(enabled=False)
    _, token = await create_user_token(username='staff', roles=[Role.STAFF])
    async with client_for(build_api_app(), token) as client:
        assert (await client.get('/api/tickets/types')).status_code == 404
        assert (await client.get('/api/me/tickets')).status_code == 404
