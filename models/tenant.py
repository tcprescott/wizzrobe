
from tortoise import fields
from tortoise.models import Model

from models.enums import JoinRequestStatus, MembershipSource


class Tenant(Model):
    """One independent tournament community hosted on the shared deployment.

    Logical multitenancy: a single database, shared tables, and this row as the
    discriminator every scoped model points at. Addressable path-based at
    ``/t/<slug>`` on the platform host; ``domain`` is reserved for optional
    host-based addressing (not yet resolved). ``discord_guild_id`` is the routing
    key the shared one-bot-many-guilds process uses to map a guild back to its
    tenant(s) — it is **not** unique, so several communities may share one Discord
    server and the bot fans out over every linked tenant. ``config`` holds
    per-tenant knobs that don't warrant columns.
    """

    id = fields.IntField(pk=True)
    name = fields.CharField(max_length=255)
    slug = fields.CharField(max_length=64, unique=True, index=True)
    domain = fields.CharField(max_length=255, null=True, unique=True, index=True)
    discord_guild_id = fields.BigIntField(null=True, index=True)
    is_active = fields.BooleanField(default=True)
    feature_group = fields.ForeignKeyField(
        'models.FeatureFlagGroup', related_name='tenants',
        on_delete=fields.SET_NULL, null=True,
    )
    config = fields.JSONField(default=dict)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'tenant'


class TenantMembership(Model):
    """Ties a global :class:`User` to a :class:`Tenant` they belong to.

    Queried across tenants (never auto-scoped): membership is what the auth layer
    checks to decide whether an authenticated user may see a tenant at all.
    """

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='memberships', on_delete=fields.CASCADE)
    user = fields.ForeignKeyField('models.User', related_name='tenant_memberships', on_delete=fields.CASCADE)
    user_id: int
    tenant_id: int
    source = fields.CharEnumField(MembershipSource, max_length=32, null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = 'tenantmembership'
        unique_together = (('user', 'tenant'),)
        indexes = (('tenant',),)


class TenantMemberBlock(Model):
    """Someone staff removed, kept out of the ways in that need no staff decision.

    Open membership and Discord auto-join let people in on their own, so a
    removal without this lasted until the person's next page view. A block
    stops those two paths only: a join request still reaches staff, and any
    staff add (by hand, an approved request, a role grant) lifts it. Cross-tenant
    like :class:`TenantMembership`, and for the same reason.
    """

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='member_blocks', on_delete=fields.CASCADE)  # type: ignore[var-annotated]
    tenant_id: int
    user = fields.ForeignKeyField('models.User', related_name='tenant_blocks', on_delete=fields.CASCADE)  # type: ignore[var-annotated]
    user_id: int
    blocked_by = fields.ForeignKeyField(  # type: ignore[var-annotated]
        'models.User', related_name='tenant_blocks_made', null=True, on_delete=fields.SET_NULL
    )
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = 'tenantmemberblock'
        unique_together = (('user', 'tenant'),)

class TenantLogo(Model):
    """A community's logo: the header mark and browser tab icon.

    Holds only the normalised WebP the service produced, never the upload as
    sent. The bytes live here rather than in ``Tenant.config`` because config is
    read on every page build; config carries just the hash (``config['logo']``)
    so the chrome can build the content-addressed URL without loading the image.
    """

    id = fields.IntField(pk=True)
    tenant = fields.OneToOneField('models.Tenant', related_name='logo', on_delete=fields.CASCADE)  # type: ignore[var-annotated]
    tenant_id: int
    content = fields.BinaryField()
    sha256 = fields.CharField(max_length=64)
    uploaded_by = fields.ForeignKeyField(  # type: ignore[var-annotated]
        'models.User', related_name='tenant_logos_uploaded', null=True, on_delete=fields.SET_NULL
    )
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'tenantlogo'


class TenantJoinRequest(Model):
    """Someone asking to join a community they can see the door of.

    The enrollment path whose absence was the documented reason no membership
    gate existed. Cross-tenant by nature, like :class:`TenantMembership`: a
    user's own list of pending requests spans tenants, and the request is
    written by someone who is not scoped to the target tenant at all.
    """

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='join_requests', on_delete=fields.CASCADE)
    tenant_id: int
    user = fields.ForeignKeyField('models.User', related_name='join_requests', on_delete=fields.CASCADE)
    user_id: int
    status = fields.CharEnumField(JoinRequestStatus, default=JoinRequestStatus.PENDING, max_length=20)
    message = fields.CharField(max_length=500, null=True)
    decided_by = fields.ForeignKeyField(
        'models.User', related_name='join_requests_decided', null=True, on_delete=fields.SET_NULL
    )
    decided_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'tenantjoinrequest'
        unique_together = (('user', 'tenant'),)
        indexes = (('tenant', 'status'),)


class ProfileNudgeDismissal(Model):
    """A member acknowledging one of their community's recommended profile items.

    The rules themselves live in ``Tenant.config['profile_nudges']``; this row
    records that ``user`` has seen the banner for ``check`` in this community and
    chosen not to act on it. Per tenant, because each community asks for its own
    things: dismissing the Challonge nudge in one does not answer another's.
    A row outlives its rule on purpose, so switching a rule off and on again
    does not re-nag everyone who already said no.
    """

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='profile_nudge_dismissals', on_delete=fields.CASCADE)
    tenant_id: int
    user: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.User', related_name='profile_nudge_dismissals', on_delete=fields.CASCADE)
    user_id: int
    # A plain string, not an enum, so retiring a ProfileCheck never strands rows.
    check = fields.CharField(max_length=32)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = 'profilenudgedismissal'
        unique_together = (('tenant', 'user', 'check'),)


class WatchLayoutVote(Model):
    """One browser's rating of one Watch page layout, from the layout trial.

    Retired: the community kept the one-page layout, so nothing in the app reads or
    writes these rows any more. The table stays to keep the trial's votes.
    """

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='watch_layout_votes', on_delete=fields.CASCADE)
    tenant_id: int
    # Keep nullable: not every write path stamps it yet.
    event: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Event', related_name='watch_layout_votes', on_delete=fields.CASCADE,
    )
    event_id: int
    session_id = fields.CharField(max_length=64)
    layout = fields.CharField(max_length=16)
    vote = fields.CharField(max_length=8, null=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'watchlayoutvote'
        unique_together = (('tenant', 'event', 'session_id', 'layout'),)
