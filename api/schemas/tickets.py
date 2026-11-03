"""Schemas for ticket sales: stores, badge types, orders, badges and comp refunds."""

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from application.services.ticket_comp_refund_service import CompRefundOwed, CompRefundResult, CompRefundsView
from application.services.ticket_service import ClaimPreview, ExtraBadge, MyBadges, TypeAvailability
from application.services.ticket_store_service import StoreView
from models import CheckInTierLanyard, Ticket, TicketOrder, TicketProvider


class StoreResponse(BaseModel):
    provider: str = Field(..., description="`stripe` or `paypal`.")
    account_label: Optional[str] = Field(None, description="The account's business name, when the key may read it.")
    test_mode: bool = Field(..., description="True for a test-mode key: no real money moves.")
    connected_by: Optional[str] = Field(None, description="Who pasted the key.")

    @classmethod
    def from_view(cls, view: StoreView) -> 'StoreResponse':
        return cls(provider=view.provider.value, account_label=view.account_label,
                   test_mode=view.test_mode, connected_by=view.connected_by)


class ConnectStoreRequest(BaseModel):
    secret: str = Field(..., min_length=1, max_length=500, description="For Stripe, a restricted key (`rk_live_…` or `rk_test_…`) with write access to Checkout Sessions, Refunds and Webhook Endpoints. For PayPal, the REST app's secret.")
    client_id: Optional[str] = Field(None, max_length=200, description="PayPal only: the REST app's client ID.")
    sandbox: bool = Field(True, description="PayPal only: the credentials are from the app's sandbox side (the default). Send `false` for live credentials that take real money.")


class TicketTypeResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    price_cents: int = Field(..., description="Price in the currency's smallest unit (cents).")
    currency: str = Field(..., description="Three-letter code, lower case.")
    quantity: Optional[int] = Field(None, description="How many exist; empty for no limit.")
    sold: int = Field(..., description="Badges paid for.")
    remaining: Optional[int] = Field(None, description="Left to sell, counting checkouts in progress; empty for no limit.")
    on_sale: bool = Field(..., description="Whether a member can buy it right now.")
    not_on_sale_reason: Optional[str] = Field(None, description="Why not, when it isn't: sold out, sales closed, and so on.")
    max_per_order: int
    sales_open_at: Optional[datetime] = None
    sales_close_at: Optional[datetime] = None
    lanyard: str = Field(..., description="The lanyard the check-in desk hands out for it.")
    covers_comp: bool = Field(..., description="The badge a comp stands in for; a comped buyer is owed up to its price.")
    is_active: bool
    sort_order: int

    @classmethod
    def from_availability(cls, a: TypeAvailability) -> 'TicketTypeResponse':
        t = a.ticket_type
        return cls(
            id=t.id, name=t.name, description=t.description, price_cents=t.price_cents, currency=t.currency,
            quantity=t.quantity, sold=a.sold, remaining=a.remaining, on_sale=a.on_sale,
            not_on_sale_reason=a.reason, max_per_order=t.max_per_order, sales_open_at=t.sales_open_at,
            sales_close_at=t.sales_close_at, lanyard=t.lanyard.value, covers_comp=t.covers_comp,
            is_active=t.is_active, sort_order=t.sort_order,
        )


class OnSaleResponse(BaseModel):
    types: List[TicketTypeResponse]
    providers: List[str] = Field(..., description="The stores a buyer can pay with: `stripe`, `paypal`.")


