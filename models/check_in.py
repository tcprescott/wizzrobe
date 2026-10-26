from typing import Optional

from tortoise import fields
from tortoise.models import Model

from .enums import CheckInDmStatus, CheckInEntrantSource, CheckInEventStatus, CheckInLinkMethod, CheckInTierLanyard


class CheckInEvent(Model):
    """An in-person event whose attendees are checked in at a desk.

    Registration happens on Matcherino: ``matcherino_venue_id`` names the
    ticketed venue whose badge sales are mirrored into :class:`CheckInPass`
    rows, one :class:`CheckInEntrant` per buyer. It is nullable so an event can
    run on walk-ups and comps alone.

    ``comp_roles`` (a list of ``Role`` values) and ``comp_volunteers`` say who
    gets a complimentary badge without buying one: holders of those roles in
    the community, and volunteers whose scheduled shifts reach the community's
    lowest volunteer comp tier. The sync puts them on the roster. The sync bookkeeping
    (``last_synced_at``/``last_sync_error``/``last_sync_count``) is shown at the
    desk because the Matcherino endpoints are unofficial: when they break, the
    volunteers need to see that the roster is stale.
    """

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='check_in_events', on_delete=fields.CASCADE,
    )
    tenant_id: int
    # Nullable until every write stamps it; the events migration backfilled the
    # rows that existed before (see docs/features/events.md).
    event: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.Event', related_name='check_in_events', on_delete=fields.CASCADE, null=True,
    )
    event_id: Optional[int]
    name = fields.CharField(max_length=255)
    matcherino_venue_id = fields.IntField(null=True)
    # mypy cannot infer a JSONField's type parameter, as with every sibling.
    comp_roles = fields.JSONField(default=list)  # type: ignore[var-annotated]
    comp_volunteers = fields.BooleanField(default=False)
    status = fields.CharEnumField(CheckInEventStatus, default=CheckInEventStatus.DRAFT, max_length=16)
    sync_interval_minutes = fields.IntField(default=5)
    last_synced_at = fields.DatetimeField(null=True)
    last_sync_error = fields.TextField(null=True)
    last_sync_count = fields.IntField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    entrants: fields.ReverseRelation["CheckInEntrant"]
    tiers: fields.ReverseRelation["CheckInTier"]
    passes: fields.ReverseRelation["CheckInPass"]

    class Meta:
        table = 'checkinevent'
        unique_together = (('tenant', 'matcherino_venue_id'),)


class CheckInEntrant(Model):
    """One person on a check-in event's roster.

    A ``MATCHERINO`` row is a badge buyer, keyed by ``matcherino_user_id``,
    with the identity Matcherino served on their purchase; a ``WALK_UP`` row
    was added by staff at the desk; a ``COMP`` row is someone comped by the
    event's comp rules who bought no badge. ``comp_reasons`` lists why a row
    (of any source) is comped: ``Role`` values and ``'volunteer'``. ``user`` is the matched Wizzrobe account,
    which is a nice-to-have rather than a requirement — plenty of buyers have
    none.

    ``source_data`` is the buyer's Matcherino account object as last served,
    kept so a field nothing reads yet can be backfilled from rows already
    synced. ``registered_at`` is their earliest badge purchase.

    A buyer whose every badge is refunded or gone, and who isn't comped, gets
    ``withdrawn_at`` (so does a ``COMP`` row that stops qualifying) rather than
    being deleted, so a check-in already recorded against them survives.
    """

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='check_in_entrants', on_delete=fields.CASCADE,
    )
    tenant_id: int
    event: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.CheckInEvent', related_name='entrants', on_delete=fields.CASCADE,
    )
    event_id: int
    source = fields.CharEnumField(CheckInEntrantSource, max_length=16)
    matcherino_user_id = fields.CharField(max_length=64, null=True)
    display_name = fields.CharField(max_length=255)
    avatar_url = fields.CharField(max_length=512, null=True)
    auth_provider = fields.CharField(max_length=32, null=True)
    auth_id = fields.CharField(max_length=128, null=True)
    registered_at = fields.DatetimeField(null=True)
    comp_reasons = fields.JSONField(null=True)  # type: ignore[var-annotated]
    # mypy cannot infer a JSONField's type parameter, as with every sibling.
    source_data = fields.JSONField(null=True)  # type: ignore[var-annotated]
    user: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.User', related_name='check_in_entries', null=True, on_delete=fields.SET_NULL
    )
    user_id: Optional[int]
    link_method = fields.CharEnumField(CheckInLinkMethod, max_length=24, null=True)
    linked_by: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.User', related_name='check_in_links_made', null=True, on_delete=fields.SET_NULL
    )
    withdrawn_at = fields.DatetimeField(null=True)
    checked_in_at = fields.DatetimeField(null=True)
    checked_in_by: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.User', related_name='check_ins_performed', null=True, on_delete=fields.SET_NULL
    )
    # The test DM sent at check-in (see CheckInDmStatus); null until one is tried.
    dm_status = fields.CharEnumField(CheckInDmStatus, max_length=16, null=True)
    dm_checked_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    passes: fields.ReverseRelation["CheckInPass"]

    class Meta:
        table = 'checkinentrant'
        unique_together = (('event', 'matcherino_user_id'), ('event', 'user'))
        indexes = (('event', 'checked_in_at'),)


