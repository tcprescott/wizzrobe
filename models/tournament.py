from typing import Iterable

from tortoise import fields
from tortoise.models import Model

from .enums import MatchNotificationLevel, ProviderTaskStatus


class Tournament(Model):
    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='tournaments', on_delete=fields.CASCADE)
    # Keep nullable: not every write path stamps it yet.
    event: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Event', related_name='tournaments', on_delete=fields.CASCADE,
    )
    event_id: int
    name = fields.CharField(max_length=255)
    short_name = fields.CharField(max_length=40, null=True)
    description = fields.TextField(null=True)
    seed_generator = fields.CharField(max_length=255, null=True)
    is_active = fields.BooleanField(default=True)
    is_test = fields.BooleanField(default=False)
    uses_tournament_room = fields.BooleanField(default=True)
    players_per_match = fields.IntField(default=2)
    bracket_url = fields.CharField(max_length=255, null=True)
    rules_url = fields.CharField(max_length=255, null=True)
    tournament_format = fields.CharField(max_length=255, null=True)
    triforce_access_message = fields.TextField(null=True)
    randomizer_notes = fields.TextField(null=True)
    average_match_duration = fields.IntField(null=True)  # in minutes
    max_match_duration = fields.IntField(null=True)  # in minutes
    challonge_tournament_id = fields.CharField(max_length=64, null=True)
    challonge_community = fields.CharField(max_length=64, null=True)
    challonge_tournament_url = fields.CharField(max_length=255, null=True)
    challonge_last_synced_at = fields.DatetimeField(null=True)
    challonge_state = fields.CharField(max_length=32, null=True)
    challonge_group_stage = fields.BooleanField(default=False)
    challonge_tournament_type = fields.CharField(max_length=32, null=True)
    startgg_event_id = fields.CharField(max_length=64, null=True)
    startgg_event_slug = fields.CharField(max_length=255, null=True)
    startgg_event_name = fields.CharField(max_length=255, null=True)
    startgg_last_synced_at = fields.DatetimeField(null=True)
    startgg_bookable_phase_id = fields.CharField(max_length=64, null=True)
    startgg_report_results = fields.BooleanField(default=False)
    config = fields.JSONField(null=True)
    preset = fields.ForeignKeyField(
        'models.Preset', related_name='tournaments', null=True, on_delete=fields.SET_NULL
    )
    # Keep the annotation: an untyped new field adds a mypy-ratchet error.
    hard_preset: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.Preset', related_name='hard_tournaments', null=True, on_delete=fields.SET_NULL
    )
    racetime_bot = fields.ForeignKeyField(
        'models.RacetimeBot', related_name='tournaments', null=True, on_delete=fields.SET_NULL
    )
    race_room_profile = fields.ForeignKeyField(
        'models.RaceRoomProfile', related_name='tournaments', null=True, on_delete=fields.SET_NULL
    )
    racetime_auto_create_rooms = fields.BooleanField(default=False)
    room_open_minutes_before = fields.IntField(default=30)
    require_racetime_link = fields.BooleanField(default=False)
    racetime_bot_runs_room = fields.BooleanField(default=False)
    racetime_seed_post_minutes = fields.IntField(default=10)
    racetime_close_finish_ms = fields.IntField(default=1000)
    stage_reminder_minutes = fields.IntField(default=30)
    racetime_default_goal = fields.CharField(max_length=255, null=True)
    discord_events_enabled = fields.BooleanField(default=False)
    discord_event_duration_minutes = fields.IntField(default=60)
    discord_event_title_template = fields.CharField(max_length=255, null=True)
    discord_event_description_template = fields.TextField(null=True)
    event_start_date = fields.DateField(null=True)
    event_end_date = fields.DateField(null=True)
    tournament_hours = fields.JSONField(null=True)
    signups_open_at = fields.DatetimeField(null=True)
    signups_close_at = fields.DatetimeField(null=True)
    prize_pool = fields.DecimalField(max_digits=10, decimal_places=2, null=True)
    prize_bonus = fields.DecimalField(max_digits=10, decimal_places=2, null=True)
    sglive_slug = fields.CharField(max_length=64, null=True)
    prize_pool_synced_at = fields.DatetimeField(null=True)
    admins = fields.ManyToManyField('models.User', related_name='admin_tournaments', through='TournamentAdmins')
    crew_coordinators = fields.ManyToManyField(
        'models.User',
        related_name='crew_coordinated_tournaments',
        through='TournamentCrewCoordinators',
    )
    required_commentators = fields.IntField(default=1)
    required_trackers = fields.IntField(default=1)
    required_broadcasters = fields.IntField(default=1)
    allow_player_match_requests = fields.BooleanField(default=True)
    allow_reschedule_requests = fields.BooleanField(default=True)
    players_release_seed = fields.BooleanField(default=False)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    players = fields.ReverseRelation["TournamentPlayers"]
    matches = fields.ReverseRelation["Match"]
    notification_preferences = fields.ReverseRelation["TournamentNotificationPreference"]
    triforce_texts = fields.ReverseRelation["TriforceText"]
    challonge_participants = fields.ReverseRelation["ChallongeParticipant"]
    challonge_matches = fields.ReverseRelation["ChallongeMatch"]

    @property
    def short_label(self) -> str:
        return (self.short_name or '').strip() or self.name

    @staticmethod
    def short_label_options(tournaments: Iterable['Tournament']) -> dict[int, str]:
        """``{id: short_label}`` for a filter dropdown, ordered by what it shows."""
        return {
            t.id: t.short_label
            for t in sorted(tournaments, key=lambda t: t.short_label.casefold())
        }

    @property
    def is_racetime_enabled(self) -> bool:
        """True when this tournament is wired to a racetime.gg category.

        A racetime tournament runs online — the race room drives the match
        lifecycle — so on-site-only actions (check-in/seating, station
        assignment) are disabled for it. ``racetime_bot`` is the FK that names
        the category; its ``racetime_bot_id`` is always loaded with the row, so
        this never triggers a query.
        """
        return self.racetime_bot_id is not None


