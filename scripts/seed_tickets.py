"""Dev seed for ticket sales.

A test-mode Stripe store and a sandbox PayPal store (fake credentials: run with
``MOCK_STRIPE=1`` / ``MOCK_PAYPAL=1`` to buy against them, or connect real
test credentials on Admin → Tickets), three
badge types in the default event (Weekend covers comps, VIP is capped at 50,
Sunday is off sale), and four orders for ``player_one``: a paid one with their
own Weekend badge plus an unclaimed extra, one mid-checkout, one that lapsed
and one refunded, plus a paid PayPal order. So My badges, the claim link, the orders list and the
comp-refunds panel all have something to show.
"""

import json
import secrets
from datetime import datetime, timedelta, timezone

from application.services.event_service import EventService
from application.services.ticket_service import CLAIM_PURPOSE, claim_lookup
from application.services.ticket_store_service import HOOK_SEAL_PURPOSE, STORE_SEAL_PURPOSE
from application.utils.secret_box import seal
from models import (
    CheckInTierLanyard,
    Tenant,
    Ticket,
    TicketOrder,
    TicketOrderStatus,
    TicketProvider,
    TicketStatus,
    TicketStore,
    TicketType,
    User,
)

_TYPES = [
    ('Weekend badge', 'Thu–Sun, all four days', 6000, 300, CheckInTierLanyard.BASE, True, 0),
    ('VIP badge', 'Weekend badge, a reserved seat at the main stage and a shirt', 12000, 50, CheckInTierLanyard.VIP, False, 1),
    ('Sunday only', 'One day, Sunday', 3000, 0, CheckInTierLanyard.DAY_PASS, False, 2),
]


async def seed_tickets_for_tenant(tenant: Tenant, buyer: User) -> None:
    """Idempotent: types by name, the order by its fake Stripe session id."""
    event_id = await EventService.default_event_id(tenant.id)
    if event_id is None:
        return
    context = str(tenant.id)
    await TicketStore.get_or_create(
        tenant=tenant, provider=TicketProvider.STRIPE,
        defaults={'account_label': 'Seed Stripe account (test mode)',
                  'secret': seal('rk_test_seed', purpose=STORE_SEAL_PURPOSE, context=context),
                  'webhook_secret': seal('whsec_seed', purpose=HOOK_SEAL_PURPOSE, context=context),
                  'webhook_id': 'we_seed', 'webhook_token': f'seed-{tenant.slug}', 'test_mode': True},
    )
    paypal_creds = json.dumps({'client_id': 'seed-client', 'client_secret': 'seed-secret', 'sandbox': True})
    await TicketStore.get_or_create(
        tenant=tenant, provider=TicketProvider.PAYPAL,
        defaults={'secret': seal(paypal_creds, purpose=STORE_SEAL_PURPOSE, context=context),
                  'webhook_id': 'WH-SEED', 'webhook_token': f'seed-paypal-{tenant.slug}', 'test_mode': True},
    )
    types = {}
    for name, description, price, quantity, lanyard, covers_comp, order in _TYPES:
        row, _ = await TicketType.get_or_create(
            name=name, tenant=tenant, event_id=event_id,
            defaults={'description': description, 'price_cents': price, 'quantity': quantity or None,
                      'lanyard': lanyard, 'covers_comp': covers_comp, 'sort_order': order,
                      'is_active': quantity != 0},
        )
        types[name] = row

    ref = f'cs_seed_{tenant.slug}'
    if await TicketOrder.filter(tenant=tenant, provider_ref=ref).exists():
        return
    now = datetime.now(timezone.utc)
    weekend = types['Weekend badge']
    order = await TicketOrder.create(
        tenant=tenant, event_id=event_id, buyer=buyer, provider=TicketProvider.STRIPE,
        status=TicketOrderStatus.PAID, total_cents=2 * weekend.price_cents, currency='usd',
        provider_ref=ref, payment_ref=f'pi_seed_{tenant.slug}', hold_expires_at=now, paid_at=now,
    )
    await Ticket.create(
        tenant=tenant, event_id=event_id, order=order, ticket_type=weekend, owner=buyer,
        status=TicketStatus.VALID, price_cents=weekend.price_cents, code=f'SEED{tenant.id:04d}',
    )
    token = f'seed-claim-{tenant.slug}-{secrets.token_hex(4)}'
    await Ticket.create(
        tenant=tenant, event_id=event_id, order=order, ticket_type=weekend, status=TicketStatus.VALID,
        price_cents=weekend.price_cents, code=f'SEEX{tenant.id:04d}',
        claim_token=seal(token, purpose=CLAIM_PURPOSE, context=str(tenant.id)), claim_lookup=claim_lookup(token),
    )
    vip = types['VIP badge']
    for status, ticket_status, hold, suffix in (
        (TicketOrderStatus.PENDING, TicketStatus.PENDING, now + timedelta(minutes=30), 'P'),
        (TicketOrderStatus.EXPIRED, TicketStatus.VOID, now - timedelta(days=1), 'X'),
        (TicketOrderStatus.REFUNDED, TicketStatus.REFUNDED, now - timedelta(days=2), 'R'),
    ):
        paid = status is TicketOrderStatus.REFUNDED
        other = await TicketOrder.create(
            tenant=tenant, event_id=event_id, buyer=buyer, provider=TicketProvider.STRIPE, status=status,
            total_cents=vip.price_cents, refunded_cents=vip.price_cents if paid else 0, currency='usd',
            provider_ref=f'{ref}_{suffix.lower()}', payment_ref=f'pi_seed_{tenant.slug}_{suffix.lower()}' if paid else None,
            hold_expires_at=hold, paid_at=hold if paid else None,
        )
        await Ticket.create(
            tenant=tenant, event_id=event_id, order=other, ticket_type=vip, status=ticket_status,
            price_cents=vip.price_cents, code=f'SE{suffix}{tenant.id:05d}', refunded_at=now if paid else None,
        )
    paypal_order = await TicketOrder.create(
        tenant=tenant, event_id=event_id, buyer=buyer, provider=TicketProvider.PAYPAL, status=TicketOrderStatus.PAID,
        total_cents=weekend.price_cents, currency='usd', provider_ref=f'{ref}_paypal',
        payment_ref=f'SEED-CAPTURE-{tenant.slug}', hold_expires_at=now, paid_at=now,
    )
    await Ticket.create(
        tenant=tenant, event_id=event_id, order=paypal_order, ticket_type=weekend, owner=buyer,
        status=TicketStatus.VALID, price_cents=weekend.price_cents, code=f'SPP{tenant.id:05d}',
    )
    print(f'    [{tenant.slug}] tickets ok (claim link /badges/claim/{token})')
