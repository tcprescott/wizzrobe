"""
Ticket Comp Refund Service - Business Logic Layer

Someone the event comps (a comp role, or a volunteer whose scheduled hours
reach the comp tier, by the check-in comp rules) who also paid for their own
badge is owed money back. This lists them as soon as they qualify, and refunds
whichever rows staff pick. Nothing is ever refunded on its own: a staff member
presses Refund every time.

The amount is up to the price of the badge type marked ``covers_comp``: a
Weekend buyer gets the Weekend price back, a VIP buyer keeps paying the VIP
difference. Only the badge the person bought for themselves counts; extras
they bought for friends are never touched. The badge stays valid, and the
person gets a DM once the store accepts the refund.

Gated by :attr:`~models.FeatureFlag.TICKET_SALES`.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

from application.feature_flags import requires_feature
from application.repositories import (
    CheckInEventRepository,
    TicketOrderRepository,
    TicketRepository,
    TicketStoreRepository,
    TicketTypeRepository,
)
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.check_in_comps import comped_users, has_comp_rules
from application.services.check_in_rules import VOLUNTEER_COMP
from application.services.feature_flag_service import FeatureFlagService
from application.services.ticket_dm import comp_refund_sent, money
from application.services.ticket_gateways import GatewayError
from application.services.ticket_store_service import store_gateway
from application.services.user_service import UserService
from application.services.volunteer.volunteer_hours import VolunteerHoursService
from models import FeatureFlag, Role, Ticket, TicketType, User


@dataclass(frozen=True)
class CompRefundOwed:
    """One comped person who paid for their own badge, and what they're owed."""

    ticket: Ticket
    owner: User
    reasons: List[str]
    scheduled_hours: Optional[float]
    paid_cents: int
    amount_cents: int
    currency: str

    @property
    def reason_labels(self) -> List[str]:
        return ['Volunteer' if r == VOLUNTEER_COMP else Role(r).label for r in self.reasons]


@dataclass(frozen=True)
class CompRefundsView:
    rows: List[CompRefundOwed]
    comp_type: Optional[TicketType]
    has_comp_rules: bool


@dataclass(frozen=True)
class CompRefundResult:
    ticket_id: int
    ok: bool
    amount_cents: int
    error: Optional[str] = None


class TicketCompRefundService:
    """Who's owed a comp refund, and sending it."""

    def __init__(self) -> None:
        self.tickets = TicketRepository()
        self.orders = TicketOrderRepository()
        self.types = TicketTypeRepository()
        self.stores = TicketStoreRepository()
        self.check_in_events = CheckInEventRepository()
        self.audit_service = AuditService()

    async def _comped(self) -> tuple[Dict[int, List[str]], bool]:
        reasons: Dict[int, List[str]] = {}
        any_rules = False
        for event in await self.check_in_events.list_all():
            if not has_comp_rules(event):
                continue
            any_rules = True
            for user_id, why in (await comped_users(event)).items():
                merged = reasons.setdefault(user_id, [])
                merged += [r for r in why if r not in merged]
        return reasons, any_rules

    async def _hours(self) -> Dict[int, float]:
        if not await FeatureFlagService().is_enabled(FeatureFlag.VOLUNTEERS):
            return {}
        system = await UserService().get_system_user()
        return {s.user_id: s.hours for s in await VolunteerHoursService().roster(actor=system)}

    async def _owed(self) -> CompRefundsView:
        comped, any_rules = await self._comped()
        comp_type = await self.types.comp_type()
        if comp_type is None or not comped:
            return CompRefundsView([], comp_type, any_rules)
        hours = await self._hours() if any(VOLUNTEER_COMP in r for r in comped.values()) else {}
        rows: List[CompRefundOwed] = []
        seen: set[int] = set()
        for ticket in await self.tickets.own_badges_of(comped):
            if ticket.owner_id in seen:
                continue
            seen.add(ticket.owner_id)  # type: ignore[arg-type]
            if ticket.comp_refunded_at is not None:
                continue
            amount = min(ticket.price_cents, comp_type.price_cents)
            if amount <= 0:
                continue
            owner = ticket.owner
            rows.append(CompRefundOwed(
                ticket, owner, comped[owner.id], hours.get(owner.id), ticket.price_cents, amount,
                ticket.order.currency,
            ))
        rows.sort(key=lambda r: r.owner.preferred_name.lower())
        return CompRefundsView(rows, comp_type, any_rules)

    @requires_feature(FeatureFlag.TICKET_SALES)
    async def owed(self, actor: User) -> CompRefundsView:
        await AuthService.ensure(await AuthService.can_manage_tickets(actor), 'Only staff can see comp refunds.')
        return await self._owed()

    @requires_feature(FeatureFlag.TICKET_SALES)
    async def refund(self, actor: User, ticket_ids: Sequence[int]) -> List[CompRefundResult]:
        """Refund each named badge that is still owed; the rest come back as errors.

        One failure never stops the others: each row goes to its own store
        call, and each result says how it went.
        """
        await AuthService.ensure(await AuthService.can_manage_tickets(actor), 'Only staff can send comp refunds.')
        if not ticket_ids:
            raise ValueError('Pick at least one person to refund.')
        owed = {row.ticket.id: row for row in (await self._owed()).rows}
        results: List[CompRefundResult] = []
        for ticket_id in dict.fromkeys(ticket_ids):
            row = owed.get(ticket_id)
            if row is None:
                results.append(CompRefundResult(ticket_id, False, 0, 'Not owed a comp refund any more.'))
                continue
            results.append(await self._refund_one(actor, row))
        return results

    async def _refund_one(self, actor: User, row: CompRefundOwed) -> CompRefundResult:
        ticket, order = row.ticket, row.ticket.order
        store = await self.stores.get_by_provider(order.provider)
        if store is None or not order.payment_ref:
            return CompRefundResult(ticket.id, False, 0, f'{order.provider.label} isn\'t connected any more.')
        try:
            await store_gateway(store).refund(
                order.payment_ref, row.amount_cents, currency=order.currency,
                idempotency_key=f'wizzrobe-comp-{ticket.id}',
            )
        except (GatewayError, ValueError) as exc:
            return CompRefundResult(ticket.id, False, 0, str(exc))
        now = datetime.now(timezone.utc)
        ticket.comp_refund_cents = row.amount_cents
        ticket.comp_refunded_at = now
        ticket.comp_refunded_by_id = actor.id
        await self.tickets.save(ticket, 'comp_refund_cents', 'comp_refunded_at', 'comp_refunded_by_id')
        order.refunded_cents += row.amount_cents
        await self.orders.save(order, 'refunded_cents')
        await self.audit_service.write_log(actor, AuditActions.TICKET_COMP_REFUNDED, {
            'ticket_id': ticket.id, 'order_id': order.id, 'user_id': row.owner.id,
            'amount_cents': row.amount_cents, 'reasons': row.reasons,
        })
        await comp_refund_sent(
            row.owner, ticket.ticket_type.name, money(row.amount_cents, row.currency), order.provider.label,
        )
        return CompRefundResult(ticket.id, True, row.amount_cents)