class TournamentPlayers(Model):
    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='tournament_players', on_delete=fields.CASCADE)
    tournament = fields.ForeignKeyField('models.Tournament', related_name='players')
    user = fields.ForeignKeyField('models.User', related_name='tournament_players')
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        unique_together = (('tournament', 'user'),)
        table = 'tournamentplayers'
        indexes = (('user',),)


class TournamentPayout(Model):
    """One placement's share of a tournament's prize pool.

    A percentage row is a *share*, not a payment: the money is computed as
    ``(prize_pool + prize_bonus) * percentage`` at read time, so it follows the
    pool as it moves through the event. A fixed row stores ``amount`` instead,
    for the organizer who announced dollars, which no two-decimal percentage
    can reproduce to the cent.

    ``place`` is not unique. Joint placings are the normal case — SGL's ALTTPR
    2025 paid two third places at 10% each — and splitting a single 20% third
    place in half would hide the arithmetic from the admin checking it.
    """

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField(  # type: ignore[var-annotated]
        'models.Tenant', related_name='payouts', on_delete=fields.CASCADE
    )
    tournament = fields.ForeignKeyField(  # type: ignore[var-annotated]
        'models.Tournament', related_name='payouts', on_delete=fields.CASCADE
    )
    place = fields.IntField()
    percentage = fields.DecimalField(max_digits=5, decimal_places=2, null=True)
    amount = fields.DecimalField(max_digits=10, decimal_places=2, null=True)
    entrant = fields.ForeignKeyField(  # type: ignore[var-annotated]
        'models.User', related_name='payouts', null=True, on_delete=fields.SET_NULL
    )
    note = fields.CharField(max_length=255, null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'tournamentpayout'
        # entrant is in the key so ties are legal; don't narrow it to (tournament, place).
        unique_together = (('tournament', 'place', 'entrant'),)
        indexes = (('tournament',),)


class TournamentNotificationPreference(Model):
    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='tournament_notification_preferences', on_delete=fields.CASCADE)
    user = fields.ForeignKeyField('models.User', related_name='tournament_notifications')
    tournament = fields.ForeignKeyField('models.Tournament', related_name='notification_preferences')
    match_notifications = fields.CharEnumField(MatchNotificationLevel, default=MatchNotificationLevel.NONE, max_length=30)
    staff_alerts = fields.BooleanField(default=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'tournament')
        table = 'tournamentnotificationpreference'
        indexes = (('tournament',),)


