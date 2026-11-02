"""Dev seed for ticket sales.

Three badge types in the default event (Weekend covers comps, VIP is capped at 50,
Sunday is off sale), and one paid Stripe order for
``player_one``: their own Weekend badge plus an unclaimed extra, so My badges,
the claim link and the comp-refunds panel all have something to show.

# seed-exempt: TicketStore — it holds a sealed store key; connect a test-mode Stripe key by hand, or run with MOCK_STRIPE=1.
"""

import secrets
from datetime import datetime, timezone

from application.services.event_service import EventService
from application.services.ticket_service import CLAIM_PURPOSE, claim_lookup
from application.utils.secret_box import seal
from models import (
    CheckInTierLanyard,
    Tenant,
    Ticket,
    TicketOrder,
    TicketOrderStatus,
    TicketProvider,
    TicketStatus,
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
    print(f'    [{tenant.slug}] tickets ok (claim link /badges/claim/{token})')
