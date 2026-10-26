from typing import Optional

from tortoise import fields
from tortoise.models import Model


class AuditLog(Model):
    id = fields.IntField(pk=True)
    # Nullable: stamped from context for tenant activity; NULL marks a
    # platform-level row (super-admin tenant CRUD). SET_NULL so a deleted tenant
    # doesn't destroy the append-only trail.
    tenant = fields.ForeignKeyField(
        'models.Tenant', related_name='audit_logs', null=True, on_delete=fields.SET_NULL
    )
    # Which event this happened during, for filtering. The row belongs to the
    # community, so it outlives the event (SET NULL) and may carry none.
    event: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.Event', related_name='audit_logs', on_delete=fields.SET_NULL, null=True,
    )
    event_id: Optional[int]
    # SET_NULL keeps the append-only trail intact when a user is deleted; the
    # actor's identity is also snapshotted into ``details`` by AuditService.
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
    # Nullable: stamped from the event/request tenant; NULL marks a platform-level
    # row (platform page views). SET_NULL preserves the trail past a tenant delete.
    tenant = fields.ForeignKeyField(
        'models.Tenant', related_name='telemetry_events', null=True, on_delete=fields.SET_NULL
    )
    # Which event this happened during, for filtering. The row belongs to the
    # community, so it outlives the event (SET NULL) and may carry none.
    event: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.Event', related_name='telemetry_events', on_delete=fields.SET_NULL, null=True,
    )
    event_id: Optional[int]
    user = fields.ForeignKeyField(
        'models.User', related_name='telemetry_events', null=True, on_delete=fields.SET_NULL
    )
    # Coarse bucket for aggregation without parsing event_type: 'page',
    # 'interaction', or 'domain' (see TelemetryCategory in the service).
    category = fields.CharField(max_length=32, index=True)
    # Namespaced ``object.verb`` name. For domain rows this is the EventType
    # string; for engagement rows it is e.g. 'page.view' / 'report.exported'.
    event_type = fields.CharField(max_length=100, index=True)
    # Route/page the event happened on (page views + interactions); null for
    # domain events, which are not page-scoped.
    path = fields.CharField(max_length=512, null=True)
    # Per-browser correlation id (NiceGUI app.storage.browser id) so a user's
    # journey can be reconstructed as an ordered session; null for bus events.
    # Indexed with ``tenant`` (Meta) rather than alone: the engagement summary's
    # distinct-session count over a community's history took 12s on a bare
    # session index at a few years' volume, and 0.2s on this one.
    session_id = fields.CharField(max_length=64, null=True)
    details = fields.TextField(null=True)
    # The control a ``ui.*`` interaction row is about (a button's name, a dialog's
    # title, a tab's slug); a column rather than a details key so the "which
    # controls get used" leaderboard can GROUP BY it. Null for every other row.
    target = fields.CharField(max_length=200, null=True)
    # 'phone' / 'tablet' / 'desktop' from the viewport width at the time of a
    # client-reported interaction; null where the server can't know it.
    device = fields.CharField(max_length=16, null=True)
    created_at = fields.DatetimeField(auto_now_add=True, index=True)

    class Meta:
        table = 'telemetryevent'
        indexes = (('user',), ('tenant', 'session_id'))
