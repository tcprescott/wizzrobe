from typing import Optional

from tortoise import fields
from tortoise.models import Model


class EmergencyContact(Model):
    """Who a member wants a community's staff to call if something happens to them.

    Per tenant, like :class:`AccommodationRequest`: a contact given to one
    community is never readable by another's staff, even a staff member of both.
    One row per person per community. Removing it deletes the row outright;
    nothing of the contact survives in history.

    ``name``, ``relationship`` and ``phone`` never leave the app: audit rows
    carry ids only, nothing is published on the event bus, and there is no
    REST, MCP, export or telemetry surface. Every staff look writes an audit row
    and stamps ``last_viewed_at``/``last_viewed_by``, which the member sees.
    """

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='emergency_contacts', on_delete=fields.CASCADE,
    )
    # Nullable until every write stamps it; the events migration backfilled the
    # rows that existed before (see docs/features/events.md).
    event: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.Event', related_name='emergency_contacts', on_delete=fields.CASCADE, null=True,
    )
    event_id: Optional[int]
    user: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.User', related_name='emergency_contacts', on_delete=fields.CASCADE,
    )
    user_id: int
    name = fields.CharField(max_length=100)
    relationship = fields.CharField(max_length=60, null=True)
    phone = fields.CharField(max_length=40)
    last_viewed_at = fields.DatetimeField(null=True)
    last_viewed_by: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.User', related_name='emergency_contacts_viewed', null=True,
        on_delete=fields.SET_NULL,
    )
    last_viewed_by_id: int | None
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'emergency_contact'
        unique_together = (('tenant', 'user'),)
