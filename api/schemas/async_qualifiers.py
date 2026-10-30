"""Schemas for the async-qualifier endpoints (api/routers/async_qualifiers.py)."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from models import AsyncQualifierReviewStatus, AsyncQualifierRunStatus


class AsyncQualifierResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="The qualifier's ID.")
    name: str = Field(..., description="The qualifier's name.")
    description: Optional[str] = Field(None, description='Longer text shown to runners, or null.')
    event_name: Optional[str] = Field(
        None,
        description='The event this qualifier feeds, for display only, or null.',
    )
    opens_at: Optional[datetime] = Field(
        None,
        description='When runs can start (UTC), or null for no start limit.',
    )
    closes_at: Optional[datetime] = Field(
        None,
        description=(
            'When the qualifier closes (UTC), or null for no end. Exact scores and the '
            'leaderboard go public at this time.'
        ),
    )
    runs_per_pool: int = Field(..., description='How many runs each runner gets from every pool.')
    allowed_reattempts: int = Field(
        ...,
        description=(
            'How many runs each runner may void for a fresh draw, across the whole '
            'qualifier.'
        ),
    )
    config: Optional[dict] = Field(
        None,
        description=(
            'Internal settings, or null for defaults. Keys: `par_sample_size` (how many '
            'fastest approved runs average into par), `draw_imbalance_threshold` '
            '(play-count gap at which the draw forces the least-played permalink), '
            '`run_time_limit_hours` (1 to 168), `expiry_warning_minutes`, and '
            '`messaging_templates` (DM text by name).'
        ),
    )
    is_active: bool = Field(
        ...,
        description=(
            'Whether the qualifier is switched on. Switching it off closes it and makes '
            'results public.'
        ),
    )
    created_at: datetime = Field(..., description='When the qualifier was created (UTC).')
    updated_at: datetime = Field(..., description='When the qualifier was last changed (UTC).')


class AsyncQualifierPublicResponse(BaseModel):
    """The player-facing public shell — name, window, and the run rules only.

    Deliberately omits ``config`` (internal draw-fairness / par-scoring knobs and
    messaging templates), which is admin-only: ``get_qualifier_for_player`` is
    ungated, so the ``/public`` route must not leak those internals the way the
    admin-gated ``AsyncQualifierResponse`` does.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="The qualifier's ID.")
    name: str = Field(..., description="The qualifier's name.")
    description: Optional[str] = Field(None, description='Longer text shown to runners, or null.')
    event_name: Optional[str] = Field(
        None,
        description='The event this qualifier feeds, for display only, or null.',
    )
    opens_at: Optional[datetime] = Field(
        None,
        description='When runs can start (UTC), or null for no start limit.',
    )
    closes_at: Optional[datetime] = Field(
        None,
        description=(
            'When the qualifier closes (UTC), or null for no end. Exact scores and the '
            'leaderboard go public at this time.'
        ),
    )
    runs_per_pool: int = Field(..., description='How many runs each runner gets from every pool.')
    allowed_reattempts: int = Field(
        ...,
        description=(
            'How many runs each runner may void for a fresh draw, across the whole '
            'qualifier.'
        ),
    )
    is_active: bool = Field(
        ...,
        description=(
            'Whether the qualifier is switched on. Switching it off closes it and makes '
            'results public.'
        ),
    )
    created_at: datetime = Field(..., description='When the qualifier was created (UTC).')
    updated_at: datetime = Field(..., description='When the qualifier was last changed (UTC).')


class QualifierCreateRequest(BaseModel):
    name: str = Field(..., description="The qualifier's name. Required, can't be blank.")
    description: Optional[str] = Field(None, description='Longer text shown to runners.')
    event_name: Optional[str] = Field(
        None,
        description='The event this qualifier feeds, for display only.',
    )
    opens_at: Optional[datetime] = Field(
        None,
        description='When runs can start (UTC). Leave out for no start limit.',
    )
    closes_at: Optional[datetime] = Field(
        None,
        description=(
            'When the qualifier closes (UTC). Must be after `opens_at`. Leave out for no '
            'end.'
        ),
    )
    runs_per_pool: int = Field(
        1,
        description='How many runs each runner gets from every pool. At least 1.',
    )
    allowed_reattempts: int = Field(
        0,
        description=(
            'How many runs each runner may void for a fresh draw, across the whole '
            'qualifier. 0 or more.'
        ),
    )
    config: Optional[dict] = Field(
        None,
        description=(
            'Internal settings; leave out for defaults. Allowed keys: `par_sample_size`, '
            '`draw_imbalance_threshold`, `run_time_limit_hours` (1 to 168), '
            '`expiry_warning_minutes` and `messaging_templates`. Any other key is refused.'
        ),
    )


class QualifierUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, description="New name. Can't be blank.")
    description: Optional[str] = Field(
        None,
        description='New description. An empty string clears it.',
    )
    event_name: Optional[str] = Field(
        None,
        description='New event name. An empty string clears it.',
    )
    opens_at: Optional[datetime] = Field(None, description='New start time (UTC).')
    closes_at: Optional[datetime] = Field(
        None,
        description='New close time (UTC). Must be after the start time.',
    )
    runs_per_pool: Optional[int] = Field(None, description='New runs per pool. At least 1.')
    allowed_reattempts: Optional[int] = Field(
        None,
        description='New reattempt allowance. 0 or more.',
    )
    is_active: Optional[bool] = Field(None, description='Switch the qualifier on or off.')
    config: Optional[dict] = Field(
        None,
        description=(
            'Replacement internal settings. Replaces the whole object; the same keys as on '
            'create are allowed.'
        ),
    )
    clear_window: bool = Field(
        False,
        description=(
            'Set true to remove the window. Times you send alongside it are kept and any '
            'you leave out are cleared.'
        ),
    )


class AdminRequest(BaseModel):
    user_id: int = Field(
        ...,
        description='The ID of the person to list as an admin of this qualifier.',
    )


class AsyncQualifierPoolResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="The pool's ID.")
    qualifier_id: int = Field(..., description='The qualifier the pool belongs to.')
    name: str = Field(..., description="The pool's name, unique within its qualifier.")
    preset_id: Optional[int] = Field(
        None,
        description='The preset seeds are rolled from, or null if the pool has none.',
    )
    created_at: datetime = Field(..., description='When the pool was created (UTC).')
    updated_at: datetime = Field(..., description='When the pool was last changed (UTC).')


class PoolCreateRequest(BaseModel):
    name: str = Field(
        ...,
        description="The pool's name. Required, and unique within the qualifier (ignoring case).",
    )
    preset_id: Optional[int] = Field(
        None,
        description='A preset to roll seeds from. Leave out to add permalinks by hand only.',
    )


class PoolUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, description="New name. Can't be blank.")
    preset_id: Optional[int] = Field(None, description='A new preset to roll seeds from.')
    clear_preset: bool = Field(
        False,
        description="Set true to detach the pool's preset. Takes priority over `preset_id`.",
    )


class AsyncQualifierPermalinkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="The permalink's ID.")
    pool_id: int = Field(..., description='The pool it belongs to.')
    url: str = Field(..., description="The seed's permalink URL.")
    notes: Optional[str] = Field(None, description='Admin notes on the seed, or null.')
    live_race: bool = Field(
        ...,
        description='True if the seed is kept for live races, so self-paced runners never draw it.',
    )
    par_time: Optional[int] = Field(
        None,
        description=(
            'Par in whole seconds, averaged from the fastest approved finishes on this '
            'seed, or null before any.'
        ),
    )
    par_updated_at: Optional[datetime] = Field(
        None,
        description='When par was last recalculated (UTC), or null.',
    )
    created_at: datetime = Field(..., description='When the permalink was added (UTC).')
    updated_at: datetime = Field(..., description='When the permalink was last changed (UTC).')


class PermalinkCreateRequest(BaseModel):
    url: str = Field(..., description="The seed's permalink. Must be an http(s) URL with a host.")
    notes: Optional[str] = Field(None, description='Admin notes on the seed.')
    live_race: bool = Field(
        False,
        description=(
            'Set true to keep this seed for live races, so self-paced runners never draw '
            'it.'
        ),
    )


class PermalinkBulkRequest(BaseModel):
    urls: List[str] = Field(
        default_factory=list,
        description='One permalink URL per entry. Blank entries are ignored.',
    )


class RejectedPermalinkLine(BaseModel):
    """One line a paste-many refused, and why."""

    line: int = Field(..., description="The entry's 1-based position in the submitted `urls`.")
    value: str = Field(..., description='The entry as submitted, trimmed.')
    reason: str = Field(..., description="Why it wasn't added.")


class PermalinkBulkResponse(BaseModel):
    """What a paste-many produced, including the lines it would not take.

    An envelope rather than the bare array this endpoint used to return, for the
    same reason the page reports line by line: a caller that hears "5 created" for
    7 submitted lines has no way to learn which 2 were dropped, and a permalink is
    revealed to a runner at the moment their slot is spent.
    """

    created: List[AsyncQualifierPermalinkResponse] = Field(
        ...,
        description='The permalinks that were added.',
    )
    rejected: List[RejectedPermalinkLine] = Field(
        default_factory=list,
        description='The entries that were skipped, and why. Empty when every entry was added.',
    )


