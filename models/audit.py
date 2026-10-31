from typing import Optional

from tortoise import fields
from tortoise.models import Model


class AuditLog(Model):
    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField(
        'models.Tenant', related_name='audit_logs', null=True, on_delete=fields.SET_NULL
    )
    event: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.Event', related_name='audit_logs', on_delete=fields.SET_NULL, null=True,
    )
    event_id: Optional[int]
    user = fields.ForeignKeyField(
        'models.User', related_name='audit_logs', null=True, on_delete=fields.SET_NULL
    )
    action = fields.CharField(max_length=255)
    details = fields.TextField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True, index=True)

    class Meta:
        table = 'auditlog'
        indexes = (('user',),)


class TelemetryEvent(Model):
    """Append-only engagement telemetry: how people actually use the tool.

    Distinct from ``AuditLog`` (which records deliberate admin *actions*): this
    is high-volume behavioral signal — page views, feature interactions, and a
    mirror of every domain event on the bus — captured to answer "how did people
    engage post-event?". ``user`` is ``SET_NULL`` + identity-snapshotted into
    ``details`` so the trail survives a user deletion, exactly like ``AuditLog``.
    """
    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField(
        'models.Tenant', related_name='telemetry_events', null=True, on_delete=fields.SET_NULL
    )
    event: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.Event', related_name='telemetry_events', on_delete=fields.SET_NULL, null=True,
    )
    event_id: Optional[int]
    user = fields.ForeignKeyField(
        'models.User', related_name='telemetry_events', null=True, on_delete=fields.SET_NULL
    )
    category = fields.CharField(max_length=32, index=True)
    event_type = fields.CharField(max_length=100, index=True)
    path = fields.CharField(max_length=512, null=True)
    # Indexed with tenant (Meta), not alone: the distinct-session count took 12s on a bare index.
    session_id = fields.CharField(max_length=64, null=True)
    details = fields.TextField(null=True)
    target = fields.CharField(max_length=200, null=True)
    device = fields.CharField(max_length=16, null=True)
    created_at = fields.DatetimeField(auto_now_add=True, index=True)

    class Meta:
        table = 'telemetryevent'
        indexes = (('user',), ('tenant', 'session_id'))
