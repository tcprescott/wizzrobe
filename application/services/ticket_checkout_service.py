"""
Ticket Checkout Service - Business Logic Layer

Buying badges. A signed-in member picks quantities; this holds those badges for
30 minutes on a ``PENDING`` order and sends them to the store's hosted checkout
(Stripe Checkout, or PayPal). The return page never decides anything: only the
store's signed webhook marks an order paid, issues the badges (the first to the
buyer, each extra with a claim link) and DMs the buyer. Lapsed checkouts free
their hold on their own: availability only counts holds that haven't expired.

Webhooks arrive with no community in scope. :meth:`handle_webhook` finds the
store by the opaque token in its URL, binds that store's community, checks the
signature with that store's secret, then binds the order's own event.

Gated by :attr:`~models.FeatureFlag.TICKET_SALES`; the webhook is the one
exception (see its docstring).
"""

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping, Optional

from tortoise.transactions import in_transaction

from application.errors import NotFoundError
from application.event_context import event_scope
from application.feature_flags import requires_feature
from application.repositories import (
    TicketOrderRepository,
    TicketRepository,
    TicketStoreRepository,
    TicketTypeRepository,
)
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.tenant_membership_service import TenantMembershipService
from application.services.ticket_dm import money, tickets_bought
from application.services.ticket_gateways import GatewayEvent, GatewaySignatureError
from application.services.ticket_service import CLAIM_PURPOSE, claim_lookup, sale_state
from application.services.ticket_store_service import store_gateway, store_webhook_secret
from application.services.user_service import UserService
from application.tenant_context import require_tenant_id, tenant_scope
from application.utils.clients.stripe_client import CheckoutLine
from application.utils.secret_box import seal
from models import FeatureFlag, TicketOrder, TicketOrderStatus, TicketProvider, TicketStatus, User

logger = logging.getLogger(__name__)

HOLD_MINUTES = 30
MAX_BADGES_PER_ORDER = 20
CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
RETURN_PATH = '/event/badges'


async def _return_base() -> str:
    from application.services import notification_links

    link = await notification_links.link_for('Badges', RETURN_PATH)
    if link is None:
        raise ValueError("Couldn't work out this community's address. Try again.")
    return link.url


def new_code() -> str:
    return ''.join(secrets.choice(CODE_ALPHABET) for _ in range(8))


@dataclass(frozen=True)
class CheckoutStart:
    order_id: int
    url: str


