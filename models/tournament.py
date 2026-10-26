from typing import Iterable

from tortoise import fields
from tortoise.models import Model

from .enums import MatchNotificationLevel, ProviderTaskStatus


class Tournament(Model):
    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='tournaments', on_delete=fields.CASCADE)
    # Nullable until every write stamps it; the events migration backfilled the
    # rows that existed before (see docs/features/events.md).
    event: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Event', related_name='tournaments', on_delete=fields.CASCADE,
    )
    event_id: int
    name = fields.CharField(max_length=255)
    # Where a full name will not fit (the timeline, run sheets). Blank means
    # use ``name``; read it through ``short_label``.
    short_name = fields.CharField(max_length=40, null=True)
    description = fields.TextField(null=True)
    seed_generator = fields.CharField(max_length=255, null=True)
    is_active = fields.BooleanField(default=True)
    # A staff-only rehearsal (e.g. a Challonge integration check): left out of
    # the event handbook's tournament list, which is written for attendees.
    is_test = fields.BooleanField(default=False)
    # Off means its matches are played somewhere other than the tournament
    # room, so the ones not on a stage take no room capacity (forecast, room
    # cap, suggested times). A stage match counts as on-stream either way.
    uses_tournament_room = fields.BooleanField(default=True)
    players_per_match = fields.IntField(default=2)
    bracket_url = fields.CharField(max_length=255, null=True)
    rules_url = fields.CharField(max_length=255, null=True)
    tournament_format = fields.CharField(max_length=255, null=True)
    triforce_access_message = fields.TextField(null=True)
    # The randomizer settings, preset and build a tournament races on, as the
    # same safe-markdown subset the description renders through.
    randomizer_notes = fields.TextField(null=True)
    average_match_duration = fields.IntField(null=True)  # in minutes
    max_match_duration = fields.IntField(null=True)  # in minutes
    challonge_tournament_id = fields.CharField(max_length=64, null=True)
    # Organization subdomain (``<community>.challonge.com``); v2.1 scopes those
    # tournaments under ``/communities/<community>/``.
    challonge_community = fields.CharField(max_length=64, null=True)
    challonge_tournament_url = fields.CharField(max_length=255, null=True)
    challonge_last_synced_at = fields.DatetimeField(null=True)
    # The linked bracket's state as of the last sync (``pending``,
    # ``group_stages_underway``, ``underway``, ``awaiting_review``, ...), cached
    # so the admin page can offer the next stage action without an API call.
    challonge_state = fields.CharField(max_length=32, null=True)
    challonge_group_stage = fields.BooleanField(default=False)
    # Challonge's own format name ("single elimination", "double elimination", …)
    # for the elimination stage, so its round schedule can offer the right rows
    # before Challonge has created any elimination matches.
    challonge_tournament_type = fields.CharField(max_length=32, null=True)
    # A linked start.gg event, read only: its organisers record results on
    # start.gg and the sync mirrors them in. ``startgg_event_slug`` is the
    # ``tournament/<t>/event/<e>`` path the API looks events up by.
    startgg_event_id = fields.CharField(max_length=64, null=True)
    startgg_event_slug = fields.CharField(max_length=255, null=True)
    startgg_event_name = fields.CharField(max_length=255, null=True)
    startgg_last_synced_at = fields.DatetimeField(null=True)
    # The phase whose sets players book (and get "matchup ready" DMs for).
    # Null means the event's last phase, usually the on-site bracket.
    startgg_bookable_phase_id = fields.CharField(max_length=64, null=True)
    # Opt-in writeback: report a confirmed Wizzrobe result to start.gg. Never
    # overwrites a set start.gg already has a result for.
    startgg_report_results = fields.BooleanField(default=False)
    # Hybrid config substrate (see docs/online-tournaments): worker-queried knobs
    # stay typed columns; templates, scoring params, and strategy choices live in
    # this schema-validated JSON blob. Written only through the service layer,
    # which validates it with ``validate_tournament_config`` (unknown keys raise
    # ``ValueError``). ``null`` until a tournament opts into online behavior.
    config = fields.JSONField(null=True)
    # Seed-rolling preset (PR 1+). Coexists with the legacy ``seed_generator``
    # string: when this FK is set it wins (seed generation resolves the preset's
    # randomizer + settings); otherwise ``seed_generator`` still drives the
    # hard-coded path. SET_NULL so deleting a preset detaches its tournaments
    # rather than cascade-deleting them.
    preset = fields.ForeignKeyField(
        'models.Preset', related_name='tournaments', null=True, on_delete=fields.SET_NULL
    )
    # The opt-in "harder settings" preset. Null means the tournament does not
    # offer one, which is what makes the whole opt-in feature dormant: with no
    # hard preset there is nothing to opt into and the control never renders.
    # Rolled only when every player in a match opted in (or staff overrode the
    # match to ``PresetOverride.HARD``) — see ``MatchHardPresetOptIn``.
    # ``TournamentService`` requires it to share ``preset``'s randomizer, since
    # seed generation resolves the randomizer from whichever preset wins.
    # Annotated where its siblings are not: the ORM builds this descriptor at
    # runtime, so an untyped new field would add an error to the mypy ratchet.
    hard_preset: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.Preset', related_name='hard_tournaments', null=True, on_delete=fields.SET_NULL
    )
    # Racetime room automation (PR 3+). ``racetime_bot`` must be a category the
    # tenant is authorized for (enforced in the service); SET_NULL so revoking a
    # bot detaches its tournaments rather than deleting them. ``race_room_profile``
    # supplies reusable room settings. The remaining knobs are worker-queried, so
    # they are typed columns rather than living in ``config``.
    racetime_bot = fields.ForeignKeyField(
        'models.RacetimeBot', related_name='tournaments', null=True, on_delete=fields.SET_NULL
    )
    race_room_profile = fields.ForeignKeyField(
        'models.RaceRoomProfile', related_name='tournaments', null=True, on_delete=fields.SET_NULL
    )
    racetime_auto_create_rooms = fields.BooleanField(default=False)
    room_open_minutes_before = fields.IntField(default=30)
    require_racetime_link = fields.BooleanField(default=False)
    # How long before a scheduled match to remind its players and approved crew
    # which stage they are on. Deliberately separate from
    # ``room_open_minutes_before`` above, which governs racetime room creation:
    # the two answer different questions and a community will want different
    # numbers for them. Zero disables the reminder.
    stage_reminder_minutes = fields.IntField(default=30)
    racetime_default_goal = fields.CharField(max_length=255, null=True)
    # Discord Scheduled Events mirror (PR 8). Per-tournament opt-in: when enabled,
    # the reconciler worker mirrors this tournament's scheduled matches into the
    # tenant guild's Discord Scheduled Events. ``discord_event_duration_minutes``
    # sets each external event's end time; the templates (nullable — a built-in
    # default is used when unset) render the event title/description from match
    # data (``{tournament}`` / ``{match}`` / ``{players}`` placeholders).
    discord_events_enabled = fields.BooleanField(default=False)
    discord_event_duration_minutes = fields.IntField(default=60)
    discord_event_title_template = fields.CharField(max_length=255, null=True)
    discord_event_description_template = fields.TextField(null=True)
    # Per-tournament "tournament days" override (see docs/timezone-handling and
    # SystemConfigService). Each is nullable and falls back to the tenant-wide
    # setting when unset: ``event_start_date`` / ``event_end_date`` override the
    # event date window, and ``tournament_hours`` overrides the per-date open/close
    # windows — same JSON shape as the tenant's ``tournament_hours_by_date`` blob,
    # ``{"YYYY-MM-DD": {"open": "HH:MM", "close": "HH:MM"}}``. Resolved at the
    # scheduling/suggestion use-sites via ``SystemConfigService`` (tenant default
    # takes over whenever the tournament leaves a field unset).
    event_start_date = fields.DateField(null=True)
    event_end_date = fields.DateField(null=True)
    tournament_hours = fields.JSONField(null=True)
    # The player signup window, read by the Tournaments tab and enforced by
    # ``TournamentService.self_enroll`` / ``self_withdraw``. Both nullable and
    # deliberately permissive: an unset open date means signups are open now, an
    # unset close date means they stay open, so every tournament that predates
    # these columns keeps behaving exactly as it did. Distinct from
    # ``event_start_date`` / ``event_end_date`` above, which bound *match
    # scheduling*, not registration. Staff enrolment ignores the window
    # entirely — a closed window stops players signing themselves up, not staff
    # fixing a roster.
    signups_open_at = fields.DatetimeField(null=True)
    signups_close_at = fields.DatetimeField(null=True)
    # The advertised pool and the leaderboard bonus. Percentages apply to their
    # SUM: SGL's ALTTPR 2025 paid 50% of a $1000 pool plus a $100 bonus as $550.
    # Both nullable — a tournament with no prize money leaves them unset rather
    # than storing a zero that reads as "decided, and it's nothing".
    prize_pool = fields.DecimalField(max_digits=10, decimal_places=2, null=True)
    prize_bonus = fields.DecimalField(max_digits=10, decimal_places=2, null=True)
    # The game's slug on sglive.speedgaming.org (``wwr`` for /wwr/). While set,
    # the sglive pool worker owns ``prize_pool`` and staff can't type over it;
    # ``prize_pool_synced_at`` is when the worker last read a value for it.
    sglive_slug = fields.CharField(max_length=64, null=True)
    prize_pool_synced_at = fields.DatetimeField(null=True)
    admins = fields.ManyToManyField('models.User', related_name='admin_tournaments', through='TournamentAdmins')
    crew_coordinators = fields.ManyToManyField(
        'models.User',
        related_name='crew_coordinated_tournaments',
        through='TournamentCrewCoordinators',
    )
    # How much approved crew a *stream candidate* needs before the coverage
    # reports call it covered. Not every tournament runs the same crew shape —
    # plenty stream with commentary and no tracker — and without this the
    # reports assumed one of each and showed those tournaments a permanent
    # false gap and a depressed health score. ``0`` means the role is not used
    # here; the defaults reproduce the old assumption for existing rows.
    #
    # Read by ``ReportsService.crew_coverage`` and
    # ``AnalyticsService.tournament_health`` (through
    # ``reporting_shared.is_crew_covered``), and by the schedule board, which
    # hides Sign up for a role requiring ``0`` — ``CrewService.signup_crew``
    # refuses it too, since hiding a control is not enforcing a rule. Withdrawing
    # stays open at any requirement, so a signup made before the setting changed
    # can still be removed.
    required_commentators = fields.IntField(default=1)
    required_trackers = fields.IntField(default=1)
    # Only counts where the tournament's event has broadcasters switched on
    # (Event settings → Crew); with it off every reader takes it as 0.
    required_broadcasters = fields.IntField(default=1)
    # Whether players may schedule matches themselves outside a bracket
    # (``MatchService.submit_match_request``). Turned off automatically when a
    # native bracket is created or a Challonge bracket is linked: a bracket-run
    # tournament schedules only the matchups the bracket produced. Staff can turn
    # it back on per tournament.
    allow_player_match_requests = fields.BooleanField(default=True)
    # Whether a player may ask staff to move or call off one of their matches
    # (``MatchRescheduleService``). Independent of the toggle above: a
    # bracket-run tournament schedules only what the bracket produced, but the
    # players in it still need a way to say "we can't make Saturday" without
    # finding a staff member. Off means the control is hidden *and* the service
    # refuses, since the REST route reaches past the hidden control.
    allow_reschedule_requests = fields.BooleanField(default=True)
    # Players release their own seed: once a match is checked in, each player
    # taps Ready for seed and the seed rolls, and goes to them, when the last one
    # does (``SeedReadyService``). Proctors and staff can still roll it any time.
    players_release_seed = fields.BooleanField(default=False)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    # related fields
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
        indexes = (('user',),)  # composite is tournament-first; user-only reverse lookup uncovered


