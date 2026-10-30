from typing import Optional

from tortoise import fields
from tortoise.models import Model

from .enums import Role, RoleSource, TournamentGrant


class User(Model):
    id = fields.IntField(pk=True)
    # Nullable only for placeholders: a DB CHECK requires discord_id unless is_placeholder.
    discord_id = fields.BigIntField(unique=True, null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)
    username = fields.CharField(max_length=150)
    display_name = fields.CharField(max_length=150, null=True)
    discord_avatar = fields.CharField(max_length=64, null=True)
    pronouns = fields.CharField(max_length=50, null=True)
    is_active = fields.BooleanField(default=True)
    # Resolve via UserService.get_system_user(); never construct the system user ad hoc.
    is_system = fields.BooleanField(default=False)
    dm_notifications = fields.BooleanField(default=True)
    dm_match_comments = fields.BooleanField(default=True)
    timezone = fields.CharField(max_length=64, null=True)
    hour_cycle = fields.CharField(max_length=2, null=True)
    challonge_user_id = fields.CharField(max_length=64, null=True, unique=True)
    challonge_username = fields.CharField(max_length=255, null=True)
    challonge_linked_at = fields.DatetimeField(null=True)
    twitch_user_id = fields.CharField(max_length=64, null=True, unique=True)
    twitch_username = fields.CharField(max_length=255, null=True)
    twitch_linked_at = fields.DatetimeField(null=True)
    racetime_user_id = fields.CharField(max_length=64, null=True, unique=True)
    racetime_username = fields.CharField(max_length=255, null=True)
    racetime_linked_at = fields.DatetimeField(null=True)
    startgg_user_id = fields.CharField(max_length=64, null=True, unique=True)
    startgg_username = fields.CharField(max_length=255, null=True)
    startgg_linked_at = fields.DatetimeField(null=True)
    is_placeholder = fields.BooleanField(default=False)
    speedgaming_id = fields.CharField(max_length=64, null=True, unique=True)
    # Deliberately not unique: a typo would lock the rightful owner out of their handle.
    matcherino_username = fields.CharField(max_length=255, null=True)
    matcherino_user_id = fields.CharField(max_length=64, null=True, unique=True)
    # login_name is stored lower-cased; case-insensitive lookups depend on it.
    login_name = fields.CharField(max_length=64, null=True, unique=True)
    password_hash = fields.CharField(max_length=255, null=True)
    password_changed_at = fields.DatetimeField(null=True)
    # Only ever store a confirmed email (a code we sent, or Discord's verified address).
    email = fields.CharField(max_length=254, null=True)
    email_verified_at = fields.DatetimeField(null=True)
    email_notifications = fields.BooleanField(default=False)
    telegram_chat_id = fields.BigIntField(null=True, unique=True)
    telegram_username = fields.CharField(max_length=64, null=True)
    telegram_linked_at = fields.DatetimeField(null=True)
    telegram_notifications = fields.BooleanField(default=False)

    admin_tournaments = fields.ManyToManyRelation["Tournament"]
    crew_coordinated_tournaments = fields.ManyToManyRelation["Tournament"]
    match_players = fields.ReverseRelation["MatchPlayers"]
    match_acknowledgments = fields.ReverseRelation["MatchAcknowledgment"]
    tournament_players = fields.ReverseRelation["TournamentPlayers"]
    tournament_notifications = fields.ReverseRelation["TournamentNotificationPreference"]
    commentaries = fields.ReverseRelation["Commentator"]
    approved_commentaries = fields.ReverseRelation["Commentator"]
    trackers = fields.ReverseRelation["Tracker"]
    approved_trackers = fields.ReverseRelation["Tracker"]
    broadcasts = fields.ReverseRelation["Broadcaster"]  # type: ignore[name-defined]
    approved_broadcasts = fields.ReverseRelation["Broadcaster"]  # type: ignore[name-defined]
    watched_matches = fields.ReverseRelation["MatchWatcher"]
    roles = fields.ReverseRelation["UserRole"]
    granted_roles = fields.ReverseRelation["UserRole"]
    audit_logs = fields.ReverseRelation["AuditLog"]
    triforce_texts = fields.ReverseRelation["TriforceText"]
    triforce_texts_moderated = fields.ReverseRelation["TriforceText"]
    api_tokens = fields.ReverseRelation["ApiToken"]
    feedback_submissions = fields.ReverseRelation["Feedback"]
    accommodation_requests = fields.ReverseRelation["AccommodationRequest"]  # type: ignore[name-defined]
    emergency_contacts = fields.ReverseRelation["EmergencyContact"]  # type: ignore[name-defined]
    owned_equipment = fields.ReverseRelation["Equipment"]
    equipment_loans = fields.ReverseRelation["EquipmentLoan"]
    equipment_checkouts_performed = fields.ReverseRelation["EquipmentLoan"]
    equipment_checkins_performed = fields.ReverseRelation["EquipmentLoan"]
    volunteer_assignments = fields.ReverseRelation["VolunteerAssignment"]
    volunteer_assignments_made = fields.ReverseRelation["VolunteerAssignment"]
    volunteer_qualifications = fields.ReverseRelation["VolunteerQualification"]
    volunteer_availability = fields.ReverseRelation["VolunteerAvailability"]
    challonge_participations = fields.ReverseRelation["ChallongeParticipant"]
    web_push_subscriptions = fields.ReverseRelation["WebPushSubscription"]
    contact_verifications = fields.ReverseRelation["ContactVerification"]
    tenant_memberships = fields.ReverseRelation["TenantMembership"]
    profile_nudge_dismissals = fields.ReverseRelation["ProfileNudgeDismissal"]  # type: ignore[name-defined]

    @property
    def preferred_name(self) -> str:
        return self.display_name if self.display_name else self.username


