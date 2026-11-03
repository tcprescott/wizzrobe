
from tortoise import fields
from tortoise.models import Model

from .enums import (
    AsyncQualifierLiveRaceStatus,
    AsyncQualifierReviewStatus,
    AsyncQualifierRunStatus,
)


class AsyncQualifier(Model):
    """A self-paced permalink-pool qualifier — a peer aggregate of ``Tournament``.

    Created/administered like a tournament (per-qualifier ``admins`` M2M, an admin
    tab, ``is_active``) but a **distinct state machine** entirely outside the
    Match/schedule system: window opens → players draw permalinks from pools →
    runs (in-progress → finished/forfeit) → review (pending → approved/rejected) →
    scored leaderboard → window closes.

    Tenant-scoped. Typed **window columns** (``opens_at``/``closes_at``) plus
    ``runs_per_pool`` / ``allowed_reattempts`` are worker-/query-facing knobs; the
    validated-JSON ``config`` blob carries scoring/reattempt/messaging strategy
    (the hybrid config decision). ``admins`` is the reviewer set (self-review is
    blocked in the service).
    """

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='async_qualifiers', on_delete=fields.CASCADE)
    # Keep nullable: not every write path stamps it yet.
    event: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Event', related_name='async_qualifiers', on_delete=fields.CASCADE,
    )
    event_id: int
    name = fields.CharField(max_length=255)
    description = fields.TextField(null=True)
    event_name = fields.CharField(max_length=255, null=True)
    opens_at = fields.DatetimeField(null=True)
    closes_at = fields.DatetimeField(null=True)
    runs_per_pool = fields.IntField(default=1)
    allowed_reattempts = fields.IntField(default=0)
    config = fields.JSONField(null=True)
    is_active = fields.BooleanField(default=True)
    review_backlog_notified_at = fields.DatetimeField(null=True)
    window_state_notified = fields.CharField(max_length=16, null=True)
    admins = fields.ManyToManyField(
        'models.User', related_name='admin_async_qualifiers', through='AsyncQualifierAdmins'
    )
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    pools = fields.ReverseRelation["AsyncQualifierPool"]
    runs = fields.ReverseRelation["AsyncQualifierRun"]

    class Meta:
        table = 'asyncqualifier'
        indexes = (('tenant',),)


class AsyncQualifierPool(Model):
    """A named permalink pool inside a qualifier, optionally tied to a preset (PR 9).

    ``preset`` records which preset the pool's permalinks were rolled from
    (SET_NULL so deleting the preset detaches rather than cascade-deletes the
    pool). ``live_race`` permalinks in the pool run synchronously on racetime
    (PR 10); the flag lives on the permalink, not the pool.
    """

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='async_qualifier_pools', on_delete=fields.CASCADE)
    qualifier = fields.ForeignKeyField('models.AsyncQualifier', related_name='pools', on_delete=fields.CASCADE)
    name = fields.CharField(max_length=255)
    preset = fields.ForeignKeyField(
        'models.Preset', related_name='async_qualifier_pools', null=True, on_delete=fields.SET_NULL
    )
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    permalinks = fields.ReverseRelation["AsyncQualifierPermalink"]

    class Meta:
        table = 'asyncqualifierpool'
        unique_together = (('qualifier', 'name'),)
        indexes = (('tenant',), ('qualifier',))


class AsyncQualifierPermalink(Model):
    """One seed permalink in a pool; ``par_time`` is maintained from approved runs (PR 9).

    ``par_time`` (whole seconds) is the mean of the N fastest finished+approved
    runs on this permalink, recomputed by the scoring path; ``par_updated_at``
    timestamps that recompute. ``live_race`` marks a permalink reserved for a
    synchronous racetime run (PR 10).
    """

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='async_qualifier_permalinks', on_delete=fields.CASCADE)
    pool = fields.ForeignKeyField('models.AsyncQualifierPool', related_name='permalinks', on_delete=fields.CASCADE)
    url = fields.CharField(max_length=1024)
    notes = fields.TextField(null=True)
    generated_seed = fields.ForeignKeyField(  # type: ignore[var-annotated]
        'models.GeneratedSeeds', related_name='async_qualifier_permalinks',
        null=True, on_delete=fields.SET_NULL,
    )
    live_race = fields.BooleanField(default=False)
    par_time = fields.IntField(null=True)
    par_updated_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    runs = fields.ReverseRelation["AsyncQualifierRun"]

    class Meta:
        table = 'asyncqualifierpermalink'
        indexes = (('tenant',), ('pool',))


