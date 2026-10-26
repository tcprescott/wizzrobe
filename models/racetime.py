from tortoise import fields
from tortoise.models import Model

from .enums import BotStatus, RaceRoomHoldReason, RaceRoomStatus


class RacetimeBot(Model):
    """A shared, platform-managed racetime.gg bot for one game category.

    **Global** (no ``tenant`` FK) like the Discord token and VAPID keys: one bot
    per racetime category, holding that category's OAuth client credentials.
    SUPER_ADMIN authorizes tenants to use it through :class:`RacetimeBotTenant`.
    ``client_secret`` is a privileged secret — never surfaced to a tenant-facing
    response or logged. The websocket connection and the health-field *writes*
    land in PR 4; here the record and its admin surface exist.
    """

    id = fields.IntField(pk=True)
    category = fields.CharField(max_length=64, unique=True)
    client_id = fields.CharField(max_length=255)
    client_secret = fields.CharField(max_length=255)
    name = fields.CharField(max_length=255)
    description = fields.TextField(null=True)
    is_active = fields.BooleanField(default=True)
    handler_class = fields.CharField(max_length=255, null=True)
    # Health fields — written by the PR 4 runtime, read by the platform health
    # page. Default to UNKNOWN until a live connection reports otherwise.
    status = fields.CharEnumField(BotStatus, default=BotStatus.UNKNOWN, max_length=20)
    status_message = fields.TextField(null=True)
    last_connected_at = fields.DatetimeField(null=True)
    last_checked_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    # related fields
    tenant_grants = fields.ReverseRelation["RacetimeBotTenant"]
    rooms = fields.ReverseRelation["RacetimeRoom"]
    tournaments = fields.ReverseRelation["Tournament"]

    class Meta:
        table = 'racetimebot'


class RacetimeBotTenant(Model):
    """SUPER_ADMIN authorization grant: a :class:`RacetimeBot` usable by a tenant.

    **Many-to-many** — a tenant may hold several categories; a category serves
    many tenants. Created on ``/platform`` with explicit ids (no ambient tenant
    scope); ``is_active`` lets a grant be suspended without deleting it.
    """

    id = fields.IntField(pk=True)
    bot = fields.ForeignKeyField('models.RacetimeBot', related_name='tenant_grants', on_delete=fields.CASCADE)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='racetime_bot_grants', on_delete=fields.CASCADE)
    is_active = fields.BooleanField(default=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'racetimebottenant'
        unique_together = (('bot', 'tenant'),)


class RaceRoomProfile(Model):
    """Reusable racetime room settings a tournament can point at.

    Tenant-scoped; managed by ``SYNC_ADMIN``. These values become the racetime
    ``startrace`` parameters when the PR 4/6 room-creation flow opens a room, so
    a community defines its house rules once and reuses them across tournaments.
    """

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='race_room_profiles', on_delete=fields.CASCADE)
    name = fields.CharField(max_length=255)
    goal = fields.CharField(max_length=255, null=True)
    invitational = fields.BooleanField(default=False)
    unlisted = fields.BooleanField(default=False)
    auto_start = fields.BooleanField(default=True)
    allow_comments = fields.BooleanField(default=True)
    allow_midrace_chat = fields.BooleanField(default=True)
    allow_non_entrant_chat = fields.BooleanField(default=True)
    chat_message_delay = fields.IntField(default=0)  # seconds
    start_delay = fields.IntField(default=15)  # seconds before an auto-started race begins
    time_limit = fields.IntField(default=24)  # hours before the room auto-closes
    streaming_required = fields.BooleanField(default=False)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    # related fields
    tournaments = fields.ReverseRelation["Tournament"]

    class Meta:
        table = 'raceroomprofile'
        unique_together = (('tenant', 'name'),)


class RacetimeRoom(Model):
    """A racetime.gg race room record — its own model, not a slug on ``Match``.

    ``slug`` is **globally unique + indexed**: inbound racetime events carry only
    the slug (no tenant), so the reverse lookup is deliberately *unscoped* for
    tenant routing (see :class:`RacetimeRoomRepository.get_by_slug`, mirroring the
    ``ApiToken``→tenant pattern). Room creation and status *writes* land in
    PR 4/6; this PR defines the record.
    """

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='racetime_rooms', on_delete=fields.CASCADE)
    # SET_NULL: removing a bot must not erase the history of rooms it opened.
    bot = fields.ForeignKeyField(
        'models.RacetimeBot', related_name='rooms', null=True, on_delete=fields.SET_NULL
    )
    slug = fields.CharField(max_length=255, unique=True, index=True)
    category = fields.CharField(max_length=64)
    room_name = fields.CharField(max_length=255, null=True)
    status = fields.CharEnumField(RaceRoomStatus, default=RaceRoomStatus.OPEN, max_length=20)
    # SET_NULL: deleting a match detaches its room record rather than dropping it.
    match = fields.OneToOneField(
        'models.Match', related_name='racetime_room', null=True, on_delete=fields.SET_NULL
    )
    opened_at = fields.DatetimeField(null=True)
    # What the bot has done in a room it runs (``Tournament.racetime_bot_runs_room``).
    # Stamped so a reconnect, a restart or a second trigger never says it twice.
    greeted_at = fields.DatetimeField(null=True)
    seed_posted_at = fields.DatetimeField(null=True)
    # Each runner's !fpa: ``{racetime_user_id, name, at, race_seconds, note}``.
    fpa_calls = fields.JSONField(default=list)
    # A finished race waiting on staff: why, the line staff read, and racetime's
    # entrants as they finished, so Check result starts from what racetime said.
    held_reason = fields.CharEnumField(RaceRoomHoldReason, max_length=20, null=True)
    held_detail = fields.TextField(null=True)
    held_entrants = fields.JSONField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'racetimeroom'
        indexes = (('match',),)

    @property
    def url(self) -> str:
        """Where a human opens this room. Derived, not stored.

        The slug is the room's address on racetime.gg, and every surface that
        shows one wants it clickable — the match dialog printed the bare slug as
        dead text for a while precisely because building the URL was left to
        each caller.
        """
        return f'https://racetime.gg/{self.slug}' if self.slug else ''