class TicketCheckoutService:
    """Start a checkout, and settle it when the store says so."""

    def __init__(self) -> None:
        self.types = TicketTypeRepository()
        self.orders = TicketOrderRepository()
        self.tickets = TicketRepository()
        self.stores = TicketStoreRepository()
        self.audit_service = AuditService()

    @requires_feature(FeatureFlag.TICKET_SALES)
    async def start_checkout(
        self, actor: User, provider: TicketProvider, quantities: Mapping[int, int],
    ) -> CheckoutStart:
        """Hold the badges and open the store's checkout; returns where to send the buyer.

        The store sends the buyer back to this event's Badges screen with
        ``?order=<id>`` either way; the URL is built here, never taken from the
        caller, so a checkout can't be pointed at another site.
        """
        if not await TenantMembershipService().is_member(actor) and not await AuthService.is_staff(actor):
            raise ValueError('Join this community to buy badges.')
        wanted = {int(k): int(v) for k, v in quantities.items() if int(v) > 0}
        if not wanted:
            raise ValueError('Pick at least one badge.')
        if sum(wanted.values()) > MAX_BADGES_PER_ORDER:
            raise ValueError(f'One order holds up to {MAX_BADGES_PER_ORDER} badges.')
        store = await self.stores.get_by_provider(provider)
        if store is None:
            raise ValueError(f'{provider.label} isn\'t set up for this community.')

        now = datetime.now(timezone.utc)
        await self._expire_lapsed(now)
        async with in_transaction():
            rows = {row.id: row for row in await self.types.lock(wanted)}
            if set(rows) != set(wanted):
                raise ValueError('One of those badges is no longer on sale.')
            taken = await self.tickets.taken_counts(wanted, now)
            for type_id, count in wanted.items():
                row = rows[type_id]
                remaining, on_sale, reason = sale_state(row, taken.get(type_id, 0), now)
                if not on_sale:
                    raise ValueError(f'{row.name}: {reason}.')
                if count > row.max_per_order:
                    raise ValueError(f'{row.name}: up to {row.max_per_order} per order.')
                if remaining is not None and count > remaining:
                    raise ValueError(f'{row.name}: only {remaining} left.')
            currencies = {rows[t].currency for t in wanted}
            if len(currencies) != 1:
                raise ValueError('Those badges are priced in different currencies.')
            order = await self.orders.create(
                buyer=actor, provider=provider, currency=currencies.pop(),
                total_cents=sum(rows[t].price_cents * n for t, n in wanted.items()),
                hold_expires_at=now + timedelta(minutes=HOLD_MINUTES),
            )
            for type_id, count in sorted(wanted.items(), key=lambda kv: rows[kv[0]].sort_order):
                for _ in range(count):
                    await self.tickets.create(
                        order=order, ticket_type_id=type_id, price_cents=rows[type_id].price_cents,
                        code=new_code(),
                    )

        lines = [CheckoutLine(rows[t].name, rows[t].price_cents, n) for t, n in wanted.items()]
        back = f'{await _return_base()}?order={order.id}'
        try:
            checkout = await store_gateway(store).create_checkout(
                order_id=order.id, lines=lines, currency=order.currency, success_url=back,
                cancel_url=f'{back}&cancelled=1', expires_at=int(order.hold_expires_at.timestamp()),
                email=None,
            )
        except ValueError:
            await self._void(order, TicketOrderStatus.EXPIRED)
            raise
        order.provider_ref = checkout.provider_ref
        await self.orders.save(order, 'provider_ref')
        return CheckoutStart(order.id, checkout.url)

    async def _expire_lapsed(self, now: datetime) -> None:
        for order in await self.orders.lapsed_pending(now):
            await self._void(order, TicketOrderStatus.EXPIRED)

    async def _void(self, order: TicketOrder, status: TicketOrderStatus) -> bool:
        if not await self.orders.transition(order.id, [TicketOrderStatus.PENDING], status=status):
            return False
        await self.tickets.void_pending(order.id)
        order.status = status
        return True

    # feature-gate: exempt — a store webhook settles money already taken; it must land even if the flag was turned off since.
    async def handle_webhook(
        self, provider: TicketProvider, token: str, body: bytes, headers: Mapping[str, str],
    ) -> Optional[str]:
        """Verify and apply one store webhook. Returns what it did, for the log.

        Unknown token → :class:`NotFoundError`; bad signature →
        :class:`GatewaySignatureError`. Every apply is idempotent: stores retry,
        and a repeat finds the order already settled and changes nothing.
        """
        store = await self.stores.get_by_webhook_token(token)
        if store is None or store.provider is not provider:
            raise NotFoundError('Unknown webhook.')
        with tenant_scope(store.tenant_id):
            secret = store_webhook_secret(store)
            if secret is None:
                raise GatewaySignatureError('No webhook secret on file for this store.')
            event = store_gateway(store).read_webhook(body, {k.lower(): v for k, v in headers.items()}, secret)
            if event is None:
                return 'ignored'
            order = await self._order_for(provider, event)
            if order is None:
                logger.info('ticket webhook %s for an order Wizzrobe never made', event.id)
                return 'unknown order'
            with event_scope(order.event_id, store.tenant_id):
                if event.kind == 'paid':
                    return await self._settle_paid(order, event)
                if event.kind == 'expired':
                    return 'expired' if await self._void(order, TicketOrderStatus.EXPIRED) else 'already settled'
                if event.kind == 'refunded':
                    return await self._settle_refund(order, event)
        return 'ignored'

    async def _order_for(self, provider: TicketProvider, event: GatewayEvent) -> Optional[TicketOrder]:
        if event.order_ref:
            return await self.orders.get_by_provider_ref(provider, event.order_ref)
        if event.payment_ref:
            return await self.orders.get_by_payment_ref(provider, event.payment_ref)
        return None

    async def _settle_paid(self, order: TicketOrder, event: GatewayEvent) -> str:
        now = datetime.now(timezone.utc)
        changed = await self.orders.transition(
            order.id, [TicketOrderStatus.PENDING, TicketOrderStatus.EXPIRED],
            status=TicketOrderStatus.PAID, payment_ref=event.payment_ref, paid_at=now,
        )
        if not changed:
            return 'already settled'
        if order.status is TicketOrderStatus.EXPIRED:
            logger.warning('ticket order %s was paid after its hold lapsed; issuing anyway', order.id)
        tickets = await self.tickets.for_order(order.id)
        buyer = await UserService().get_user_by_id(order.buyer_id)
        holds_one = any(
            t.status is TicketStatus.VALID and t.order_id != order.id for t in await self.tickets.owned_by(order.buyer_id)
        )
        tenant = str(require_tenant_id())
        for i, ticket in enumerate(tickets):
            ticket.status = TicketStatus.VALID
            if i == 0 and not holds_one:
                ticket.owner_id = order.buyer_id
                await self.tickets.save(ticket, 'status', 'owner_id')
                continue
            token = secrets.token_urlsafe(18)
            ticket.claim_token = seal(token, purpose=CLAIM_PURPOSE, context=tenant)
            ticket.claim_lookup = claim_lookup(token)
            await self.tickets.save(ticket, 'status', 'claim_token', 'claim_lookup')
        if buyer is not None:
            await self.audit_service.write_log(buyer, AuditActions.TICKET_ORDER_PAID, {
                'order_id': order.id, 'badges': len(tickets), 'total_cents': order.total_cents,
                'provider': order.provider.value,
            })
            await tickets_bought(buyer, len(tickets), money(order.total_cents, order.currency))
        return 'paid'

    async def _settle_refund(self, order: TicketOrder, event: GatewayEvent) -> str:
        """Mirror a refund made in the store's own dashboard.

        Refunds made from Wizzrobe already raised ``refunded_cents``, so their
        webhook finds nothing new. A partial refund from the dashboard is only
        recorded; a full one voids the badges.
        """
        if event.amount_refunded <= order.refunded_cents:
            return 'already settled'
        order.refunded_cents = event.amount_refunded
        fields = ['refunded_cents']
        full = event.amount_refunded >= order.total_cents and order.status is TicketOrderStatus.PAID
        if full:
            order.status = TicketOrderStatus.REFUNDED
            fields.append('status')
        await self.orders.save(order, *fields)
        if full:
            await self.tickets.set_status_for_order(order.id, TicketStatus.REFUNDED, refunded_at=datetime.now(timezone.utc))
        system = await UserService().get_system_user()
        await self.audit_service.write_log(system, AuditActions.TICKET_ORDER_REFUNDED, {
            'order_id': order.id, 'refunded_total_cents': event.amount_refunded,
            'provider': order.provider.value, 'from_store_dashboard': True,
        })
        return 'refunded' if full else 'partly refunded'