class CheckInTier(Model):
    """A kind of badge sold on the event's Matcherino venue (Base, VIP, Day Pass).

    Mirrors a Matcherino *pass*. ``amount_cents`` orders tiers. ``lanyard`` is
    what the desk hands its holders; the sync guesses it from the title once
    (``VIP`` in the name → VIP, ``Day`` → Day Pass, else Base) and staff can
    change it, after which the sync leaves it alone.
    A tier Matcherino stops listing is kept: badges already sold still point
    at it.
    """

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='check_in_tiers', on_delete=fields.CASCADE,
    )
    tenant_id: int
    event: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.CheckInEvent', related_name='tiers', on_delete=fields.CASCADE,
    )
    event_id: int
    matcherino_pass_id = fields.IntField()
    title = fields.CharField(max_length=255)
    amount_cents = fields.IntField(default=0)
    role = fields.CharField(max_length=32, null=True)
    lanyard = fields.CharEnumField(CheckInTierLanyard, max_length=16, default=CheckInTierLanyard.BASE)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    passes: fields.ReverseRelation["CheckInPass"]

    class Meta:
        table = 'checkintier'
        unique_together = (('event', 'matcherino_pass_id'),)


class CheckInPass(Model):
    """One badge bought on the event's Matcherino venue.

    One row per purchase, not per person: a buyer can hold several badges (one
    of them often for a friend, whom the venue doesn't name), and ``code`` is
    what that friend shows at the door. ``entrant`` is the buyer's roster row.

    ``refunded_at`` is Matcherino's refund; ``removed_at`` marks a purchase
    that dropped out of the feed, kept rather than deleted for the same reason
    an entrant is withdrawn rather than deleted. ``source_data`` holds the
    purchase as served, minus the buyer's contact details and the payment
    ledger, which check-in has no use for.
    """

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='check_in_passes', on_delete=fields.CASCADE,
    )
    tenant_id: int
    event: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.CheckInEvent', related_name='passes', on_delete=fields.CASCADE,
    )
    event_id: int
    tier: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.CheckInTier', related_name='passes', on_delete=fields.CASCADE,
    )
    tier_id: int
    entrant: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.CheckInEntrant', related_name='passes', null=True, on_delete=fields.SET_NULL,
    )
    entrant_id: Optional[int]
    matcherino_purchase_id = fields.IntField()
    buyer_matcherino_user_id = fields.CharField(max_length=64)
    code = fields.CharField(max_length=32)
    purchased_at = fields.DatetimeField(null=True)
    refunded_at = fields.DatetimeField(null=True)
    removed_at = fields.DatetimeField(null=True)
    # mypy cannot infer a JSONField's type parameter, as with every sibling.
    source_data = fields.JSONField(null=True)  # type: ignore[var-annotated]
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'checkinpass'
        unique_together = (('event', 'matcherino_purchase_id'),)
        indexes = (('event', 'code'),)


class MatcherinoLogin(Model):
    """The Matcherino account a community's check-in sync signs in as.

    One per tenant. ``refresh_token`` is the long-lived token Matcherino's web
    app keeps; the client trades it for a day-long access token on each use.
    It is stored in plaintext (like ``RandomizerCredential.value``) and is
    **privileged**: ``MatcherinoLoginService`` hands it only to the check-in
    sync, never to a page, and never writes it into an audit entry.
    ``matcherino_user_id`` is the account the token signed in as when it was
    saved, so staff can see whose login it is without seeing the secret.

    ``alert_user`` is the one person DMed when Matcherino refuses the login
    during a sync; ``alerted_at`` marks that the DM for the current failure has
    gone out, so a sync retrying every few minutes sends it once. A good sync or
    a freshly saved token clears it.
    """

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='matcherino_logins', on_delete=fields.CASCADE,
    )
    tenant_id: int
    refresh_token = fields.TextField()
    matcherino_user_id = fields.CharField(max_length=64, null=True)
    # SET_NULL: the login outlives whoever pasted it; the audit row keeps who.
    updated_by: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.User', related_name='matcherino_logins_updated', null=True, on_delete=fields.SET_NULL,
    )
    updated_by_id: Optional[int]
    alert_user: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.User', related_name='matcherino_login_alerts', null=True, on_delete=fields.SET_NULL,
    )
    alert_user_id: Optional[int]
    alerted_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'matcherinologin'
        unique_together = (('tenant',),)
