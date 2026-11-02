from typing import Optional

from tortoise import fields
from tortoise.models import Model

from .enums import CheckInTierLanyard, TicketOrderStatus, TicketProvider, TicketStatus


class TicketStore(Model):
    """A community's own store account that badge payments go to.

    One per provider per community. Wizzrobe takes no cut and never holds the
    money: checkout happens on the provider's hosted page and the payout lands
    in the community's account. ``secret`` (the Stripe restricted key, or the
    PayPal client id and secret) and ``webhook_secret`` are sealed with
    ``secret_box`` (purpose ``ticket_store``, the tenant id as context), so a
    value copied to another community's row fails to open, and neither is ever
    returned to a page, an API response or an audit row.

    ``webhook_token`` is the opaque part of the URL the provider posts to; it
    names the row, the signature proves the sender.
    """

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='ticket_stores', on_delete=fields.CASCADE,
    )
    tenant_id: int
    provider = fields.CharEnumField(TicketProvider, max_length=16)
    account_label = fields.CharField(max_length=255, null=True)
    secret = fields.TextField()
    webhook_secret = fields.TextField(null=True)
    webhook_id = fields.CharField(max_length=128, null=True)
    webhook_token = fields.CharField(max_length=64, unique=True)
    test_mode = fields.BooleanField(default=False)
    connected_by: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.User', related_name='ticket_stores_connected', null=True, on_delete=fields.SET_NULL,
    )
    connected_by_id: Optional[int]
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'ticket_store'
        unique_together = (('tenant', 'provider'),)


class TicketType(Model):
    """A kind of badge an event sells (Weekend, VIP, Saturday only).

    Staff set the price, how many exist and when sales run; the store only
    takes the money. ``covers_comp`` marks the badge a comp stands in for: a
    comped person who paid for their own badge is owed up to this one's price.
    ``lanyard`` is what the check-in desk hands out for it.
    """

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='ticket_types', on_delete=fields.CASCADE,
    )
    tenant_id: int
    event: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Event', related_name='ticket_types', on_delete=fields.CASCADE,
    )
    event_id: int
    name = fields.CharField(max_length=100)
    description = fields.TextField(null=True)
    price_cents = fields.IntField()
    currency = fields.CharField(max_length=3, default='usd')
    quantity = fields.IntField(null=True)
    max_per_order = fields.IntField(default=10)
    sales_open_at = fields.DatetimeField(null=True)
    sales_close_at = fields.DatetimeField(null=True)
    lanyard = fields.CharEnumField(CheckInTierLanyard, max_length=16, default=CheckInTierLanyard.BASE)
    covers_comp = fields.BooleanField(default=False)
    is_active = fields.BooleanField(default=True)
    sort_order = fields.IntField(default=0)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'ticket_type'


class TicketOrder(Model):
    """One checkout: a signed-in buyer, some badges, one store payment.

    Created ``PENDING`` when the buyer is sent to the store, with its badges
    held until ``hold_expires_at``. Only the store's signed webhook moves it to
    ``PAID``; the return page just waits for that. ``provider_ref`` is the
    store's checkout id (Stripe Checkout Session, PayPal order) and
    ``payment_ref`` the payment a refund is made against.
    """

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='ticket_orders', on_delete=fields.CASCADE,
    )
    tenant_id: int
    event: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Event', related_name='ticket_orders', on_delete=fields.CASCADE,
    )
    event_id: int
    buyer: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.User', related_name='ticket_orders', on_delete=fields.CASCADE,
    )
    buyer_id: int
    provider = fields.CharEnumField(TicketProvider, max_length=16)
    status = fields.CharEnumField(TicketOrderStatus, max_length=16, default=TicketOrderStatus.PENDING)
    total_cents = fields.IntField()
    refunded_cents = fields.IntField(default=0)
    currency = fields.CharField(max_length=3)
    provider_ref = fields.CharField(max_length=128, null=True)
    payment_ref = fields.CharField(max_length=128, null=True)
    hold_expires_at = fields.DatetimeField()
    paid_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'ticket_order'
        indexes = (('provider', 'provider_ref'), ('event', 'status'))


class Ticket(Model):
    """One badge from an order.

    The buyer owns their first badge; each extra starts with no ``owner`` and a
    claim link (``claim_token``, sealed so the buyer can copy it again) that
    whoever opens it signed in takes. ``code`` is what the check-in desk
    scans. ``comp_refund_cents`` records money sent back because its owner was
    comped; the badge stays valid.
    """

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='tickets', on_delete=fields.CASCADE,
    )
    tenant_id: int
    event: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Event', related_name='tickets', on_delete=fields.CASCADE,
    )
    event_id: int
    order: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.TicketOrder', related_name='tickets', on_delete=fields.CASCADE,
    )
    order_id: int
    ticket_type: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.TicketType', related_name='tickets', on_delete=fields.CASCADE,
    )
    ticket_type_id: int
    owner: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.User', related_name='tickets', null=True, on_delete=fields.SET_NULL,
    )
    owner_id: Optional[int]
    status = fields.CharEnumField(TicketStatus, max_length=16, default=TicketStatus.PENDING)
    price_cents = fields.IntField()
    code = fields.CharField(max_length=16, unique=True)
    claim_token = fields.TextField(null=True)
    claim_lookup = fields.CharField(max_length=64, null=True, unique=True)
    claimed_at = fields.DatetimeField(null=True)
    comp_refund_cents = fields.IntField(default=0)
    comp_refunded_at = fields.DatetimeField(null=True)
    comp_refunded_by: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.User', related_name='ticket_comp_refunds', null=True, on_delete=fields.SET_NULL,
    )
    comp_refunded_by_id: Optional[int]
    refunded_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'ticket'
        indexes = (('event', 'owner'), ('order',))