class AsyncQualifierRun(Model):
    """A player's single attempt at a drawn permalink (PR 9).

    Created ``IN_PROGRESS`` at draw time (web-first collapses reveal + start):
    the ``permalink`` is assigned inside a locked transaction so concurrent draws
    can't double-assign, ``started_at`` is server-stamped, and the player later
    submits ``elapsed_seconds`` + ``runner_vod_url`` (→ ``FINISHED``) or forfeits.
    Finished runs enter review (``review_status``); an approved run is par-scored.

    ``reattempted`` + ``reattempt_reason`` are the one-attempt integrity backstop:
    a reattempt voids the prior run (excluded from par/scoring/played-count),
    frees the pool slot, requires a reason, and is limited by
    ``AsyncQualifier.allowed_reattempts``. ``permalink`` is SET_NULL so purging a
    permalink keeps run history. ``live_race`` (PR 10) is set on runs captured
    from a synchronous racetime race and is ``None`` for self-paced runs.
    """

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='async_qualifier_runs', on_delete=fields.CASCADE)
    qualifier = fields.ForeignKeyField('models.AsyncQualifier', related_name='runs', on_delete=fields.CASCADE)
    user = fields.ForeignKeyField('models.User', related_name='async_qualifier_runs')
    permalink = fields.ForeignKeyField(
        'models.AsyncQualifierPermalink', related_name='runs', null=True, on_delete=fields.SET_NULL
    )
    live_race = fields.ForeignKeyField(
        'models.AsyncQualifierLiveRace', related_name='runs', null=True, on_delete=fields.SET_NULL
    )
    status = fields.CharEnumField(AsyncQualifierRunStatus, default=AsyncQualifierRunStatus.IN_PROGRESS, max_length=20)
    review_status = fields.CharEnumField(
        AsyncQualifierReviewStatus, default=AsyncQualifierReviewStatus.PENDING, max_length=20
    )
    started_at = fields.DatetimeField(null=True)
    finished_at = fields.DatetimeField(null=True)
    expired_at = fields.DatetimeField(null=True)
    expiry_warned_at = fields.DatetimeField(null=True)
    elapsed_seconds = fields.IntField(null=True)
    measured_seconds = fields.IntField(null=True)
    runner_vod_url = fields.CharField(max_length=1024, null=True)
    reattempted = fields.BooleanField(default=False)
    reattempt_reason = fields.TextField(null=True)
    reattempt_granted_by = fields.ForeignKeyField(
        'models.User', related_name='granted_async_qualifier_reattempts', null=True,
        on_delete=fields.SET_NULL,
    )
    score = fields.FloatField(null=True)
    reviewed_by = fields.ForeignKeyField(
        'models.User', related_name='reviewed_async_qualifier_runs', null=True, on_delete=fields.SET_NULL
    )
    reviewed_at = fields.DatetimeField(null=True)
    review_claimed_by = fields.ForeignKeyField(
        'models.User', related_name='claimed_async_qualifier_runs', null=True, on_delete=fields.SET_NULL
    )
    review_claimed_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    review_notes = fields.ReverseRelation["AsyncQualifierReviewNote"]

    class Meta:
        table = 'asyncqualifierrun'
        indexes = (
            ('tenant',),
            ('qualifier', 'review_status'),
            ('user',),
            ('permalink',),
            ('status', 'started_at'),        # expiry worker's cross-tenant scan
            # Not redundant with ('qualifier', 'review_status'): the leaderboard filters on all four.
            ('qualifier', 'reattempted', 'status', 'review_status'),
        )


class AsyncQualifierReviewNote(Model):
    """A reviewer's note attached to a run during review (PR 9)."""

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='async_qualifier_review_notes', on_delete=fields.CASCADE)
    run = fields.ForeignKeyField('models.AsyncQualifierRun', related_name='review_notes', on_delete=fields.CASCADE)
    author = fields.ForeignKeyField('models.User', related_name='authored_async_qualifier_review_notes')
    note = fields.TextField()
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = 'asyncqualifierreviewnote'
        indexes = (('tenant',), ('run',))


class AsyncQualifierLiveRace(Model):
    """A synchronous racetime race whose results flow into ``AsyncQualifierRun``s (PR 10).

    A pool permalink flagged ``live_race`` is raced live on racetime instead of
    self-paced: every entrant runs the same ``permalink`` in one room, and on
    finish each racetime entrant is mapped back to a ``User`` and captured as an
    ``AsyncQualifierRun`` (racetime status → run status; ``end_time`` → elapsed).
    Live-race runs **skip reviewer sign-off** — the racetime result is
    self-attributing — and are par-scored like any other approved run.

    Reuses the PR 4/6 racetime subsystem: opening a live race creates a
    :class:`RacetimeRoom` (with ``match=None``) whose lifecycle events the shared
    handler routes here by slug. ``racetime_slug`` mirrors that room's slug and is
    globally unique (nullable until a room opens). ``episode`` optionally links an
    SG-imported episode this race stands in for.
    """

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='async_qualifier_live_races', on_delete=fields.CASCADE)
    pool = fields.ForeignKeyField('models.AsyncQualifierPool', related_name='live_races', on_delete=fields.CASCADE)
    permalink = fields.ForeignKeyField(
        'models.AsyncQualifierPermalink', related_name='live_races', null=True, on_delete=fields.SET_NULL
    )
    match_title = fields.CharField(max_length=255)
    racetime_slug = fields.CharField(max_length=255, unique=True, null=True)
    episode = fields.ForeignKeyField(
        'models.SpeedGamingEpisode', related_name='async_qualifier_live_races', null=True,
        on_delete=fields.SET_NULL,
    )
    status = fields.CharEnumField(
        AsyncQualifierLiveRaceStatus, default=AsyncQualifierLiveRaceStatus.SCHEDULED, max_length=20
    )
    unmatched_handles = fields.JSONField(null=True)  # type: ignore[var-annotated]
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    runs = fields.ReverseRelation["AsyncQualifierRun"]

    class Meta:
        table = 'asyncqualifierliverace'
        indexes = (('tenant',), ('pool',))
