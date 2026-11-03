"""
Ticket Service - Business Logic Layer

The badges an event sells and the people who hold them. Staff set up badge
types (price, how many, sale window, the lanyard the desk hands out, which one
a comp stands in for) and see and refund orders. A signed-in member sees what's
on sale, their own badges, and the extras they bought with a claim link each;
whoever opens a claim link signed in takes that badge.

Buying lives in :mod:`ticket_checkout_service` and comp refunds in
:mod:`ticket_comp_refund_service`. Gated by :attr:`~models.FeatureFlag.TICKET_SALES`.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from application.errors import require_found
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
from application.services.ticket_dm import ticket_claimed
from application.services.ticket_store_service import store_gateway
from application.utils.secret_box import open_
from models import (
    CheckInTierLanyard,
    FeatureFlag,
    Ticket,
    TicketOrder,
    TicketOrderStatus,
    TicketStatus,
    TicketType,
    User,
)

CLAIM_PURPOSE = 'ticket_claim'
CLAIM_PATH = '/badges/claim/'
NAME_MAX = 100
DESCRIPTION_MAX = 500
MIN_PRICE_CENTS = 50
MAX_PER_ORDER_CAP = 20


def claim_lookup(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def open_claim_token(ticket: Ticket) -> Optional[str]:
    if not ticket.claim_token:
        return None
    return open_(ticket.claim_token, purpose=CLAIM_PURPOSE, context=str(ticket.tenant_id))


@dataclass(frozen=True)
class TypeAvailability:
    """A badge type with what's left of it right now."""

    ticket_type: TicketType
    sold: int
    remaining: Optional[int]
    on_sale: bool
    reason: Optional[str]


@dataclass(frozen=True)
class ExtraBadge:
    ticket: Ticket
    claim_path: Optional[str]


@dataclass(frozen=True)
class MyBadges:
    own: List[Ticket]
    extras: List[ExtraBadge]


@dataclass(frozen=True)
class ClaimPreview:
    ticket: Ticket
    buyer_name: str
    claimable: bool
    reason: Optional[str]


def sale_state(ticket_type: TicketType, taken: int, now: datetime) -> tuple[Optional[int], bool, Optional[str]]:
    """``(remaining, on_sale, why not)`` for one type at ``now``."""
    remaining = None if ticket_type.quantity is None else max(0, ticket_type.quantity - taken)
    if not ticket_type.is_active:
        return remaining, False, 'Not on sale'
    if ticket_type.sales_open_at and now < ticket_type.sales_open_at:
        return remaining, False, 'Sales open later'
    if ticket_type.sales_close_at and now >= ticket_type.sales_close_at:
        return remaining, False, 'Sales closed'
    if remaining == 0:
        return remaining, False, 'Sold out'
    return remaining, True, None


def _clean_text(value: Optional[str], limit: int, what: str) -> Optional[str]:
    text = (value or '').strip()
    if len(text) > limit:
        raise ValueError(f'{what} must be {limit} characters or fewer.')
    return text or None


