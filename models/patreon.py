from typing import Optional

from tortoise import fields
from tortoise.models import Model

from .enums import PatreonOAuthPurpose


class PatreonCampaign(Model):
    """A community's connected Patreon campaign: the creator's OAuth grant and our webhook on it.

    One per community. ``access_token``, ``refresh_token`` and
    ``webhook_secret`` are sealed with ``secret_box`` (the tenant id as
    context) and never leave the server. ``webhook_token`` is the opaque part
    of the URL Patreon posts to; it names the row, the signature proves the
    sender. ``sync_requested_at`` asks the sync worker for a full pass on its
    next tick.
    """

    id = fields.IntField(pk=True)
    tenant: fields.OneToOneRelation = fields.OneToOneField(
        'models.Tenant', related_name='patreon_campaign', on_delete=fields.CASCADE,
    )
    tenant_id: int
    campaign_id = fields.CharField(max_length=64)
    campaign_name = fields.CharField(max_length=255, null=True)
    access_token = fields.TextField()
    refresh_token = fields.TextField(null=True)
    token_expires_at = fields.DatetimeField(null=True)
    webhook_id = fields.CharField(max_length=64, null=True)
    webhook_secret = fields.TextField(null=True)
    webhook_token = fields.CharField(max_length=64, unique=True)
    connected_by: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.User', related_name='patreon_campaigns_connected', null=True, on_delete=fields.SET_NULL,
    )
    connected_by_id: Optional[int]
    connected_at = fields.DatetimeField(auto_now_add=True)
    last_synced_at = fields.DatetimeField(null=True)
    last_sync_error = fields.CharField(max_length=255, null=True)
    sync_requested_at = fields.DatetimeField(null=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'patreon_campaign'


class PatreonTierBadge(Model):
    """One tier of the community's campaign and the badge label staff gave it.

    ``badge_label`` empty means the default label. The lowest ``sort`` among
    the tiers someone holds picks their label.
    """

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='patreon_tier_badges', on_delete=fields.CASCADE,
    )
    tenant_id: int
    tier_id = fields.CharField(max_length=64)
    tier_title = fields.CharField(max_length=255)
    badge_label = fields.CharField(max_length=40, null=True)
    sort = fields.IntField(default=0)

    class Meta:
        table = 'patreon_tier_badge'
        unique_together = (('tenant', 'tier_id'),)


class PatreonMembership(Model):
    """A linked member's standing in this community's campaign, as Patreon last reported it.

    Only people who linked Patreon get a row. Holds the status and the tier ids,
    never a pledge amount.
    """

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='patreon_memberships', on_delete=fields.CASCADE,
    )
    tenant_id: int
    user: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.User', related_name='patreon_memberships', on_delete=fields.CASCADE,
    )
    user_id: int
    patreon_member_id = fields.CharField(max_length=64, null=True)
    patron_status = fields.CharField(max_length=32, null=True)
    tier_ids = fields.JSONField(default=list)  # type: ignore[var-annotated]
    synced_at = fields.DatetimeField()

    class Meta:
        table = 'patreon_membership'
        unique_together = (('tenant', 'user'),)


class PatreonOAuthState(Model):
    """A Patreon sign-in in flight: who started it, where, and for what.

    Stored by the SHA-256 of the state sent to Patreon, single use, ten
    minutes. The callback arrives without a session, so this row is what ties
    it to the person who started it.
    """

    id = fields.IntField(pk=True)
    state_hash = fields.CharField(max_length=64, unique=True)
    purpose = fields.CharEnumField(PatreonOAuthPurpose, max_length=16)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='patreon_oauth_states', on_delete=fields.CASCADE,
    )
    tenant_id: int
    user: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.User', related_name='patreon_oauth_states', on_delete=fields.CASCADE,
    )
    user_id: int
    expires_at = fields.DatetimeField()
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = 'patreon_oauth_state'
