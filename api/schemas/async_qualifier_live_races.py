"""Schemas for async qualifier live-race endpoints."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from api.schemas.async_qualifiers import AsyncQualifierRunResponse
from models import AsyncQualifierLiveRaceStatus, AsyncQualifierRunStatus

RunResponse = AsyncQualifierRunResponse


class LiveRaceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="The live race's ID.")
    pool_id: int = Field(..., description='The pool the race belongs to.')
    match_title: str = Field(
        ...,
        description="The race's title, also used as the racetime room's info line.",
    )
    racetime_slug: Optional[str] = Field(
        None,
        description="The racetime.gg room's slug, or null until a room is opened.",
    )
    status: AsyncQualifierLiveRaceStatus = Field(
        ...,
        description=(
            'Race status: `scheduled` (no room yet), `pending` (room open, not started), '
            '`in_progress`, `finished` (results recorded) or `cancelled` (the racetime room '
            'was cancelled).'
        ),
    )
    permalink_id: Optional[int] = Field(
        None,
        description='The permalink the race is run on, or null until one is set.',
    )
    episode_id: Optional[int] = Field(
        None,
        description='The SpeedGaming episode this race stands in for, or null.',
    )
    unmatched_handles: Optional[List[str]] = Field(
        None,
        description=(
            "Racetime handles from the last result capture that couldn't be turned into a "
            'run: no linked account, or a finish with no time. Null or empty when there are '
            'none.'
        ),
    )
    created_at: datetime = Field(..., description='When the race was scheduled (UTC).')


class LiveRaceCreateRequest(BaseModel):
    pool_id: int = Field(..., description='The pool to race on.')
    match_title: str = Field(..., description="The race's title. Required, can't be blank.")
    permalink_id: Optional[int] = Field(
        None,
        description='The permalink to race on. Must belong to the pool. Leave out to set it later.',
    )
    episode_id: Optional[int] = Field(
        None,
        description='A SpeedGaming episode this race stands in for.',
    )


class PermalinkAssignRequest(BaseModel):
    """Set the pool permalink a scheduled race is run on.

    The counterpart to ``permalink_id: null`` on creation — without it a race
    scheduled before its seed was chosen could never be recorded at all.
    """

    permalink_id: int = Field(
        ...,
        description="The permalink to race on. Must belong to the race's pool.",
    )


class ManualResultRequest(BaseModel):
    """One racer's outcome, as a human asserts it."""

    user_id: int = Field(..., description="The racer's user ID.")
    status: AsyncQualifierRunStatus = Field(
        ...,
        description="The racer's outcome: `finished`, `forfeit` or `disqualified`.",
    )
    elapsed_seconds: Optional[int] = Field(
        None,
        description=(
            'Finish time in whole seconds. Required for `finished` and at most a week; '
            'ignored otherwise.'
        ),
    )


class ManualRecordRequest(BaseModel):
    """Record a live race's results by hand, when the room's event never arrived.

    The remedy for a dropped racetime connection: ``record_finish`` is reachable only
    from the inbound FINISHED event, so without this a missed event left the race
    stuck and its entrants unscored with nowhere to fix it.
    """

    results: List[ManualResultRequest] = Field(
        default_factory=list,
        description='One entry per racer. At least one, and each racer only once.',
    )