class ApiToken(Model):
    """A bearer credential acting as its owning user.

    Only the SHA-256 hash of the token is stored; the plaintext is shown once at
    creation (PAT) or returned once from the token endpoint (OAuth). A token acts
    with the full permissions of ``user`` unless ``read_only`` is set.

    Two kinds share this table, distinguished by ``origin``:

    - ``pat`` — a personal access token created on the profile page. ``tenant``
      is set, and the REST API derives the request's community from it.
    - ``oauth`` — issued by the MCP authorization server. ``tenant`` is NULL:
      the token is platform-wide and each MCP tool call names its own community.
      Rejected at ``/api``; a PAT is likewise rejected at ``/mcp``.
    - ``app`` — a grant a person approved for a third-party app
      (``oauth_app``). ``tenant`` is the community picked on the consent screen
      and ``scope`` is what they approved. REST only; rejected at ``/mcp``.

    One table so revocation, expiry, and the profile listing have a single
    implementation for both.
    """

    id = fields.IntField(pk=True)
    # Keep token_hash globally unique: it's looked up before any tenant context exists.
    tenant = fields.ForeignKeyField(
        'models.Tenant', related_name='api_tokens', on_delete=fields.CASCADE, null=True
    )
    user = fields.ForeignKeyField('models.User', related_name='api_tokens')
    user_id: int
    tenant_id: Optional[int]
    name = fields.CharField(max_length=100)
    token_hash = fields.CharField(max_length=64, unique=True, index=True)
    token_prefix = fields.CharField(max_length=24)
    read_only = fields.BooleanField(default=False)
    origin = fields.CharField(max_length=16, default='pat', index=True)
    oauth_client = fields.ForeignKeyField(
        'models.McpOAuthClient', related_name='tokens', on_delete=fields.CASCADE, null=True
    )
    oauth_app: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.OAuthApp', related_name='grants', on_delete=fields.CASCADE, null=True
    )
    oauth_app_id: Optional[int]
    refresh_token_hash = fields.CharField(max_length=64, null=True, unique=True, index=True)
    previous_refresh_hash = fields.CharField(max_length=64, null=True, index=True)
    refresh_expires_at = fields.DatetimeField(null=True)
    scope = fields.CharField(max_length=255, null=True)
    last_used_at = fields.DatetimeField(null=True)
    expires_at = fields.DatetimeField(null=True)
    revoked_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = 'apitoken'


class UserRole(Model):
    id = fields.IntField(pk=True)
    user = fields.ForeignKeyField('models.User', related_name='roles')
    # NULL only for SUPER_ADMIN, the one global role.
    tenant = fields.ForeignKeyField(
        'models.Tenant', related_name='user_roles', null=True, on_delete=fields.CASCADE
    )
    role = fields.CharEnumField(Role, max_length=32)
    event = fields.ForeignKeyField(  # type: ignore[var-annotated]
        'models.Event', related_name='user_roles', null=True, on_delete=fields.CASCADE,
    )
    granted_by = fields.ForeignKeyField(
        'models.User', related_name='granted_roles', null=True, on_delete=fields.SET_NULL
    )
    source = fields.CharEnumField(RoleSource, max_length=16, default=RoleSource.MANUAL)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        # NULLs are distinct, so this only holds per-event rows; migration 141's partial index holds the rest.
        unique_together = ('user', 'role', 'tenant', 'event')
        table = 'userrole'
        indexes = (('role',),)


