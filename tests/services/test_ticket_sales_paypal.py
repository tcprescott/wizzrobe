"""Ticket sales through PayPal: connecting a REST app, capture on approval, refunds, the signature check."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from application.services.feature_flag_service import reset_flag_cache
from application.services.ticket_checkout_service import TicketCheckoutService
from application.services.ticket_gateways import GatewaySignatureError
from application.services.ticket_service import TicketService
from application.services.ticket_store_service import STORE_SEAL_PURPOSE, TicketStoreService
from application.utils.clients import paypal_client as paypal_module
from application.utils.clients.paypal_client import parse_event, to_cents, to_value
from application.utils.secret_box import open_
from models import (
    FeatureFlag,
    Role,
    TenantFeatureFlag,
    TenantMembership,
    Ticket,
    TicketOrder,
    TicketOrderStatus,
    TicketProvider,
    TicketStatus,
    TicketStore,
    UserRole,
)
from tests.conftest import DEFAULT_TEST_TENANT_ID
from tests.factories import make_user

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture(autouse=True)
def mock_paypal(monkeypatch):
    monkeypatch.setenv('MOCK_PAYPAL', '1')
    refunds = []

    async def refund(self, capture_id, amount, currency, *, request_id):
        refunds.append((capture_id, amount, currency, request_id))
        return 'REFUND-1'

    monkeypatch.setattr(paypal_module.MockPayPalClient, 'refund', refund)
    return refunds


async def _member(discord_id: int, name: str, *roles: Role):
    user = await make_user(discord_id=discord_id, username=name)
    await TenantMembership.create(user=user, tenant_id=DEFAULT_TEST_TENANT_ID)
    for role in roles:
        await UserRole.create(user=user, role=role, tenant_id=DEFAULT_TEST_TENANT_ID)
    return user


@pytest.fixture
async def staff():
    return await _member(800, 'staffer', Role.STAFF)


@pytest.fixture
async def buyer():
    return await _member(801, 'buyer')


@pytest.fixture
async def store(staff):
    await TicketStoreService().connect(staff, TicketProvider.PAYPAL, 'app-secret', client_id='app-id')
    return await TicketStore.get(provider=TicketProvider.PAYPAL)


@pytest.fixture
async def weekend(staff, store):
    return await TicketService().create_type(staff, name='Weekend badge', price_cents=6000, quantity=5)


async def _post(store: TicketStore, event_type: str, resource: dict, *, sig=None, event_id='WH-1'):
    body = json.dumps({'id': event_id, 'event_type': event_type, 'resource': resource}).encode()
    headers = {'PayPal-Transmission-Sig': sig or f'mock-{store.webhook_id}'}
    return await TicketCheckoutService().handle_webhook(TicketProvider.PAYPAL, store.webhook_token, body, headers)


async def _approved(buyer, weekend, store, quantity=1):
    start = await TicketCheckoutService().start_checkout(buyer, TicketProvider.PAYPAL, {weekend.id: quantity})
    order = await TicketOrder.get(id=start.order_id)
    return order, await _post(store, 'CHECKOUT.ORDER.APPROVED', {'id': order.provider_ref})


async def test_connecting_seals_the_app_credentials_and_defaults_to_sandbox(staff, store):
    assert store.test_mode is True and store.webhook_id.startswith('WH-MOCK-')
    sealed = json.loads(open_(store.secret, purpose=STORE_SEAL_PURPOSE, context=str(store.tenant_id)))
    assert sealed == {'client_id': 'app-id', 'client_secret': 'app-secret', 'sandbox': True}
    assert 'app-secret' not in store.secret
    await TicketStoreService().connect(staff, TicketProvider.PAYPAL, 'live-secret', client_id='live-id', sandbox=False)
    assert (await TicketStore.get(provider=TicketProvider.PAYPAL)).test_mode is False
    with pytest.raises(ValueError, match='client ID'):
        await TicketStoreService().connect(staff, TicketProvider.PAYPAL, 'app-secret')


async def test_approval_is_captured_and_settles_the_order(buyer, weekend, store):
    order, outcome = await _approved(buyer, weekend, store, quantity=2)
    assert outcome == 'paid'
    order = await TicketOrder.get(id=order.id)
    assert order.status is TicketOrderStatus.PAID and order.payment_ref == f'MOCK-CAPTURE-{order.provider_ref}'
    assert len((await TicketService().my_badges(buyer)).extras) == 1

    again = await _post(store, 'CHECKOUT.ORDER.APPROVED', {'id': order.provider_ref}, event_id='WH-2')
    completed = await _post(store, 'PAYMENT.CAPTURE.COMPLETED', {
        'id': order.payment_ref, 'supplementary_data': {'related_ids': {'order_id': order.provider_ref}},
    }, event_id='WH-3')
    assert again == completed == 'already settled'


async def test_an_approval_after_the_hold_lapsed_is_never_captured(buyer, weekend, store, monkeypatch):
    captured = []

    async def capture(self, order_ref):
        captured.append(order_ref)
        return 'CAP'

    monkeypatch.setattr(paypal_module.MockPayPalClient, 'capture', capture)
    start = await TicketCheckoutService().start_checkout(buyer, TicketProvider.PAYPAL, {weekend.id: 1})
    await TicketOrder.filter(id=start.order_id).update(hold_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    order = await TicketOrder.get(id=start.order_id)

    outcome = await _post(store, 'CHECKOUT.ORDER.APPROVED', {'id': order.provider_ref})
    assert outcome == 'expired, not captured' and captured == []
    assert (await TicketOrder.get(id=order.id)).status is TicketOrderStatus.EXPIRED


async def test_a_webhook_paypal_wont_vouch_for_changes_nothing(buyer, weekend, store):
    start = await TicketCheckoutService().start_checkout(buyer, TicketProvider.PAYPAL, {weekend.id: 1})
    order = await TicketOrder.get(id=start.order_id)
    with pytest.raises(GatewaySignatureError):
        await _post(store, 'CHECKOUT.ORDER.APPROVED', {'id': order.provider_ref}, sig='forged')
    assert (await TicketOrder.get(id=order.id)).status is TicketOrderStatus.PENDING


async def test_a_dashboard_refund_voids_the_badges(buyer, weekend, store):
    order, _ = await _approved(buyer, weekend, store)
    order = await TicketOrder.get(id=order.id)
    await _post(store, 'PAYMENT.CAPTURE.REFUNDED', {
        'id': 'REF-1', 'amount': {'value': '60.00', 'currency_code': 'USD'},
        'seller_payable_breakdown': {'total_refunded_amount': {'value': '60.00', 'currency_code': 'USD'}},
        'links': [{'rel': 'up', 'href': f'https://api-m.paypal.com/v2/payments/captures/{order.payment_ref}'}],
    }, event_id='WH-R')
    assert (await TicketOrder.get(id=order.id)).status is TicketOrderStatus.REFUNDED
    assert {t.status for t in await Ticket.filter(order_id=order.id)} == {TicketStatus.REFUNDED}


async def test_staff_refund_goes_to_the_capture_in_the_order_currency(staff, buyer, weekend, store, mock_paypal):
    order, _ = await _approved(buyer, weekend, store)
    order = await TicketOrder.get(id=order.id)
    await TicketService().refund_order(staff, order.id)
    assert mock_paypal == [(order.payment_ref, 6000, 'usd', f'wizzrobe-refund-{order.id}')]


async def test_buyers_see_paypal_once_it_is_connected(weekend):
    assert await TicketStoreService().connected_providers() == [TicketProvider.PAYPAL]


def test_amounts_follow_paypals_decimal_rules():
    assert to_value(6000, 'usd') == '60.00' and to_value(5, 'eur') == '0.05'
    assert to_value(6000, 'jpy') == '6000' and to_cents('60.00', 'USD') == 6000 and to_cents('6000', 'JPY') == 6000


def test_unhandled_events_are_ignored():
    body = json.dumps({'id': 'WH', 'event_type': 'CUSTOMER.DISPUTE.CREATED', 'resource': {}}).encode()
    assert parse_event(body) is None


async def test_the_hub_card_and_claim_links_follow_the_buyers_order(buyer, weekend, store):
    teaser = await TicketService().hub_teaser(buyer)
    assert (teaser.on_sale, teaser.from_cents, teaser.currency, teaser.owned) == (1, 6000, 'usd', 0)

    await _approved(buyer, weekend, store, quantity=2)
    assert (await TicketService().hub_teaser(buyer)).owned == 1
    (extra,) = (await TicketService().my_badges(buyer)).extras
    assert extra.claim_path.startswith('/badges/claim/')
    assert extra.claim_url.startswith('http') and extra.claim_url.endswith(extra.claim_path)


async def test_no_hub_card_with_sales_off(buyer, weekend):
    await TenantFeatureFlag.filter(tenant_id=DEFAULT_TEST_TENANT_ID, flag=FeatureFlag.TICKET_SALES.value).update(enabled=False)
    reset_flag_cache()
    assert await TicketService().hub_teaser(buyer) is None
