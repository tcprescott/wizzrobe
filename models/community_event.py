
from tortoise import fields
from tortoise.models import Model

from .enums import CommunityEventStatus


class CommunityEvent(Model):
    """Something a member is running during the community's onsite event.

    Meetups, panels, side tournaments, a board game night: anything attendees
    hold for each other that isn't on the official schedule. Members submit them
    and staff approve them; only ``APPROVED`` events are listed and reminded.
    The listing carries a disclaimer that none of it is endorsed by event staff.

    ``description`` is markdown authored in the editor and is only ever rendered
    through the closed block parser (``application.content.blocks``), never as
    markup. ``starts_at`` is UTC; the end is derived from ``duration_minutes``.
    """

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='community_events', on_delete=fields.CASCADE,
    )
    tenant_id: int
    # Nullable until every write stamps it; the events migration backfilled the
    # rows that existed before (see docs/features/events.md).
    event: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Event', related_name='meetups', on_delete=fields.CASCADE,
    )
    event_id: int
    host: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.User', related_name='hosted_community_events', on_delete=fields.CASCADE,
    )
    host_id: int
    title = fields.CharField(max_length=120)
    description = fields.TextField(default='')
    location = fields.CharField(max_length=200, null=True)
    starts_at = fields.DatetimeField()
    duration_minutes = fields.IntField()
    status = fields.CharEnumField(
        CommunityEventStatus, default=CommunityEventStatus.PENDING, max_length=20,
    )
    # SET_NULL: the decision outlives the reviewer's account; the audit row keeps who.
    reviewed_by: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.User', related_name='reviewed_community_events',
        null=True, on_delete=fields.SET_NULL,
    )
    reviewed_at = fields.DatetimeField(null=True)
    # Why staff rejected or pulled it. Shown to the host, and sent in their DM.
    review_note = fields.TextField(null=True)
    # The host opted in to RSVPs; ``rsvp_capacity`` caps them (null: no cap).
    rsvp_enabled = fields.BooleanField(default=False)
    rsvp_capacity = fields.IntField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'community_event'
        indexes = (('tenant', 'status', 'starts_at'),)


class CommunityEventSubscription(Model):
    """A member asking to be DM'd before a community event starts.

    ``reminder_sent_at`` is stamped by the reminder worker so each subscriber
    hears once; moving the event's start clears it so the reminder fires again
    at the new time.

    ``going`` marks an RSVP: someone who said they're coming, which also gets
    them the reminder. It only counts while the event has RSVPs turned on.
    """

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='community_event_subscriptions', on_delete=fields.CASCADE,
    )
    tenant_id: int
    event: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.CommunityEvent', related_name='subscriptions', on_delete=fields.CASCADE,
    )
    event_id: int
    user: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.User', related_name='community_event_subscriptions', on_delete=fields.CASCADE,
    )
    user_id: int
    reminder_sent_at = fields.DatetimeField(null=True)
    going = fields.BooleanField(default=False)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = 'community_event_subscription'
        unique_together = (('event', 'user'),)
        indexes = (('tenant',), ('reminder_sent_at',))


class CommunityEventSeen(Model):
    """When a member last opened the community events listing.

    One row per member per event. An approved event listed after
    ``seen_at`` is new to them, which is what the count on Home's Event tab
    shows; opening the listing moves ``seen_at`` to now. No row means they
    have never looked, so everything listed is new.
    """

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='community_events_seen', on_delete=fields.CASCADE,
    )
    tenant_id: int
    # Nullable until every write stamps it; the events migration backfilled the
    # rows that existed before (see docs/features/events.md).
    event: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Event', related_name='meetup_seen', on_delete=fields.CASCADE,
    )
    event_id: int
    user: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.User', related_name='community_events_seen', on_delete=fields.CASCADE,
    )
    user_id: int
    seen_at = fields.DatetimeField()

    class Meta:
        table = 'community_event_seen'
        unique_together = (('tenant', 'event', 'user'),)