class DiscordRoleMapping(Model):
    """One guild role → one grant. Exactly one of ``app_role`` (tenant-wide) or
    ``tournament_grant`` + ``tournament`` (scoped to one event) is set.

    The two shapes share a table because they answer the same question — where
    does this person's authority come from — and staff manage them on one screen.
    ``DiscordRoleMappingService`` enforces the either/or; the database cannot,
    since the constraint spans columns.
    """

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='discord_role_mappings', on_delete=fields.CASCADE)
    # A guild can be shared by several tenants: always combine guild_id with the tenant scope.
    guild_id = fields.BigIntField()
    discord_role_id = fields.BigIntField()
    discord_role_name = fields.CharField(max_length=100)
    app_role = fields.CharEnumField(Role, max_length=32, null=True)
    event = fields.ForeignKeyField(  # type: ignore[var-annotated]
        'models.Event', related_name='discord_role_mappings', null=True, on_delete=fields.CASCADE,
    )
    tournament_grant = fields.CharEnumField(TournamentGrant, max_length=32, null=True)
    tournament = fields.ForeignKeyField(  # type: ignore[var-annotated]
        'models.Tournament', related_name='discord_role_mappings',
        null=True, on_delete=fields.CASCADE,
    )
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        # NULLs are distinct, so each key only bites on rows with all its columns set.
        unique_together = (
            ('tenant', 'discord_role_id', 'app_role', 'event'),
            ('tenant', 'discord_role_id', 'tournament_grant', 'tournament'),
        )
        table = 'discordrolemapping'


class DiscordTournamentGrant(Model):
    """Provenance for a per-tournament grant the guild-role sync made.

    The grant itself lives on ``Tournament.admins`` / ``.crew_coordinators``,
    which are bare join tables with nowhere to record who made the row. This is
    the ``UserRole.source`` equivalent for them: the sync only ever revokes
    grants recorded here, so a hand-made Tournament Admin survives a re-sync.
    A staff member granting the same thing by hand deletes the row, which pins
    the grant as manual exactly the way ``RoleSource.MANUAL`` does.
    """

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField(  # type: ignore[var-annotated]
        'models.Tenant', related_name='discord_tournament_grants', on_delete=fields.CASCADE
    )
    tournament = fields.ForeignKeyField(  # type: ignore[var-annotated]
        'models.Tournament', related_name='discord_tournament_grants', on_delete=fields.CASCADE
    )
    user = fields.ForeignKeyField(  # type: ignore[var-annotated]
        'models.User', related_name='discord_tournament_grants', on_delete=fields.CASCADE
    )
    grant = fields.CharEnumField(TournamentGrant, max_length=32)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        unique_together = ('tournament', 'user', 'grant')
        table = 'discordtournamentgrant'


class WebPushSubscription(Model):
    """One browser/device push subscription for a user (Web Push, RFC 8030).

    ``endpoint`` is the push-service URL the browser handed out and is unique
    per subscription; ``p256dh``/``auth`` are the client keys every message is
    encrypted against (RFC 8291). A user may hold one row per device/browser.
    """

    id = fields.IntField(pk=True)
    user = fields.ForeignKeyField(
        'models.User', related_name='web_push_subscriptions', on_delete=fields.CASCADE
    )
    endpoint = fields.CharField(max_length=1024, unique=True)
    p256dh = fields.CharField(max_length=128)
    auth = fields.CharField(max_length=64)
    user_agent = fields.CharField(max_length=255, null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    last_used_at = fields.DatetimeField(null=True)

    class Meta:
        table = 'webpushsubscription'
        indexes = (('user',),)


class ContactVerification(Model):
    """A pending proof that a person controls an email address or Telegram chat.

    ``channel='email'`` holds the address being confirmed and the hash of the
    6-digit code we mailed to it; ``channel='telegram'`` holds the hash of the
    one-time token in the ``t.me`` start link. Only hashes are stored, rows
    expire, and a user has at most one open row per channel (a new request
    replaces the old one). Global like ``User``: a channel is identity.
    """

    id = fields.IntField(pk=True)
    user = fields.ForeignKeyField(
        'models.User', related_name='contact_verifications', on_delete=fields.CASCADE
    )
    channel = fields.CharField(max_length=16)
    target = fields.CharField(max_length=254, null=True)
    secret_hash = fields.CharField(max_length=64, unique=True)
    attempts = fields.IntField(default=0)
    expires_at = fields.DatetimeField()
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = 'contactverification'
        unique_together = (('user', 'channel'),)
