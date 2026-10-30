from typing import Optional

from tortoise import fields
from tortoise.models import Model

from .enums import DiscordEventSource, SentMessageStatus, StaffRequestKind


class DiscordScheduledEvent(Model):
    """A Discord Scheduled Event mirrored from an Wizzrobe schedule row (PR 8).

    Tenant-scoped reconciliation link. The reconciler keeps the tenant guild's
    Scheduled Events in sync with its schedule: ``content_hash`` drives
    update-vs-noop, and the working set is **only this tenant's own rows** —
    never every event in the guild — so a shared guild never has a sibling
    tenant's events cancelled (``discord_guild_id`` is not unique).

    Uniqueness: ``discord_event_id`` is globally unique (one link per Discord
    event); ``(tenant, source_type, source_id)`` is unique for idempotency (one
    mirrored event per source row).
    """

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='discord_scheduled_events', on_delete=fields.CASCADE)
    # Keep nullable: not every write path stamps it yet.
    event: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Event', related_name='discord_scheduled_events', on_delete=fields.CASCADE,
    )
    event_id: int
    # Snapshotted at creation so a later guild re-link doesn't orphan the row.
    guild_id = fields.BigIntField()
    discord_event_id = fields.BigIntField(unique=True)
    source_type = fields.CharEnumField(DiscordEventSource, max_length=20)
    source_id = fields.IntField()
    title = fields.CharField(max_length=255)
    scheduled_at = fields.DatetimeField(null=True)
    content_hash = fields.CharField(max_length=64, null=True)
    synced_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'discordscheduledevent'
        unique_together = (('tenant', 'source_type', 'source_id'),)
        indexes = (('tenant',), ('guild_id',))


class StaffRequestDM(Model):
    """One staff member's Discord DM about a request someone has to decide.

    Kept so the DM can be edited once anyone decides it: every staff member is
    DM'd, and without the edit they all open the request to check. ``object_id``
    is a bare id, not a foreign key, because an approved match cancellation
    deletes the request it came from and the DMs still need their edit.
    """

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='staff_request_dms', on_delete=fields.CASCADE)
    tenant_id: int
    event: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.Event', related_name='staff_request_dms', on_delete=fields.SET_NULL, null=True,
    )
    event_id: Optional[int]
    kind = fields.CharEnumField(StaffRequestKind, max_length=32)
    object_id = fields.IntField()
    user: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.User', related_name='staff_request_dms', on_delete=fields.CASCADE)
    user_id: int
    channel_id = fields.BigIntField()
    message_id = fields.BigIntField()
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = 'staff_request_dm'
        table_description = 'Staff DMs about a pending request, edited once someone decides it.'
        indexes = (('tenant', 'kind', 'object_id'),)


class SentMessage(Model):
    """One DM Wizzrobe sent, as the recipient got it, and how each channel went.

    Written by ``DiscordService.send_dm`` (the chokepoint every notification
    flows through) so staff can answer "did they get told?" from Admin →
    Reports. ``body`` and ``embed`` can hold staff-private text, so the log is
    staff-only and tenant-scoped. ``discord_id`` is kept beside the nullable
    ``recipient`` because a DM to an id with no account still goes out.
    """

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='sent_messages', on_delete=fields.CASCADE)
    tenant_id: int
    event: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.Event', related_name='sent_messages', on_delete=fields.SET_NULL, null=True,
    )
    event_id: Optional[int]
    recipient: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.User', related_name='sent_messages', null=True, on_delete=fields.SET_NULL)
    recipient_id: Optional[int]
    discord_id = fields.CharField(max_length=32)
    title = fields.CharField(max_length=256, null=True)
    body = fields.TextField()
    embed = fields.JSONField(null=True)  # type: ignore[var-annotated]
    link_label = fields.CharField(max_length=80, null=True)
    link_url = fields.CharField(max_length=1024, null=True)
    silent = fields.BooleanField(default=False)
    notice_key = fields.CharField(max_length=64, null=True)
    audience = fields.CharField(max_length=32, null=True)
    status = fields.CharEnumField(SentMessageStatus, max_length=16, default=SentMessageStatus.PENDING)
    error = fields.CharField(max_length=500, null=True)
    push_devices = fields.IntField(null=True)
    email_sent = fields.BooleanField(null=True)
    telegram_sent = fields.BooleanField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = 'sent_message'
        table_description = 'Every DM sent in a community, for the staff Sent messages report.'
        indexes = (('tenant', 'created_at'), ('tenant', 'recipient'))
