from typing import Optional

from tortoise import fields
from tortoise.models import Model

from .enums import Role, RoleSource, TournamentGrant


class User(Model):
    id = fields.IntField(pk=True)
    # Nullable+unique (Postgres allows many NULLs) so an unresolved SpeedGaming
    # player can exist as a *placeholder* User (``is_placeholder=True``,
    # ``discord_id=NULL``) — keeping ``MatchPlayers.user`` NOT NULL. A DB CHECK
    # (``discord_id IS NOT NULL OR is_placeholder``) enforces that only
    # placeholders may lack a discord id; see migration 26. A placeholder is
    # *upgraded in place* to a real User when its ``discord_id`` later appears.
    discord_id = fields.BigIntField(unique=True, null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)
    username = fields.CharField(max_length=150)
    display_name = fields.CharField(max_length=150, null=True)
    # Discord avatar *hash*, not a URL: the CDN path is derived from it and the
    # discord id (``application/utils/discord_avatar.py``), so a stale row costs
    # one broken image rather than a wrong link. Refreshed at every web login and
    # whenever the bot sees the member change it. NULL means "no custom avatar" —
    # surfaces fall back to their own placeholder.
    discord_avatar = fields.CharField(max_length=64, null=True)
    pronouns = fields.CharField(max_length=50, null=True)
    is_active = fields.BooleanField(default=True)
    # Marks the single reserved automation actor (sentinel ``discord_id`` =
    # ``SYSTEM_USER_DISCORD_ID``). Workers/bots pass this row as ``actor`` so
    # audit rows snapshot a real username instead of a bare sentinel. Resolve it
    # via ``UserService.get_system_user()``, never construct it ad hoc.
    is_system = fields.BooleanField(default=False)
    dm_notifications = fields.BooleanField(default=True)
    # A DM when staff leave a comment on one of this person's matches that
    # players can read. Separate from ``dm_notifications``, which turns off
    # every DM at once.
    dm_match_comments = fields.BooleanField(default=True)
    # Display-timezone preference (IANA name). NULL means "detect from my
    # device" — the browser reports its zone and that is used. Only consulted
    # when the community leaves the choice to its members; a community that
    # pins one clock overrides this. Global, like the rest of identity: a
    # person carries one timezone across every community they belong to.
    timezone = fields.CharField(max_length=64, null=True)
    # Clock preference: ``'12'`` or ``'24'``. NULL means "detect from my
    # browser", then 12-hour; the same shape as ``timezone`` above. Display
    # only, and only on the person's own pages: notification copies are 12-hour.
    hour_cycle = fields.CharField(max_length=2, null=True)
    # Verified Challonge identity (captured via one-time OAuth, scope ``me``).
    # Identity only — we do not retain a player's Challonge access token.
    # Unique so bracket sync can resolve a Challonge id to exactly one user
    # (Postgres allows multiple NULLs, so unlinked users are unconstrained).
    challonge_user_id = fields.CharField(max_length=64, null=True, unique=True)
    challonge_username = fields.CharField(max_length=255, null=True)
    challonge_linked_at = fields.DatetimeField(null=True)
    # Verified Twitch identity (captured via one-time OAuth). Identity only — we
    # do not retain a user's Twitch access token. Unique so a Twitch id resolves
    # to exactly one user (Postgres allows multiple NULLs, so unlinked users are
    # unconstrained).
    twitch_user_id = fields.CharField(max_length=64, null=True, unique=True)
    twitch_username = fields.CharField(max_length=255, null=True)
    twitch_linked_at = fields.DatetimeField(null=True)
    # Verified racetime.gg identity (captured via one-time OAuth, read scope).
    # Identity only — we do not retain a user's racetime access token. Unique so a
    # racetime id resolves to exactly one user (Postgres allows multiple NULLs, so
    # unlinked users are unconstrained).
    racetime_user_id = fields.CharField(max_length=64, null=True, unique=True)
    racetime_username = fields.CharField(max_length=255, null=True)
    racetime_linked_at = fields.DatetimeField(null=True)
    # Verified start.gg identity (captured via one-time OAuth, ``user.identity``).
    # Identity only, like Twitch: the token is discarded. Unique so an entrant's
    # start.gg user id resolves to exactly one user.
    startgg_user_id = fields.CharField(max_length=64, null=True, unique=True)
    startgg_username = fields.CharField(max_length=255, null=True)
    startgg_linked_at = fields.DatetimeField(null=True)
    # Placeholder identity (SpeedGaming ETL, PR 7). A player the SG sync could not
    # resolve to a real account becomes a placeholder User so its ``MatchPlayers``
    # row is still first-class. ``speedgaming_id`` is the SG-side numeric id used
    # to re-find the same placeholder across syncs; unique so an SG id maps to one
    # User (Postgres allows many NULLs, so non-SG users are unconstrained).
    is_placeholder = fields.BooleanField(default=False)
    speedgaming_id = fields.CharField(max_length=64, null=True, unique=True)
    # Matcherino handle, in that platform's ``name#id`` form. Self-entered,
    # unlike the OAuth-verified links above, so deliberately NOT unique: a typo
    # under a unique constraint would lock the rightful owner out of their own
    # handle. Global like the rest of identity — someone who wins in two
    # communities has one Matcherino account.
    matcherino_username = fields.CharField(max_length=255, null=True)
    # Matcherino's numeric account id. Unlike the handle above this is only
    # written from evidence: a check-in match on an OAuth-verified Discord or
    # Twitch id, or a link confirmed at the desk (never from the typed handle),
    # so it is unique. It is how a registrant linked at one event is recognised
    # at the next, whatever they have since renamed themselves.
    matcherino_user_id = fields.CharField(max_length=64, null=True, unique=True)
    # Password sign-in, set only by a platform super-admin (no self-service).
    # A password account may have no ``discord_id`` at all: the DB CHECK
    # ``ck_user_identity`` requires a discord id, a password, or placeholder
    # status. ``login_name`` is stored lower-cased so lookups are
    # case-insensitive. ``password_changed_at`` stamps each password session;
    # a change or removal moves it, which ends every older password session.
    login_name = fields.CharField(max_length=64, null=True, unique=True)
    password_hash = fields.CharField(max_length=255, null=True)
    password_changed_at = fields.DatetimeField(null=True)
    # Extra notification channels, each a copy of what goes out as a Discord DM
    # (see ``NotificationChannelService``). Both are global identity like the
    # rest of this row; whether anything is sent also needs the current
    # community's ``notification_channels`` flag. ``email`` is only ever stored
    # confirmed: by a code we sent, or from Discord's own ``verified`` address.
    email = fields.CharField(max_length=254, null=True)
    email_verified_at = fields.DatetimeField(null=True)
    email_notifications = fields.BooleanField(default=False)
    # Unique so one Telegram chat can't be bound to two accounts.
    telegram_chat_id = fields.BigIntField(null=True, unique=True)
    telegram_username = fields.CharField(max_length=64, null=True)
    telegram_linked_at = fields.DatetimeField(null=True)
    telegram_notifications = fields.BooleanField(default=False)

    # related fields
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
    # NULL for OAuth tokens (platform-wide). token_hash stays globally unique —
    # the lookup happens before any tenant context exists (api/dependencies.py,
    # mcpserver/asgi.py).
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
    # Refresh tokens are hashed like access tokens and rotate on every use.
    refresh_token_hash = fields.CharField(max_length=64, null=True, unique=True, index=True)
    # The refresh hash this row rotated away from. Presenting it again means a
    # spent refresh token is in someone else's hands, so the grant is revoked.
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
    # Nullable: every grant is per-tenant EXCEPT SUPER_ADMIN, which carries
    # tenant=NULL and stays visible inside any tenant request (the only global
    # role). CASCADE so a deleted tenant drops its per-tenant grants; NULL
    # super-admin rows are untouched.
    tenant = fields.ForeignKeyField(
        'models.Tenant', related_name='user_roles', null=True, on_delete=fields.CASCADE
    )
    role = fields.CharEnumField(Role, max_length=32)
    # The event a per-event role (``Role.is_per_event``) is held in; NULL for a
    # community-wide role. CASCADE, so a grant goes with its event, and that
    # makes the row archive-guarded: an archived event's grants can't change.
    event = fields.ForeignKeyField(  # type: ignore[var-annotated]
        'models.Event', related_name='user_roles', null=True, on_delete=fields.CASCADE,
    )
    # SET_NULL: deleting the granter must not revoke roles they granted to others.
    granted_by = fields.ForeignKeyField(
        'models.User', related_name='granted_roles', null=True, on_delete=fields.SET_NULL
    )
    source = fields.CharEnumField(RoleSource, max_length=16, default=RoleSource.MANUAL)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        # Postgres treats NULLs as distinct, so this only holds per-event rows;
        # migration 141's partial index ``uid_userrole_community`` holds the
        # community-wide ones (event_id IS NULL) to one per user and role.
        unique_together = ('user', 'role', 'tenant', 'event')
        table = 'userrole'
        indexes = (('role',),)  # composite is user-first; role-only enumeration uncovered


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
    # The Discord guild these mappings apply to. A guild may be shared by several
    # tenants, so ``guild_id`` alone does not isolate a tenant — reads combine it
    # with the tenant scope, and the unique key is (tenant, discord_role_id, app_role).
    guild_id = fields.BigIntField()
    discord_role_id = fields.BigIntField()
    discord_role_name = fields.CharField(max_length=100)
    app_role = fields.CharEnumField(Role, max_length=32, null=True)
    # The event a per-event ``app_role`` is granted in, named when the mapping
    # is made (the default event, unless staff pick another). Fixed after that:
    # moving the default never re-points a mapping. NULL for a community-wide
    # role and for tournament grants, whose tournament names the event.
    event = fields.ForeignKeyField(  # type: ignore[var-annotated]
        'models.Event', related_name='discord_role_mappings', null=True, on_delete=fields.CASCADE,
    )
    tournament_grant = fields.CharEnumField(TournamentGrant, max_length=32, null=True)
    # CASCADE: a mapping onto a deleted tournament grants nothing, so it goes with it.
    tournament = fields.ForeignKeyField(  # type: ignore[var-annotated]
        'models.Tournament', related_name='discord_role_mappings',
        null=True, on_delete=fields.CASCADE,
    )
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        # Two keys, one per shape. Postgres treats NULLs as distinct, so each
        # constraint only bites on the rows whose columns are all populated —
        # the role key ignores tournament rows and vice versa.
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
    # Captured at subscribe time so the settings UI can label the device.
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
