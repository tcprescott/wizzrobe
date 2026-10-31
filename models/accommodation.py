
from tortoise import fields
from tortoise.models import Model

from .enums import AccommodationStatus


class AccommodationRequest(Model):
    """A member's request for ADA accommodation in one community.

    Per tenant, not on the global :class:`User`: asking SpeedGaming Live for a
    seat near the stage must not tell another community's staff anything. One
    row per person per event; withdrawing keeps the row (status
    ``WITHDRAWN``, ``details`` cleared) so the staff notes survive as history,
    and asking again reopens it as ``NEW``.

    Editing the details of an ``ARRANGED`` request leaves it ``ARRANGED`` and
    sets ``changed_since_arranged`` (with ``arranged_details`` holding what staff
    arranged against) until staff review the change.

    ``details`` is the requester's own text and ``staff_notes`` is staff-only;
    neither ever leaves the app (audit rows, events, REST and MCP carry ids and
    status only).
    """

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='accommodation_requests', on_delete=fields.CASCADE,
    )
    # Keep nullable: not every write path stamps it yet.
    event: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Event', related_name='accommodation_requests', on_delete=fields.CASCADE,
    )
    event_id: int
    user: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.User', related_name='accommodation_requests', on_delete=fields.CASCADE,
    )
    user_id: int
    status = fields.CharEnumField(AccommodationStatus, default=AccommodationStatus.NEW, max_length=20)
    details = fields.TextField(null=True)
    changed_since_arranged = fields.BooleanField(default=False)
    arranged_details = fields.TextField(null=True)
    staff_notified_at = fields.DatetimeField(null=True)
    staff_notes = fields.TextField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'accommodation_request'
        unique_together = (('tenant', 'event', 'user'),)
