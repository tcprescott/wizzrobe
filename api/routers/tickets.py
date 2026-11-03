"""Ticket sales: badge types, buying, your badges, claim links, orders, comp refunds.

Gated by the ``TICKET_SALES`` feature flag at the router and again in each
service. Badges belong to an event, so these routes bind one like any other
event-owned router; stores are community-wide and simply ignore it.

The store webhook is separate (:data:`webhook_router`): it carries no token and
no community, and proves itself with the store's signature instead.
"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request, status

from api.dependencies import ServiceErrorRoute, event_binding, require_api_actor, require_write_actor
from api.schemas.tickets import (
    BadgeResponse,
    CheckoutRequest,
    CheckoutResponse,
    ClaimPreviewResponse,
    CompRefundRequest,
    CompRefundResultResponse,
    CompRefundsResponse,
    ConnectStoreRequest,
    MyBadgesResponse,
    OnSaleResponse,
    OrderResponse,
    StoreResponse,
    TicketTypeCreate,
    TicketTypeResponse,
    TicketTypeUpdate,
    type_fields,
)
from application.errors import NotFoundError
from application.services import (
    TicketCheckoutService,
    TicketCompRefundService,
    TicketService,
    TicketStoreService,
)
from application.services.ticket_gateways import GatewaySignatureError
from models import TicketOrder, TicketProvider, TicketType, User

_BINDING = Depends(event_binding(type_id=TicketType, order_id=TicketOrder))
router = APIRouter(prefix="/tickets", tags=["Tickets"], route_class=ServiceErrorRoute, dependencies=[_BINDING])
me_router = APIRouter(prefix="/me/tickets", tags=["Tickets"], route_class=ServiceErrorRoute, dependencies=[_BINDING])
webhook_router = APIRouter(prefix="/ticket-webhooks", tags=["Tickets"], include_in_schema=False)

STAFF = "**Who can call this:** Staff."
MEMBER = "**Who can call this:** any member."


async def _type_row(type_id: int, actor: User) -> TicketTypeResponse:
    for row in await TicketService().list_types(actor):
        if row.ticket_type.id == type_id:
            return TicketTypeResponse.from_availability(row)
    raise NotFoundError('Badge type was not found.')


# ── Stores (staff) ────────────────────────────────────────────────────────

@router.get("/stores", response_model=List[StoreResponse], summary="The store accounts badges sell through",
            description=f"Each connected store: provider, account name, test mode and who connected it. Never the key.\n\n{STAFF}")
async def list_stores(actor: User = Depends(require_api_actor)):
    return [StoreResponse.from_view(v) for v in await TicketStoreService().list_stores(actor)]


@router.put("/stores/{provider}", response_model=StoreResponse, summary="Connect a store account",
            description=("Connects the community's own account with a pasted key and registers Wizzrobe's webhook on it. "
                         f"Reconnecting replaces the old key.\n\nFails with 400 when the key is the wrong kind or the store refuses it.\n\n{STAFF}"))
async def connect_store(provider: TicketProvider, body: ConnectStoreRequest, actor: User = Depends(require_write_actor)):
    return StoreResponse.from_view(await TicketStoreService().connect(actor, provider, body.secret))


@router.delete("/stores/{provider}", status_code=status.HTTP_204_NO_CONTENT, summary="Disconnect a store account",
               description=f"Forgets the key and removes Wizzrobe's webhook from the account. Paid orders stay paid.\n\n{STAFF}")
async def disconnect_store(provider: TicketProvider, actor: User = Depends(require_write_actor)):
    await TicketStoreService().disconnect(actor, provider)


# ── Badge types ───────────────────────────────────────────────────────────

@router.get("/types", response_model=List[TicketTypeResponse], summary="Every badge type this event sells",
            description=f"All badge types, on sale or not, with sold and remaining counts.\n\n{STAFF}")
async def list_types(actor: User = Depends(require_api_actor)):
    return [TicketTypeResponse.from_availability(a) for a in await TicketService().list_types(actor)]


@router.post("/types", response_model=TicketTypeResponse, status_code=status.HTTP_201_CREATED,
             summary="Add a badge type", description=f"Fails with 400 when a field is out of range.\n\n{STAFF}")
async def create_type(body: TicketTypeCreate, actor: User = Depends(require_write_actor)):
    row = await TicketService().create_type(actor, **type_fields(body))
    return await _type_row(row.id, actor)


@router.patch("/types/{type_id}", response_model=TicketTypeResponse, summary="Change a badge type",
              description=f"Badges already sold keep the price they were bought at.\n\n{STAFF}")
async def update_type(type_id: int, body: TicketTypeUpdate, actor: User = Depends(require_write_actor)):
    await TicketService().update_type(actor, type_id, **type_fields(body))
    return await _type_row(type_id, actor)


@router.delete("/types/{type_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a badge type",
               description=f"Only one nobody has ordered; take a sold one off sale instead.\n\n{STAFF}")
async def delete_type(type_id: int, actor: User = Depends(require_write_actor)):
    await TicketService().delete_type(actor, type_id)


@router.get("/on-sale", response_model=OnSaleResponse, summary="Badges a member can buy now",
            description=f"Active badge types with what's left, and the stores to pay with. Empty until staff connect a store.\n\n{MEMBER}")
async def on_sale(actor: User = Depends(require_api_actor)):
    types = await TicketService().on_sale(actor)
    providers = await TicketStoreService().connected_providers() if types else []
    return OnSaleResponse(types=[TicketTypeResponse.from_availability(a) for a in types],
                          providers=[p.value for p in providers])


@router.post("/checkout", response_model=CheckoutResponse, summary="Hold badges and open the store's checkout",
             description=("Holds the badges for 30 minutes and returns the store's checkout page. The order is paid only "
                          "when the store confirms; poll `GET /api/me/tickets/orders/{order_id}` on the way back.\n\n"
                          f"Fails with 400 when a badge is sold out, off sale or over the per-order limit.\n\n{MEMBER}"))
async def checkout(body: CheckoutRequest, actor: User = Depends(require_write_actor)):
    start = await TicketCheckoutService().start_checkout(actor, body.provider, body.quantities)
    return CheckoutResponse(order_id=start.order_id, checkout_url=start.url)


@router.get("/claims/{token}", response_model=ClaimPreviewResponse, summary="What a claim link would give you",
            description=f"Who sent it, which badge, and whether you can take it.\n\n{MEMBER}")
async def preview_claim(token: str, actor: User = Depends(require_api_actor)):
    return ClaimPreviewResponse.from_preview(await TicketService().preview_claim(actor, token))


@router.post("/claims/{token}", response_model=BadgeResponse, summary="Claim a badge someone bought for you",
             description=f"A link works once. Fails with 400 when it's already claimed, refunded or your own.\n\n{MEMBER}")
async def claim(token: str, actor: User = Depends(require_write_actor)):
    return BadgeResponse.from_row(await TicketService().claim(actor, token))


# ── Orders and comp refunds (staff) ───────────────────────────────────────

@router.get("/orders", response_model=List[OrderResponse], summary="Paid and refunded orders, newest first",
            description=f"The latest 200 orders for this event.\n\n{STAFF}")
async def list_orders(actor: User = Depends(require_api_actor)):
    return [OrderResponse.from_row(o) for o in await TicketService().list_orders(actor)]


@router.post("/orders/{order_id}/refund", response_model=OrderResponse, summary="Refund an order",
             description=f"Refunds what's left of it through its store and voids its badges.\n\n{STAFF}")
async def refund_order(order_id: int, actor: User = Depends(require_write_actor)):
    return OrderResponse.from_row(await TicketService().refund_order(actor, order_id))


@router.get("/comp-refunds", response_model=CompRefundsResponse, summary="Comped people who paid for their own badge",
            description=("Everyone the check-in comp rules comp who also bought their own badge and hasn't been refunded, "
                         f"with what a refund would send back (up to the comp badge's price).\n\n{STAFF}"))
async def comp_refunds(actor: User = Depends(require_api_actor)):
    return CompRefundsResponse.from_view(await TicketCompRefundService().owed(actor))


@router.post("/comp-refunds", response_model=List[CompRefundResultResponse], summary="Send comp refunds",
             description=("Refunds each named badge through its store and DMs its holder. One failure doesn't stop the "
                          f"rest; each result says how it went.\n\n{STAFF}"))
async def send_comp_refunds(body: CompRefundRequest, actor: User = Depends(require_write_actor)):
    results = await TicketCompRefundService().refund(actor, body.ticket_ids)
    return [CompRefundResultResponse.from_result(r) for r in results]


# ── Your badges ───────────────────────────────────────────────────────────

@me_router.get("", response_model=MyBadgesResponse, summary="Your badges, and extras you bought",
               description=f"Your own badges, and each extra you bought with its claim link until someone claims it.\n\n{MEMBER}")
async def my_badges(actor: User = Depends(require_api_actor)):
    return MyBadgesResponse.from_view(await TicketService().my_badges(actor))


@me_router.get("/orders/{order_id}", response_model=OrderResponse, summary="One of your orders",
               description=f"For the page the store sends you back to: `pending` until the store confirms.\n\n{MEMBER}")
async def my_order(order_id: int, actor: User = Depends(require_api_actor)):
    return OrderResponse.from_row(await TicketService().my_order(actor, order_id))


# ── Store webhooks (no token: signed by the store) ────────────────────────

@webhook_router.post("/{provider}/{token}", status_code=status.HTTP_200_OK)
async def store_webhook(provider: TicketProvider, token: str, request: Request):
    body = await request.body()
    try:
        outcome = await TicketCheckoutService().handle_webhook(provider, token, body, dict(request.headers))
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Unknown webhook') from exc
    except GatewaySignatureError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Bad signature') from exc
    return {'ok': True, 'outcome': outcome}