class GeneratedSeeds(Model):
    """One rolled seed, with the provenance a disputed result asks for.

    ``seed_url`` alone cannot answer "what settings produced this?" — a
    :class:`Preset` is an editable row, so editing one silently rewrites the
    apparent history of every seed rolled from it. ``settings_snapshot`` is
    therefore the resolved payload **as sent upstream**, captured at roll time and
    never updated; ``preset`` records which preset was selected, and the two
    together survive any later edit. ``provider_meta`` carries what the roll cost
    (attempts, latency, surface) from the seed-provider envelope.

    No credential is ever part of the snapshot: randomizer keys are resolved
    separately and never merged into a settings payload.
    """

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='generated_seeds', on_delete=fields.CASCADE)
    seed_url = fields.CharField(max_length=255)
    seed_info = fields.TextField(null=True)
    randomizer = fields.CharField(max_length=32, null=True)
    preset = fields.ForeignKeyField(  # type: ignore[var-annotated]
        'models.Preset', related_name='generated_seeds', null=True, on_delete=fields.SET_NULL
    )
    settings_snapshot = fields.JSONField(null=True)  # type: ignore[var-annotated]
    rolled_by = fields.ForeignKeyField(  # type: ignore[var-annotated]
        'models.User', related_name='rolled_seeds', null=True, on_delete=fields.SET_NULL
    )
    provider_meta = fields.JSONField(null=True)  # type: ignore[var-annotated]
    seed_code = fields.JSONField(null=True)  # type: ignore[var-annotated]
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)


class ProviderTask(Model):
    """One long-running call to an upstream provider, tracked across restarts.

    Most provider work is a request: ask, wait a second, get an answer. The DK64
    randomizer is not — it is a task queue, and a roll takes two to three
    minutes. Holding that upstream task id in a local variable for the duration
    means a restart orphans the work: the seed exists upstream, but nothing can
    ever claim it, so the match stays unseeded, no DM goes out, and there is no
    record the roll happened. This row is the fix. Once the id is persisted,
    resuming after a restart is not a special path — it is the normal one, since
    the poller only ever reads its work from here.

    Deliberately named for the *provider*, not for seeds: ``provider`` and
    ``operation`` mirror ``application.utils.seed_provider.ProviderCall``, so a
    later long-running call that is not a seed roll needs no new table. The
    poller dispatches on ``operation``; today the only value is
    ``generate_seed``.

    **Consumers are explicit nullable FKs, not a generic type/id pair.** Tortoise
    has no generic relation, and the generic version loses the cascade that
    matters here: delete a match and a dangling task would keep polling for a
    seed nobody can receive.

    ``settings_snapshot`` is captured at submit time for the same reason
    :class:`GeneratedSeeds` captures one — a completion that happens minutes
    later, possibly in a different process, must record what was *sent*, not
    re-resolve a ``Preset`` that may have been edited in between.
    """

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField(  # type: ignore[var-annotated]
        'models.Tenant', related_name='provider_tasks', on_delete=fields.CASCADE,
    )
    provider = fields.CharField(max_length=32)
    operation = fields.CharField(max_length=32)
    status = fields.CharEnumField(ProviderTaskStatus, max_length=16, default=ProviderTaskStatus.QUEUED)
    # Null means the submit died before upstream answered; the poller abandons it.
    provider_task_id = fields.CharField(max_length=128, null=True)
    provider_params = fields.JSONField(null=True)  # type: ignore[var-annotated]
    settings_snapshot = fields.JSONField(null=True)  # type: ignore[var-annotated]
    preset = fields.ForeignKeyField(  # type: ignore[var-annotated]
        'models.Preset', related_name='provider_tasks', null=True, on_delete=fields.SET_NULL
    )
    requested_by = fields.ForeignKeyField(  # type: ignore[var-annotated]
        'models.User', related_name='provider_tasks', null=True, on_delete=fields.SET_NULL
    )
    match = fields.ForeignKeyField(  # type: ignore[var-annotated]
        'models.Match', related_name='provider_tasks', null=True, on_delete=fields.CASCADE
    )
    error = fields.TextField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)
    completed_at = fields.DatetimeField(null=True)

    class Meta:
        table = 'provider_task'
        indexes = (('status', 'provider'),)

    @property
    def is_terminal(self) -> bool:
        return self.status in ProviderTaskStatus.terminal()


