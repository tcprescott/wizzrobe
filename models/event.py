from tortoise import fields
from tortoise.models import Model

from .enums import EventStatus


class Event(Model):
    """One weekend (or season) a community runs, e.g. "SGL 2026".

    The community (:class:`~models.Tenant`) keeps what lasts year to year:
    members, staff, integrations, presets, equipment. An event owns what belongs
    to one run of it: tournaments and their matches, stages, volunteer shifts,
    check-in, meetups, qualifiers. Event pages live at ``/t/<community>/e/<slug>/``.

    Exactly one event per community is the default, the target of a bare link.
    Tortoise cannot express that as a partial unique index, so the migration
    creates it (``uid_event_one_default_per_tenant``) and :class:`EventService`
    keeps it true on every write. ``slug`` is unique within the community and is
    locked once the event goes live, because links depend on it.
    """

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='events', on_delete=fields.CASCADE,
    )
    tenant_id: int
    name = fields.CharField(max_length=120)
    slug = fields.CharField(max_length=64)
    status = fields.CharEnumField(EventStatus, max_length=16, default=EventStatus.DRAFT)
    is_default = fields.BooleanField(default=False)
    starts_on = fields.DateField(null=True)
    ends_on = fields.DateField(null=True)
    config = fields.JSONField(default=dict)
    archived_at = fields.DatetimeField(null=True)
    archived_by: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.User', related_name='archived_events', on_delete=fields.SET_NULL, null=True,
    )
    # Set when the nightly job deletes the event's emergency contacts and
    # accommodation requests, 30 days after it was archived.
    pii_purged_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'event'
        unique_together = (('tenant', 'slug'),)

    def __str__(self) -> str:
        return self.name

    @property
    def is_writable(self) -> bool:
        return self.status != EventStatus.ARCHIVED
