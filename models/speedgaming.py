from tortoise import fields
from tortoise.models import Model

from .enums import SyncStatus


class SpeedGamingEventLink(Model):
    """Config row wiring an SG event slug to a tenant tournament (PR 7).

    Tenant-scoped. The sync worker iterates the *active* links, polls the SG
    schedule API for each ``event_slug`` over a forward window, and materializes
    the returned episodes into the linked tournament's ``Match`` rows. The
    observability fields (``last_synced_at`` / ``last_status`` / ``last_error``)
    make sync health visible in the admin UI without reading the audit log.
    """

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='sg_event_links', on_delete=fields.CASCADE)
    tournament = fields.ForeignKeyField('models.Tournament', related_name='sg_event_links', on_delete=fields.CASCADE)
    event_slug = fields.CharField(max_length=128)
    content_type = fields.CharField(max_length=64, null=True)
    active = fields.BooleanField(default=True)
    sync_interval_minutes = fields.IntField(default=15)
    # hours
    lookahead_hours = fields.IntField(default=72)
    last_synced_at = fields.DatetimeField(null=True)
    last_status = fields.CharField(max_length=32, null=True)
    last_error = fields.TextField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    episodes = fields.ReverseRelation["SpeedGamingEpisode"]

    class Meta:
        table = 'speedgamingeventlink'
        unique_together = (('tenant', 'tournament', 'event_slug'),)
        indexes = (('tenant',), ('tournament',))


class SpeedGamingEpisode(Model):
    """A synced SpeedGaming schedule episode — the ETL staging record (PR 7).

    Tenant-scoped, unique ``(tenant, sg_episode_id)``. Holds the raw upstream
    payload snapshot plus a ``content_hash`` so an unchanged re-sync is a cheap
    no-op. The materialized Wizzrobe ``Match`` is reachable via the reverse of
    ``Match.speedgaming_episode`` (that FK is the canonical source marker; there
    is no second column here).
    """

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='sg_episodes', on_delete=fields.CASCADE)
    event_link = fields.ForeignKeyField(
        'models.SpeedGamingEventLink', related_name='episodes', null=True, on_delete=fields.SET_NULL
    )
    sg_episode_id = fields.CharField(max_length=64)
    title = fields.CharField(max_length=255, null=True)
    scheduled_at = fields.DatetimeField(null=True)
    payload = fields.JSONField(null=True)
    content_hash = fields.CharField(max_length=64, null=True)
    sync_status = fields.CharEnumField(SyncStatus, default=SyncStatus.PENDING, max_length=20)
    synced_at = fields.DatetimeField(null=True)
    sync_error = fields.TextField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    match = fields.ReverseRelation["Match"]

    class Meta:
        table = 'speedgamingepisode'
        unique_together = (('tenant', 'sg_episode_id'),)
        indexes = (('tenant',), ('event_link',))