class TicketTypeCreate(BaseModel):
    name: str = Field(..., max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    price_cents: int = Field(..., ge=50)
    currency: str = Field('usd', min_length=3, max_length=3)
    quantity: Optional[int] = Field(None, ge=1)
    max_per_order: int = Field(10, ge=1, le=20)
    sales_open_at: Optional[datetime] = None
    sales_close_at: Optional[datetime] = None
    lanyard: CheckInTierLanyard = CheckInTierLanyard.BASE
    covers_comp: bool = False
    is_active: bool = True
    sort_order: int = 0


class TicketTypeUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    price_cents: Optional[int] = Field(None, ge=50)
    quantity: Optional[int] = Field(None, ge=1)
    max_per_order: Optional[int] = Field(None, ge=1, le=20)
    sales_open_at: Optional[datetime] = None
    sales_close_at: Optional[datetime] = None
    lanyard: Optional[CheckInTierLanyard] = None
    covers_comp: Optional[bool] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None


class CheckoutRequest(BaseModel):
    provider: TicketProvider
    quantities: Dict[int, int] = Field(..., description="Badge type id → how many.")


class CheckoutResponse(BaseModel):
    order_id: int
    checkout_url: str = Field(..., description="Send the buyer here to pay.")


class BadgeResponse(BaseModel):
    id: int
    ticket_type_id: int
    ticket_type_name: str
    status: str = Field(..., description="`pending`, `valid`, `void` or `refunded`.")
    code: str = Field(..., description="What the check-in desk scans.")
    price_cents: int
    owner_id: Optional[int] = None
    owner_name: Optional[str] = None
    claimed_at: Optional[datetime] = None
    comp_refund_cents: int = 0

    @classmethod
    def from_row(cls, t: Ticket) -> 'BadgeResponse':
        owner = t.owner if t.owner_id else None
        return cls(
            id=t.id, ticket_type_id=t.ticket_type_id, ticket_type_name=t.ticket_type.name, status=t.status.value,
            code=t.code, price_cents=t.price_cents, owner_id=t.owner_id,
            owner_name=owner.preferred_name if owner is not None else None,
            claimed_at=t.claimed_at, comp_refund_cents=t.comp_refund_cents,
        )


class ExtraBadgeResponse(BaseModel):
    badge: BadgeResponse
    claim_path: Optional[str] = Field(None, description="In-app path of the claim link while nobody has claimed it.")

    @classmethod
    def from_extra(cls, e: ExtraBadge) -> 'ExtraBadgeResponse':
        return cls(badge=BadgeResponse.from_row(e.ticket), claim_path=e.claim_path)


class MyBadgesResponse(BaseModel):
    own: List[BadgeResponse]
    extras: List[ExtraBadgeResponse]

    @classmethod
    def from_view(cls, v: MyBadges) -> 'MyBadgesResponse':
        return cls(own=[BadgeResponse.from_row(t) for t in v.own],
                   extras=[ExtraBadgeResponse.from_extra(e) for e in v.extras])


class OrderResponse(BaseModel):
    id: int
    buyer_id: int
    buyer_name: Optional[str] = None
    provider: str
    status: str = Field(..., description="`pending`, `paid`, `expired` or `refunded`.")
    total_cents: int
    refunded_cents: int
    currency: str
    created_at: datetime
    paid_at: Optional[datetime] = None
    badges: List[BadgeResponse]

    @classmethod
    def from_row(cls, o: TicketOrder) -> 'OrderResponse':
        return cls(
            id=o.id, buyer_id=o.buyer_id, buyer_name=o.buyer.preferred_name if o.buyer else None,
            provider=o.provider.value, status=o.status.value, total_cents=o.total_cents,
            refunded_cents=o.refunded_cents, currency=o.currency, created_at=o.created_at, paid_at=o.paid_at,
            badges=[BadgeResponse.from_row(t) for t in o.tickets],
        )


class ClaimPreviewResponse(BaseModel):
    ticket_type_name: str
    buyer_name: str
    claimable: bool
    reason: Optional[str] = None

    @classmethod
    def from_preview(cls, p: ClaimPreview) -> 'ClaimPreviewResponse':
        return cls(ticket_type_name=p.ticket.ticket_type.name, buyer_name=p.buyer_name,
                   claimable=p.claimable, reason=p.reason)


class CompRefundRow(BaseModel):
    ticket_id: int
    user_id: int
    user_name: str
    ticket_type_name: str
    reasons: List[str] = Field(..., description="Why they're comped: role names, or Volunteer.")
    scheduled_hours: Optional[float] = Field(None, description="Volunteer hours scheduled in the event window.")
    paid_cents: int
    amount_cents: int = Field(..., description="What a refund sends back: up to the comp badge's price.")
    currency: str
    provider: str

    @classmethod
    def from_row(cls, r: CompRefundOwed) -> 'CompRefundRow':
        return cls(
            ticket_id=r.ticket.id, user_id=r.owner.id, user_name=r.owner.preferred_name,
            ticket_type_name=r.ticket.ticket_type.name, reasons=r.reason_labels,
            scheduled_hours=r.scheduled_hours, paid_cents=r.paid_cents, amount_cents=r.amount_cents,
            currency=r.currency, provider=r.ticket.order.provider.value,
        )


class CompRefundsResponse(BaseModel):
    rows: List[CompRefundRow]
    comp_type_id: Optional[int] = Field(None, description="The badge type a comp covers; empty until staff pick one.")
    has_comp_rules: bool = Field(..., description="False when no check-in event comps anyone, so nobody can be owed.")

    @classmethod
    def from_view(cls, v: CompRefundsView) -> 'CompRefundsResponse':
        return cls(rows=[CompRefundRow.from_row(r) for r in v.rows],
                   comp_type_id=v.comp_type.id if v.comp_type else None, has_comp_rules=v.has_comp_rules)


class CompRefundRequest(BaseModel):
    ticket_ids: List[int] = Field(..., min_length=1, max_length=200)


class CompRefundResultResponse(BaseModel):
    ticket_id: int
    ok: bool
    amount_cents: int
    error: Optional[str] = None

    @classmethod
    def from_result(cls, r: CompRefundResult) -> 'CompRefundResultResponse':
        return cls(ticket_id=r.ticket_id, ok=r.ok, amount_cents=r.amount_cents, error=r.error)


def type_fields(body: BaseModel) -> Dict[str, object]:
    """The fields a PATCH actually sent, enums as their values."""
    out = body.model_dump(exclude_unset=True)
    if 'lanyard' in out and out['lanyard'] is not None:
        out['lanyard'] = CheckInTierLanyard(out['lanyard']).value
    return out