class TicketService:
    """Badge types, orders and badges for the event in scope."""

    def __init__(self) -> None:
        self.types = TicketTypeRepository()
        self.orders = TicketOrderRepository()
        self.tickets = TicketRepository()
        self.stores = TicketStoreRepository()
        self.audit_service = AuditService()

    @staticmethod
    async def _ensure_staff(actor: User) -> None:
        await AuthService.ensure(await AuthService.can_manage_tickets(actor), 'Only staff can manage tickets.')

    async def _availability(self, rows: List[TicketType]) -> List[TypeAvailability]:
        now = datetime.now(timezone.utc)
        ids = [row.id for row in rows]
        taken = await self.tickets.taken_counts(ids, now)
        sold = await self.tickets.sold_counts(ids)
        out = []
        for row in rows:
            remaining, on_sale, reason = sale_state(row, taken.get(row.id, 0), now)
            out.append(TypeAvailability(row, sold.get(row.id, 0), remaining, on_sale, reason))
        return out

    # ── Badge types (staff) ──────────────────────────────────────────────

    @requires_feature(FeatureFlag.TICKET_SALES)
    async def list_types(self, actor: User) -> List[TypeAvailability]:
        await self._ensure_staff(actor)
        return await self._availability(await self.types.list_all())

    async def _validated(self, fields: Dict[str, Any], current: Optional[TicketType]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        if 'name' in fields or current is None:
            name = _clean_text(fields.get('name'), NAME_MAX, 'Name')
            if name is None:
                raise ValueError('Give the badge a name.')
            out['name'] = name
        if 'description' in fields:
            out['description'] = _clean_text(fields['description'], DESCRIPTION_MAX, 'Description')
        if 'price_cents' in fields or current is None:
            price = fields.get('price_cents')
            if not isinstance(price, int) or price < MIN_PRICE_CENTS:
                raise ValueError('A badge costs at least 0.50. Free entry is a comp or a walk-up at the desk.')
            out['price_cents'] = price
        if 'currency' in fields or current is None:
            currency = str(fields.get('currency') or 'usd').strip().lower()
            if len(currency) != 3 or not currency.isalpha():
                raise ValueError('Currency is a three-letter code, like USD.')
            others = [t for t in await self.types.list_all() if current is None or t.id != current.id]
            if others and any(t.currency != currency for t in others):
                raise ValueError(f'Every badge for this event is priced in {others[0].currency.upper()}.')
            out['currency'] = currency
        if 'quantity' in fields:
            quantity = fields['quantity']
            if quantity is not None and (not isinstance(quantity, int) or quantity < 1):
                raise ValueError('Quantity is at least 1, or leave it empty for no limit.')
            out['quantity'] = quantity
        if 'max_per_order' in fields:
            cap = fields['max_per_order']
            if not isinstance(cap, int) or not 1 <= cap <= MAX_PER_ORDER_CAP:
                raise ValueError(f'Per order is between 1 and {MAX_PER_ORDER_CAP}.')
            out['max_per_order'] = cap
        for key in ('sales_open_at', 'sales_close_at'):
            if key in fields:
                out[key] = fields[key]
        opens = out.get('sales_open_at', current.sales_open_at if current else None)
        closes = out.get('sales_close_at', current.sales_close_at if current else None)
        if opens and closes and closes <= opens:
            raise ValueError('Sales have to close after they open.')
        if 'lanyard' in fields:
            out['lanyard'] = CheckInTierLanyard(fields['lanyard'])
        for key in ('covers_comp', 'is_active'):
            if key in fields:
                out[key] = bool(fields[key])
        if 'sort_order' in fields:
            out['sort_order'] = int(fields['sort_order'])
        return out

    @requires_feature(FeatureFlag.TICKET_SALES)
    async def create_type(self, actor: User, **fields: Any) -> TicketType:
        await self._ensure_staff(actor)
        values = await self._validated(fields, None)
        row = await self.types.create(**values)
        if row.covers_comp:
            await self.types.clear_comp_flag(row.id)
        await self.audit_service.write_log(actor, AuditActions.TICKET_TYPE_CREATED, {
            'ticket_type_id': row.id, 'name': row.name, 'price_cents': row.price_cents,
        })
        return row

    @requires_feature(FeatureFlag.TICKET_SALES)
    async def update_type(self, actor: User, type_id: int, **fields: Any) -> TicketType:
        await self._ensure_staff(actor)
        row = require_found(await self.types.get_by_id(type_id), 'Badge type')
        values = await self._validated(fields, row)
        changed = {k: v for k, v in values.items() if getattr(row, k) != v}
        if changed:
            await self.types.update(row, **changed)
            if changed.get('covers_comp'):
                await self.types.clear_comp_flag(row.id)
            await self.audit_service.write_log(actor, AuditActions.TICKET_TYPE_UPDATED, {
                'ticket_type_id': row.id, 'changed': sorted(changed),
            })
        return row

    @requires_feature(FeatureFlag.TICKET_SALES)
    async def delete_type(self, actor: User, type_id: int) -> None:
        await self._ensure_staff(actor)
        row = require_found(await self.types.get_by_id(type_id), 'Badge type')
        if await self.types.has_tickets(row.id):
            raise ValueError('Badges of this type have been ordered. Take it off sale instead.')
        await self.types.delete(row)
        await self.audit_service.write_log(actor, AuditActions.TICKET_TYPE_DELETED, {
            'ticket_type_id': type_id, 'name': row.name,
        })

    # ── Buying side (members) ────────────────────────────────────────────

    @requires_feature(FeatureFlag.TICKET_SALES)
    async def on_sale(self, actor: User) -> List[TypeAvailability]:
        """Active badge types with what's left, for a member. Empty until a store is connected."""
        if not await TenantMembershipService().is_member(actor) and not await AuthService.is_staff(actor):
            raise ValueError('Join this community to buy badges.')
        if not await self.stores.list_all():
            return []
        return await self._availability(await self.types.list_all(active_only=True))

    @requires_feature(FeatureFlag.TICKET_SALES)
    async def my_badges(self, actor: User) -> MyBadges:
        own = await self.tickets.owned_by(actor.id)
        extras = [
            ExtraBadge(t, None if t.owner_id else (CLAIM_PATH + token if (token := open_claim_token(t)) else None))
            for t in await self.tickets.extras_bought_by(actor.id)
        ]
        return MyBadges(own, extras)

    @requires_feature(FeatureFlag.TICKET_SALES)
    async def my_order(self, actor: User, order_id: int) -> TicketOrder:
        """One of the actor's own orders, for the page the store sends them back to."""
        order = await self.orders.get_with_tickets(order_id)
        return require_found(order if order and order.buyer_id == actor.id else None, 'Order')

    async def _claim_target(self, token: str) -> Ticket:
        ticket = await self.tickets.get_by_claim_lookup(claim_lookup((token or '').strip()))
        return require_found(ticket, 'Badge')

    @requires_feature(FeatureFlag.TICKET_SALES)
    async def preview_claim(self, actor: User, token: str) -> ClaimPreview:
        ticket = await self._claim_target(token)
        buyer = ticket.order.buyer
        reason = None
        if ticket.status is not TicketStatus.VALID:
            reason = 'This badge was refunded.'
        elif ticket.owner_id is not None:
            reason = 'This badge has already been claimed. Ask for a new link if it was meant for you.'
        elif buyer.id == actor.id:
            reason = "You bought this one. Send the link to the person it's for."
        elif await self._already_holds(actor):
            reason = 'You already have a badge for this event.'
        return ClaimPreview(ticket, buyer.preferred_name, reason is None, reason)

    async def _already_holds(self, actor: User) -> bool:
        return any(t.status is TicketStatus.VALID for t in await self.tickets.owned_by(actor.id))

    @requires_feature(FeatureFlag.TICKET_SALES)
    async def claim(self, actor: User, token: str) -> Ticket:
        preview = await self.preview_claim(actor, token)
        if not preview.claimable:
            raise ValueError(preview.reason)
        if not await TenantMembershipService().is_member(actor):
            raise ValueError('Join this community before claiming a badge.')
        ticket = preview.ticket
        ticket.owner_id = actor.id
        ticket.claimed_at = datetime.now(timezone.utc)
        ticket.claim_token = None
        ticket.claim_lookup = None
        await self.tickets.save(ticket, 'owner_id', 'claimed_at', 'claim_token', 'claim_lookup')
        await self.audit_service.write_log(actor, AuditActions.TICKET_CLAIMED, {
            'ticket_id': ticket.id, 'order_id': ticket.order_id,
        })
        await ticket_claimed(ticket.order.buyer, actor.preferred_name, ticket.ticket_type.name)
        return ticket

    # ── Orders (staff) ───────────────────────────────────────────────────

    @requires_feature(FeatureFlag.TICKET_SALES)
    async def list_orders(self, actor: User, *, include_unpaid: bool = False) -> List[TicketOrder]:
        await self._ensure_staff(actor)
        statuses = None if include_unpaid else [TicketOrderStatus.PAID, TicketOrderStatus.REFUNDED]
        return await self.orders.list_for_event(statuses=statuses)

    @requires_feature(FeatureFlag.TICKET_SALES)
    async def refund_order(self, actor: User, order_id: int) -> TicketOrder:
        """Refund what's left of a paid order through its store and void its badges."""
        await self._ensure_staff(actor)
        order = require_found(await self.orders.get_with_tickets(order_id), 'Order')
        if order.status is not TicketOrderStatus.PAID:
            raise ValueError('Only a paid order can be refunded.')
        remaining = order.total_cents - order.refunded_cents
        if remaining > 0:
            store = await self.stores.get_by_provider(order.provider)
            if store is None or not order.payment_ref:
                raise ValueError(f'{order.provider.label} isn\'t connected any more. Refund it there instead.')
            await store_gateway(store).refund(
                order.payment_ref, remaining, currency=order.currency, idempotency_key=f'wizzrobe-refund-{order.id}',
            )
        now = datetime.now(timezone.utc)
        order.status = TicketOrderStatus.REFUNDED
        order.refunded_cents = order.total_cents
        await self.orders.save(order, 'status', 'refunded_cents')
        await self.tickets.set_status_for_order(order.id, TicketStatus.REFUNDED, refunded_at=now)
        await self.audit_service.write_log(actor, AuditActions.TICKET_ORDER_REFUNDED, {
            'order_id': order.id, 'amount_cents': remaining, 'provider': order.provider.value,
        })
        return require_found(await self.orders.get_with_tickets(order.id), 'Order')