class Preset(Model):
    """A tenant-authored seed-rolling preset: a named randomizer settings blob.

    Replaces the hard-coded ``presets/*`` files as the source of seed settings —
    seed generation resolves a ``Preset`` (its ``randomizer`` + ``settings``)
    rather than opening a path. The built-in files can be imported as starting
    rows (see ``PresetService.import_builtins``). ``settings`` is the raw payload
    handed to the randomizer backend (for ALTTPR, the customizer settings dict).
    """
    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='presets', on_delete=fields.CASCADE)
    name = fields.CharField(max_length=255)
    randomizer = fields.CharField(max_length=32)
    settings = fields.JSONField()
    description = fields.TextField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    tournaments = fields.ReverseRelation["Tournament"]

    class Meta:
        table = 'preset'
        unique_together = (('tenant', 'randomizer', 'name'),)


class RandomizerCredential(Model):
    """A community's own credential for one randomizer's key-gated upstream API.

    Randomizer credentials are per tenant, not per deployment: each community
    supplies the key it was issued and is bound by its terms, rather than every
    community rolling on the operator's. Which credentials exist is declared in
    ``application/randomizer_credentials.py``; ``(randomizer, key)`` points at a
    ``CredentialSpec`` there.

    ``value`` is stored in plaintext (like ``RacetimeBot.client_secret`` and
    ``Webhook.secret``) and is **privileged**: ``RandomizerCredentialService``
    never returns it to a caller other than seed generation, and never writes it
    into an audit entry.
    """
    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField(
        'models.Tenant', related_name='randomizer_credentials', on_delete=fields.CASCADE
    )
    randomizer = fields.CharField(max_length=32)
    key = fields.CharField(max_length=64)
    value = fields.TextField()
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'randomizercredential'
        unique_together = (('tenant', 'randomizer', 'key'),)
        indexes = (('tenant',),)


class TriforceText(Model):
    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='triforce_texts', on_delete=fields.CASCADE)
    tournament = fields.ForeignKeyField(
        'models.Tournament', related_name='triforce_texts', on_delete=fields.CASCADE
    )
    user = fields.ForeignKeyField(
        'models.User', related_name='triforce_texts',
        null=True, on_delete=fields.SET_NULL,
    )
    text = fields.CharField(max_length=200)
    author = fields.CharField(max_length=200, null=True)
    approved = fields.BooleanField(null=True)
    approved_by = fields.ForeignKeyField(
        'models.User', related_name='triforce_texts_moderated',
        null=True, on_delete=fields.SET_NULL,
    )
    approved_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'triforcetext'
        indexes = (('tournament', 'user'),)
