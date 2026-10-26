
from tortoise import fields
from tortoise.models import Model


class RoomToken(Model):
    """An unlisted key that opens one read-only page on a shared venue machine.

    Deliberately not an :class:`~models.ApiToken`, which acts with its owning
    user's full permissions. A token taped to a tournament-room PC — a machine
    nobody signs in on, in a room the public walks through — unlocks the seeds
    view and nothing else, so it carries no user identity at all beyond who
    issued it.

    Only the SHA-256 hash is stored; the URL is shown once, at issue. The token
    is resolved *inside* the tenant prefix, so the lookup is tenant-scoped like
    every other read and a token from another community simply does not exist
    here.
    """

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField(
        'models.Tenant', related_name='room_tokens', on_delete=fields.CASCADE
    )
    # Nullable until every write stamps it; the events migration backfilled the
    # rows that existed before (see docs/features/events.md).
    event: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Event', related_name='room_tokens', on_delete=fields.CASCADE,
    )
    event_id: int
    label = fields.CharField(max_length=100)
    token_hash = fields.CharField(max_length=64, unique=True, index=True)
    # SET_NULL: an issuer who leaves must not revoke the room PC's access on
    # their way out — staff revoke a token deliberately or not at all.
    created_by = fields.ForeignKeyField(
        'models.User', related_name='room_tokens_created',
        null=True, on_delete=fields.SET_NULL,
    )
    last_used_at = fields.DatetimeField(null=True)
    revoked_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = 'roomtoken'


class SystemConfiguration(Model):
    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='system_configurations', on_delete=fields.CASCADE)
    name = fields.CharField(max_length=255)
    value = fields.TextField()
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'systemconfiguration'
        unique_together = (('tenant', 'name'),)


class LogLevelOverride(Model):
    """A super-admin's level for one stdlib logger, reapplied at every startup.

    **Global** (no ``tenant`` FK): there is one process and one logging tree, so
    a level is a property of the deployment, not of a community. Set on
    ``/platform`` while diagnosing a live problem; a row means "this logger runs
    at this level until someone clears it". ``logger_name`` is the dotted
    ``logging.getLogger`` name, with ``''`` reserved for the root logger.
    """

    id = fields.IntField(pk=True)
    logger_name = fields.CharField(max_length=255, unique=True)
    level = fields.CharField(max_length=16)
    # SET_NULL: the override outlives whoever set it; the audit row keeps who.
    updated_by: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.User', related_name='log_level_overrides',
        null=True, on_delete=fields.SET_NULL,
    )
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'logleveloverride'


class BrowserMonitoringSettings(Model):
    """How the browser-side Sentry SDK behaves, set on ``/platform`` → Monitoring.

    **Global** and a singleton (``id=1``): the browser reports to the same Sentry
    project as the server, so these are properties of the deployment. No row
    means the defaults: on, replay on error, every error sent.
    """

    id = fields.IntField(pk=True)
    enabled = fields.BooleanField(default=True)
    replay_on_error = fields.BooleanField(default=True)
    # Share of browser errors sent, 0.0–1.0, for throttling a flood.
    sample_rate = fields.FloatField(default=1.0)
    # SET_NULL: the setting outlives whoever changed it; the audit row keeps who.
    updated_by: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.User', related_name='browser_monitoring_updates',
        null=True, on_delete=fields.SET_NULL,
    )
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'browsermonitoringsettings'


class BrowserErrorIgnoreRule(Model):
    """A super-admin's rule for a browser error that should never reach Sentry.

    **Global**. ``pattern`` is a case-insensitive substring of the error's
    message (``match='message'``) or of a script URL in its stack
    (``match='url'``). The browser drops a matching error before sending it, so
    a rule added mid-event silences that error in every open tab at once.
    """

    id = fields.IntField(pk=True)
    pattern = fields.CharField(max_length=200)
    match = fields.CharField(max_length=16, default='message')
    created_by: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.User', related_name='browser_error_ignore_rules',
        null=True, on_delete=fields.SET_NULL,
    )
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = 'browsererrorignorerule'
        unique_together = (('pattern', 'match'),)