class PermalinkRollRequest(BaseModel):
    count: int = Field(..., description='How many seeds to roll, from 1 to 25.')


class PermalinkUpdateRequest(BaseModel):
    url: Optional[str] = Field(
        None,
        description='New permalink URL. Must be an http(s) URL with a host.',
    )
    notes: Optional[str] = Field(None, description='New admin notes. An empty string clears them.')
    live_race: Optional[bool] = Field(None, description='Whether to keep this seed for live races.')


class AsyncQualifierRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="The run's ID.")
    qualifier_id: int = Field(..., description='The qualifier the run belongs to.')
    user_id: int = Field(..., description="The runner's user ID.")
    permalink_id: Optional[int] = Field(
        None,
        description='The permalink drawn for the run, or null if it has since been deleted.',
    )
    live_race_id: Optional[int] = Field(
        None,
        description='The live race the run was captured from, or null for a self-paced run.',
    )
    status: AsyncQualifierRunStatus = Field(
        ...,
        description=(
            'Run status: `pending` (created ahead of a live race, not yet started), '
            '`in_progress`, `finished`, `forfeit` or `disqualified`.'
        ),
    )
    review_status: AsyncQualifierReviewStatus = Field(
        ...,
        description=(
            'Review verdict: `pending`, `approved` or `rejected`. A forfeit is settled as '
            '`approved` straight away.'
        ),
    )
    started_at: Optional[datetime] = Field(
        None,
        description='When the run started (UTC): the moment the seed was revealed.',
    )
    finished_at: Optional[datetime] = Field(
        None,
        description=(
            'When the run was submitted, forfeited or recorded (UTC), or null while in '
            'progress.'
        ),
    )
    elapsed_seconds: Optional[int] = Field(
        None,
        description=(
            'The finish time in whole seconds, as the runner claimed it or racetime '
            'reported it. Null for an unfinished run.'
        ),
    )
    measured_seconds: Optional[int] = Field(
        None,
        description=(
            'Server-measured seconds from the draw to the submit. An upper bound on the '
            'run, kept as evidence for reviewers. Null for live-race runs.'
        ),
    )
    runner_vod_url: Optional[str] = Field(None, description="The runner's VoD link, or null.")
    reattempted: bool = Field(
        ...,
        description=(
            "True if the run was voided to free its pool slot. A voided run doesn't count "
            'toward scores or par.'
        ),
    )
    reattempt_reason: Optional[str] = Field(
        None,
        description='The reason given for the reattempt, or null.',
    )
    score: Optional[float] = Field(
        None,
        description=(
            'Par score from 0 to 105: 100 at par, 0 at twice par. Null until the run is '
            'approved and its seed has a par, and after a rejection.'
        ),
    )
    reviewed_by_id: Optional[int] = Field(None, description='Who gave the verdict, or null.')
    reviewed_at: Optional[datetime] = Field(
        None,
        description='When the verdict was given (UTC), or null.',
    )
    review_claimed_by_id: Optional[int] = Field(
        None,
        description='The reviewer holding a claim on the run, or null.',
    )
    review_claimed_at: Optional[datetime] = Field(
        None,
        description='When that claim was taken (UTC), or null. A claim lapses two hours after this.',
    )
    created_at: datetime = Field(..., description='When the run record was created (UTC).')
    updated_at: datetime = Field(..., description='When the run was last changed (UTC).')


class AsyncQualifierRunPage(BaseModel):
    """One page of a qualifier's runs, and how many there are in total.

    The same envelope ``GET /audit-logs`` uses. An envelope rather than a bare array
    because the array had no bound: a 500-player qualifier answered with 3,129 runs
    in one 1.66 MB body, and a caller wanting the ten most recent could not say so.
    """

    total: int = Field(
        ...,
        description='How many runs the qualifier holds in all, not how many came back.',
    )
    limit: int = Field(..., description='The page size you asked for.')
    offset: int = Field(..., description='How many runs were skipped before this page.')
    items: List[AsyncQualifierRunResponse] = Field(
        ...,
        description='This page of runs, newest first.',
    )


