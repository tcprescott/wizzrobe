"""Ticket sales: badge types, checkout holds, the Stripe webhook, claim links, refunds and comp refunds."""

import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from application.errors import NotFoundError
from application.services.audit_service import AuditActions
from application.services.ticket_checkout_service import TicketCheckoutService
from application.services.ticket_comp_refund_service import TicketCompRefundService
from application.services.ticket_gateways import GatewaySignatureError
from application.services.ticket_service import TicketService
from application.services.ticket_store_service import HOOK_SEAL_PURPOSE, TicketStoreService
from application.utils.clients import stripe_client as stripe_module
from application.utils.clients.stripe_client import StripeSignatureError, sign, verify_signature
from application.utils.secret_box import open_
from models import (
    AuditLog,
    CheckInEvent,
    CheckInEventStatus,
    Role,
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

KEY = 'rk_test_abc123'


@pytest.fixture(autouse=True)
def mock_stripe(monkeypatch):
    monkeypatch.setenv('MOCK_STRIPE', '1')
    refunds = []

    async def refund(self, payment_ref, amount, *, idempotency_key):
        refunds.append((payment_ref, amount, idempotency_key))
        return 're_test'

    monkeypatch.setattr(stripe_module.MockStripeClient, 'refund', refund)
    return refunds


@pytest.fixture
async def staff():
    user = await make_user(discord_id=700, username='staffer')
    await UserRole.create(user=user, role=Role.STAFF, tenant_id=DEFAULT_TEST_TENANT_ID)
    await TenantMembership.create(user=user, tenant_id=DEFAULT_TEST_TENANT_ID)
    return user


async def _member(discord_id: int, name: str):
    user = await make_user(discord_id=discord_id, username=name)
    await TenantMembership.create(user=user, tenant_id=DEFAULT_TEST_TENANT_ID)
    return user


@pytest.fixture
async def buyer():
    return await _member(701, 'buyer')


@pytest.fixture
async def store(staff):
    await TicketStoreService().connect(staff, TicketProvider.STRIPE, KEY)
    return await TicketStore.get(provider=TicketProvider.STRIPE)


@pytest.fixture
async def weekend(staff, store):
    return await TicketService().create_type(
        staff, name='Weekend badge', price_cents=6000, quantity=3, covers_comp=True,
    )


@pytest.fixture
async def vip(staff, store):
    return await TicketService().create_type(staff, name='VIP badge', price_cents=12000, lanyard='vip')


def _secret(store: TicketStore) -> str:
    return open_(store.webhook_secret, purpose=HOOK_SEAL_PURPOSE, context=str(store.tenant_id))


async def _post(store: TicketStore, payload: dict, *, secret=None, stamp=None):
    body = json.dumps(payload).encode()
    headers = {'Stripe-Signature': sign(body, secret or _secret(store), stamp)}
    return await TicketCheckoutService().handle_webhook(TicketProvider.STRIPE, store.webhook_token, body, headers)


def _paid(session_id: str, intent: str = 'pi_1', event_id: str = 'evt_1') -> dict:
    return {'id': event_id, 'type': 'checkout.session.completed',
            'data': {'object': {'id': session_id, 'payment_status': 'paid', 'payment_intent': intent}}}


async def _buy(buyer, quantities, store):
    start = await TicketCheckoutService().start_checkout(buyer, TicketProvider.STRIPE, quantities)
    order = await TicketOrder.get(id=start.order_id)
    await _post(store, _paid(order.provider_ref, intent=f'pi_{order.id}', event_id=f'evt_{order.id}'))
    return await TicketOrder.get(id=order.id)


# ── Stores ────────────────────────────────────────────────────────────────

async def test_connecting_seals_the_key_and_never_shows_it(staff, store):
    assert KEY not in store.secret and store.webhook_secret
    views = await TicketStoreService().list_stores(staff)
    assert [(v.provider, v.test_mode) for v in views] == [(TicketProvider.STRIPE, True)]
    log = await AuditLog.get(action=AuditActions.TICKET_STORE_CONNECTED)
    assert KEY not in json.dumps(log.details)


async def test_a_secret_key_is_refused_with_a_pointer_to_restricted_keys(staff):
    with pytest.raises(ValueError, match='restricted key'):
        await TicketStoreService().connect(staff, TicketProvider.STRIPE, 'sk_live_nope')


async def test_only_staff_connect_stores(buyer):
    with pytest.raises(PermissionError):
        await TicketStoreService().connect(buyer, TicketProvider.STRIPE, KEY)


# ── Badge types ───────────────────────────────────────────────────────────

async def test_one_badge_covers_comps_at_a_time(staff, weekend, vip):
    await TicketService().update_type(staff, vip.id, covers_comp=True)
    rows = {a.ticket_type.name: a.ticket_type.covers_comp for a in await TicketService().list_types(staff)}
    assert rows == {'Weekend badge': False, 'VIP badge': True}


async def test_badge_types_refuse_free_and_mixed_currencies(staff, weekend):
    with pytest.raises(ValueError, match='at least'):
        await TicketService().create_type(staff, name='Free', price_cents=0)
    with pytest.raises(ValueError, match='USD'):
        await TicketService().create_type(staff, name='Euro', price_cents=500, currency='eur')


# ── Checkout ──────────────────────────────────────────────────────────────

async def test_checkout_holds_badges_until_the_hold_lapses(buyer, weekend):
    service = TicketCheckoutService()
    await service.start_checkout(buyer, TicketProvider.STRIPE, {weekend.id: 3})
    with pytest.raises(ValueError, match='Sold out'):
        await service.start_checkout(buyer, TicketProvider.STRIPE, {weekend.id: 1})

    await TicketOrder.all().update(hold_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    await service.start_checkout(buyer, TicketProvider.STRIPE, {weekend.id: 1})
    assert await TicketOrder.filter(status=TicketOrderStatus.EXPIRED).count() == 1
    assert await Ticket.filter(status=TicketStatus.VOID).count() == 3


async def test_checkout_respects_the_per_order_cap(buyer, staff, weekend):
    await TicketService().update_type(staff, weekend.id, max_per_order=1, quantity=None)
    with pytest.raises(ValueError, match='up to 1 per order'):
        await TicketCheckoutService().start_checkout(buyer, TicketProvider.STRIPE, {weekend.id: 2})


# ── Webhook ───────────────────────────────────────────────────────────────

async def test_paid_webhook_gives_the_buyer_one_badge_and_claim_links_for_the_rest(buyer, store, weekend):
    order = await _buy(buyer, {weekend.id: 2}, store)

    assert order.status is TicketOrderStatus.PAID and order.payment_ref == f'pi_{order.id}'
    mine = await TicketService().my_badges(buyer)
    assert [t.owner_id for t in mine.own] == [buyer.id]
    assert len(mine.extras) == 1 and mine.extras[0].claim_path.startswith('/badges/claim/')
    assert await AuditLog.filter(action=AuditActions.TICKET_ORDER_PAID).count() == 1


async def test_a_repeated_webhook_settles_the_order_once(buyer, store, weekend):
    order = await _buy(buyer, {weekend.id: 1}, store)
    again = await _post(store, _paid(order.provider_ref, intent=order.payment_ref, event_id='evt_again'))
    assert again == 'already settled'
    assert await AuditLog.filter(action=AuditActions.TICKET_ORDER_PAID).count() == 1


async def test_a_forged_or_stale_webhook_changes_nothing(buyer, store, weekend):
    start = await TicketCheckoutService().start_checkout(buyer, TicketProvider.STRIPE, {weekend.id: 1})
    order = await TicketOrder.get(id=start.order_id)
    with pytest.raises(GatewaySignatureError):
        await _post(store, _paid(order.provider_ref), secret='whsec_forged')
    with pytest.raises(GatewaySignatureError):
        await _post(store, _paid(order.provider_ref), stamp=int(time.time()) - 3600)
    with pytest.raises(NotFoundError):
        await TicketCheckoutService().handle_webhook(TicketProvider.STRIPE, 'nope', b'{}', {})
    assert (await TicketOrder.get(id=order.id)).status is TicketOrderStatus.PENDING


async def test_a_dashboard_refund_voids_the_badges(buyer, store, weekend):
    order = await _buy(buyer, {weekend.id: 1}, store)
    await _post(store, {'id': 'evt_r', 'type': 'charge.refunded',
                        'data': {'object': {'payment_intent': order.payment_ref, 'amount_refunded': 6000}}})
    assert (await TicketOrder.get(id=order.id)).status is TicketOrderStatus.REFUNDED
    assert {t.status for t in await Ticket.filter(order_id=order.id)} == {TicketStatus.REFUNDED}


def test_signature_check_matches_stripes_scheme():
    body = b'{"x":1}'
    verify_signature(body, sign(body, 'whsec_1'), 'whsec_1')
    with pytest.raises(StripeSignatureError):
        verify_signature(body, sign(body, 'whsec_1'), 'whsec_2')


# ── Claim links ───────────────────────────────────────────────────────────

async def test_a_claim_link_works_once_for_someone_else(buyer, store, weekend):
    await _buy(buyer, {weekend.id: 2}, store)
    path = (await TicketService().my_badges(buyer)).extras[0].claim_path
    token = path.rsplit('/', 1)[1]
    friend = await _member(702, 'friend')

    preview = await TicketService().preview_claim(buyer, token)
    assert not preview.claimable and 'You bought this one' in preview.reason

    badge = await TicketService().claim(friend, token)
    assert badge.owner_id == friend.id
    with pytest.raises(NotFoundError):
        await TicketService().claim(await _member(703, 'late'), token)


# ── Refunds ───────────────────────────────────────────────────────────────

async def test_staff_refund_sends_whats_left_and_voids_the_badges(staff, buyer, store, weekend, mock_stripe):
    order = await _buy(buyer, {weekend.id: 2}, store)
    refunded = await TicketService().refund_order(staff, order.id)
    assert refunded.status is TicketOrderStatus.REFUNDED
    assert mock_stripe == [(order.payment_ref, 12000, f'wizzrobe-refund-{order.id}')]
    assert {t.status for t in refunded.tickets} == {TicketStatus.REFUNDED}


# ── Comp refunds ──────────────────────────────────────────────────────────

async def test_comped_buyers_are_owed_up_to_the_comp_badge_price(staff, store, weekend, vip, mock_stripe):
    await CheckInEvent.create(name='SGL', status=CheckInEventStatus.OPEN, comp_roles=[Role.PROCTOR.value])
    weekend_buyer = await _member(710, 'weekend')
    vip_buyer = await _member(711, 'vip')
    stranger = await _member(712, 'stranger')
    for user in (weekend_buyer, vip_buyer):
        await UserRole.create(user=user, role=Role.PROCTOR, tenant_id=DEFAULT_TEST_TENANT_ID)
    await _buy(weekend_buyer, {weekend.id: 2}, store)
    await _buy(vip_buyer, {vip.id: 1}, store)
    await _buy(stranger, {weekend.id: 1}, store)

    view = await TicketCompRefundService().owed(staff)
    owed = {r.owner.id: (r.paid_cents, r.amount_cents, r.reason_labels) for r in view.rows}
    assert owed == {weekend_buyer.id: (6000, 6000, ['Proctor']), vip_buyer.id: (12000, 6000, ['Proctor'])}

    results = await TicketCompRefundService().refund(staff, [r.ticket.id for r in view.rows] + [999])
    assert [(r.ok, r.amount_cents) for r in results] == [(True, 6000), (True, 6000), (False, 0)]
    assert sorted(a for _, a, _ in mock_stripe) == [6000, 6000]
    assert (await TicketCompRefundService().owed(staff)).rows == []
    vip_order = await TicketOrder.get(buyer_id=vip_buyer.id)
    assert vip_order.status is TicketOrderStatus.PAID and vip_order.refunded_cents == 6000


async def test_nobody_is_owed_until_staff_pick_the_comp_badge(staff, store, vip):
    await CheckInEvent.create(name='SGL', status=CheckInEventStatus.OPEN, comp_roles=[Role.PROCTOR.value])
    view = await TicketCompRefundService().owed(staff)
    assert view.rows == [] and view.comp_type is None and view.has_comp_rules