class TournamentPayout(Model):
    """One placement's share of a tournament's prize pool.

    A row is a *share*, not a payment: ``place`` and ``percentage`` are stored
    and the money is computed as ``(prize_pool + prize_bonus) * percentage`` at
    read time. Storing the amount would be a second source of truth that drifts
    the moment the pool moves, which it does throughout the event.

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
    percentage = fields.DecimalField(max_digits=5, decimal_places=2)
    # Null while the split is drafted before the bracket finishes. SET_NULL so
    # retiring a user leaves the historical split intact and legible.
    entrant = fields.ForeignKeyField(  # type: ignore[var-annotated]
        'models.User', related_name='payouts', null=True, on_delete=fields.SET_NULL
    )
    note = fields.CharField(max_length=255, null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'tournamentpayout'
        # (tournament, place, entrant) rather than (tournament, place), so ties
        # are legal. Postgres treats NULLs as distinct, so several unfilled rows
        # can sit at place 3 while the split is drafted, and once both are named
        # the constraint stops one person being paid twice at one place.
        # ``tournament`` already implies the tenant, so no tenant column is
        # needed for the key to be tenant-safe.
        unique_together = (('tournament', 'place', 'entrant'),)
        indexes = (('tournament',),)


class TournamentNotificationPreference(Model):
    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='tournament_notification_preferences', on_delete=fields.CASCADE)
    user = fields.ForeignKeyField('models.User', related_name='tournament_notifications')
    tournament = fields.ForeignKeyField('models.Tournament', related_name='notification_preferences')
    match_notifications = fields.CharEnumField(MatchNotificationLevel, default=MatchNotificationLevel.NONE, max_length=30)
    # A tournament admin's opt-out from its match alerts (player comments,
    # reschedule requests). No row means on.
    staff_alerts = fields.BooleanField(default=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'tournament')
        table = 'tournamentnotificationpreference'
        indexes = (('tournament',),)  # composite is user-first; tournament-only fan-out uncovered


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
    # Which backend actually rolled it — ``Tournament.seed_generator`` and the
    # preset FK can both change afterwards, so the name is stamped here.
    randomizer = fields.CharField(max_length=32, null=True)
    preset = fields.ForeignKeyField(  # type: ignore[var-annotated]
        'models.Preset', related_name='generated_seeds', null=True, on_delete=fields.SET_NULL
    )
    settings_snapshot = fields.JSONField(null=True)  # type: ignore[var-annotated]
    # SET_NULL: deleting the roller must not delete the seed record.
    rolled_by = fields.ForeignKeyField(  # type: ignore[var-annotated]
        'models.User', related_name='rolled_seeds', null=True, on_delete=fields.SET_NULL
    )
    provider_meta = fields.JSONField(null=True)  # type: ignore[var-annotated]
    # The file-select code players compare before a race (e.g. ALTTPR's five
    # items), as a list of names; null when the randomizer doesn't return one.
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
    # Mirrors ProviderCall.provider / .operation ('dk64r' / 'generate_seed').
    provider = fields.CharField(max_length=32)
    operation = fields.CharField(max_length=32)
    status = fields.CharEnumField(ProviderTaskStatus, max_length=16, default=ProviderTaskStatus.QUEUED)
    # The upstream handle. Null between row creation and a successful submit —
    # a row in that window is a submit that died, which the poller abandons.
    provider_task_id = fields.CharField(max_length=128, null=True)
    # Whatever a *later* poll needs to address the task, e.g. {'branch': 'stable'}.
    provider_params = fields.JSONField(null=True)  # type: ignore[var-annotated]
    settings_snapshot = fields.JSONField(null=True)  # type: ignore[var-annotated]
    preset = fields.ForeignKeyField(  # type: ignore[var-annotated]
        'models.Preset', related_name='provider_tasks', null=True, on_delete=fields.SET_NULL
    )
    # SET_NULL, like GeneratedSeeds.rolled_by: deleting the requester must not
    # delete the record of the work. Carried so a completion running in the
    # worker can attribute the resulting seed to whoever asked for it.
    requested_by = fields.ForeignKeyField(  # type: ignore[var-annotated]
        'models.User', related_name='provider_tasks', null=True, on_delete=fields.SET_NULL
    )
    match = fields.ForeignKeyField(  # type: ignore[var-annotated]
        'models.Match', related_name='provider_tasks', null=True, on_delete=fields.CASCADE
    )
    # Upstream's own words when it failed, shown to the admin who asked. Null on
    # every non-failed row.
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

    # related fields
    tournaments = fields.ReverseRelation["Tournament"]

    class Meta:
        table = 'preset'
        # Formerly-global preset names are namespaced per tenant; a preset is
        # uniquely a (randomizer, name) within its tenant.
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
        # tournament_id prefix serves list_by_tournament(+approved); full key
        # serves list_by_tournament_and_user / list_approved_by_user.
        indexes = (('tournament', 'user'),)