class MyQualifierRunResponse(BaseModel):
    """One of the caller's own runs — a narrower projection than the admin view.

    Separate from :class:`AsyncQualifierRunResponse` because the two audiences are
    not the same. An exact ``score`` is exactly solvable for the seed's par (the
    caller knows their own ``elapsed_seconds``), so it is withheld while the
    qualifier is open and ``score_band`` stands in; a reviewer reading
    ``/{id}/runs`` still gets the number. The projection also carries what the
    runner needs and the model alone did not spell out: the seed played, the
    deadline an in-progress run dies at, and whether a void was a reviewer's.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="The run's ID.")
    pool_name: str = Field(..., description='The name of the pool the run was drawn from.')
    permalink_url: Optional[str] = Field(
        None,
        description='The seed you played, or null if it has since been deleted.',
    )
    status: AsyncQualifierRunStatus = Field(
        ...,
        description=(
            'Run status: `pending` (created ahead of a live race, not yet started), '
            '`in_progress`, `finished`, `forfeit` or `disqualified`.'
        ),
    )
    review_status: AsyncQualifierReviewStatus = Field(
        ...,
        description=(
            'Review verdict: `pending`, `approved` or `rejected`. A forfeit is settled as '
            '`approved` straight away.'
        ),
    )
    started_at: Optional[datetime] = Field(None, description='When the run started (UTC).')
    finished_at: Optional[datetime] = Field(
        None,
        description=(
            'When the run was submitted, forfeited or recorded (UTC), or null while in '
            'progress.'
        ),
    )
    deadline: Optional[datetime] = Field(
        None,
        description=(
            "When a run in progress is forfeited automatically (UTC). Null once it's no "
            'longer in progress.'
        ),
    )
    elapsed_seconds: Optional[int] = Field(
        None,
        description='Your claimed finish time in whole seconds, or null.',
    )
    measured_seconds: Optional[int] = Field(
        None,
        description='Server-measured seconds from your draw to your submit, or null.',
    )
    runner_vod_url: Optional[str] = Field(None, description='Your VoD link, or null.')
    reattempted: bool = Field(..., description='True if the run was voided to free its pool slot.')
    reattempt_reason: Optional[str] = Field(
        None,
        description='The reason given for the reattempt, or null.',
    )
    reattempt_was_granted: bool = Field(
        ...,
        description=(
            'True if a reviewer voided the run for you rather than you spending one of your '
            'own reattempts.'
        ),
    )
    expired_at: Optional[datetime] = Field(
        None,
        description=(
            'When the run was forfeited automatically for running past its deadline (UTC). '
            'Null if you finished or forfeited it yourself.'
        ),
    )
    score: Optional[float] = Field(
        None,
        description=(
            'Par score from 0 to 105. Null while the qualifier is open (read `score_band` '
            'instead) and for an unscored run.'
        ),
    )
    score_band: Optional[str] = Field(
        None,
        description=(
            'Where a finished run stands against par: `under`, `near` or `above`. Null for '
            "a run that didn't finish."
        ),
    )
    notes: List[str] = Field(
        default_factory=list,
        description='Reviewer notes on the run, oldest first.',
    )


class StartRunRequest(BaseModel):
    pool_id: int = Field(..., description='The pool to draw from. Must belong to this qualifier.')


class SubmitRunRequest(BaseModel):
    elapsed_seconds: int = Field(
        ...,
        description=(
            'Your finish time in whole seconds. Must be positive, at most a week, and no '
            'longer than the run has been going.'
        ),
    )
    runner_vod_url: Optional[str] = Field(
        None,
        description='A link to your VoD, if you have one. Must be an http(s) URL.',
    )


class ReattemptRequest(BaseModel):
    reason: str = Field(
        ...,
        description=(
            'Why the run is being voided. Required. On a granted reattempt the runner is '
            'sent this text.'
        ),
    )


class ReviewRequest(BaseModel):
    approved: bool = Field(..., description='True to approve the run, false to reject it.')
    note: Optional[str] = Field(
        None,
        description='A note for the runner, who gets it in a Discord DM. Required when rejecting.',
    )


class AsyncQualifierReviewNoteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="The note's ID.")
    run_id: int = Field(..., description='The run the note is on.')
    author_id: int = Field(..., description='The reviewer who wrote it.')
    note: str = Field(..., description="The note's text.")
    created_at: datetime = Field(..., description='When the note was written (UTC).')


class LeaderboardEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    rank: int = Field(
        ...,
        description='Competition rank: equal `actual` totals share a rank, and the next rank skips.',
    )
    user_id: int = Field(..., description="The runner's user ID.")
    username: str = Field(..., description="The runner's display name.")
    actual: float = Field(
        ...,
        description=(
            "The sum of the runner's best scores across their slots, with empty slots "
            'counting 0.'
        ),
    )
    estimate: float = Field(
        ...,
        description="A projection that fills every slot at the runner's average score.",
    )
    slots_filled: int = Field(..., description="How many of the runner's slots hold a run.")
    slots_total: int = Field(
        ...,
        description='How many slots the runner has: `runs_per_pool` for every pool open to them.',
    )
