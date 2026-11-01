from tortoise import fields
from tortoise.models import Model


class Webhook(Model):
    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='webhooks', on_delete=fields.CASCADE)
    name = fields.CharField(max_length=255)
    url = fields.CharField(max_length=1024)
    # Plaintext because each delivery is signed with it; never return it from list/GET or log it.
    secret = fields.CharField(max_length=128)
    # List of EventType values this webhook fires on; ['*'] means every event.
    event_types = fields.JSONField(default=list)
    event_ids = fields.JSONField(null=True)  # type: ignore[var-annotated]
    is_active = fields.BooleanField(default=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'webhook'


class WebhookDelivery(Model):
    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='webhook_deliveries', on_delete=fields.CASCADE)
    webhook = fields.ForeignKeyField(
        'models.Webhook', related_name='deliveries', on_delete=fields.CASCADE
    )
    event_type = fields.CharField(max_length=100)
    payload = fields.TextField()
    response_status = fields.IntField(null=True)
    attempt_count = fields.IntField(default=0)
    success = fields.BooleanField(default=False)
    error = fields.TextField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True, index=True)
    delivered_at = fields.DatetimeField(null=True)
    # Outbox: a row is written pending before the first POST, so a delivery outlives the process that queued it.
    pending = fields.BooleanField(default=False, index=True)
    claimed_until = fields.DatetimeField(null=True)
    delivery_uuid = fields.CharField(max_length=36, null=True)

    class Meta:
        table = 'webhookdelivery'
        indexes = (('webhook',),)


class PersonalWebhook(Model):
    """A webhook one member points at their own Discord channel or app.

    Unlike :class:`Webhook` (staff, raw events), what this one sends is decided
    per delivery from what ``user`` can see at that moment, and its payload is
    rebuilt from a field allowlist rather than forwarding the event as-is.
    """

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField('models.Tenant', related_name='personal_webhooks', on_delete=fields.CASCADE)
    user: fields.ForeignKeyRelation = fields.ForeignKeyField('models.User', related_name='personal_webhooks', on_delete=fields.CASCADE)
    name = fields.CharField(max_length=80)
    url = fields.CharField(max_length=1024)
    secret = fields.CharField(max_length=128)
    event_types: list = fields.JSONField(default=list)
    only_mine = fields.BooleanField(default=True)
    is_active = fields.BooleanField(default=True)
    paused_reason = fields.CharField(max_length=255, null=True)
    consecutive_failures = fields.IntField(default=0)
    last_delivery_at = fields.DatetimeField(null=True)
    last_status = fields.IntField(null=True)
    last_error = fields.CharField(max_length=255, null=True)
    delivered_count = fields.IntField(default=0)
    failed_count = fields.IntField(default=0)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'personalwebhook'
        indexes = (('tenant', 'user'),)


class PersonalWebhookOutbox(Model):
    """A personal webhook message waiting to be sent, so it outlives the process that queued it.

    Written per webhook when an event fans out, deleted once the send settles.
    ``view`` is what the owner may be told, worked out at fan-out time.
    """

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='personal_webhook_outbox', on_delete=fields.CASCADE)
    webhook: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.PersonalWebhook', related_name='outbox', on_delete=fields.CASCADE)
    event_type = fields.CharField(max_length=100)
    view: dict = fields.JSONField()
    occurred_at = fields.DatetimeField()
    delivery_uuid = fields.CharField(max_length=36)
    claimed_until = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True, index=True)

    class Meta:
        table = 'personalwebhookoutbox'
