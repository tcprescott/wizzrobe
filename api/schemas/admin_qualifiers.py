"""Schemas for the app's Admin → Qualifiers screen."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class _Attrs(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class QualifierRowResponse(_Attrs):
    id: int
    name: str
    state: str = Field(..., description="`open`, `upcoming`, `closed`, or `off` (switched off).")
    state_label: str
    opens_at: Optional[datetime] = Field(None, description="When runs open (UTC), or null for no start.")
    closes_at: Optional[datetime] = Field(None, description="When runs close (UTC), or null for no end.")
    to_review: int = Field(..., description="Finished runs waiting for a verdict.")
    runs: int = Field(..., description="Every run in the qualifier.")


class QualifierBoardResponse(_Attrs):
    qualifiers: List[QualifierRowResponse] = Field(..., description="Open ones first, then by how much is waiting.")
    can_create: bool = Field(..., description="Whether the caller may add a qualifier (Staff or Qualifier Admin).")
    zone: Optional[str] = Field(None, description="The IANA zone to show times in for this viewer, or null for the device's.")


class QualifierQueueCardResponse(_Attrs):
    run_id: int
    runner: str
    runner_id: int
    pool: str
    claimed_seconds: Optional[int] = Field(None, description="The time the runner submitted.")
    measured_seconds: Optional[int] = Field(None, description="The wall clock from draw to submit.")
    implausible: bool = Field(..., description="The claimed time is well under what the wall clock allows.")
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    permalink_url: Optional[str] = None
    vod_url: Optional[str] = None
    other_runs: str = Field(..., description="The runner's other attempts here, or empty.")
    notes: List[str] = Field(..., description="Review notes already on the run.")
    held_by: Optional[str] = Field(None, description="Who holds the review claim, or null.")
    held_by_me: bool


class QualifierBoardLineResponse(_Attrs):
    rank: int
    name: str
    score: float
    slots: str = Field(..., description="Slots filled out of the total, as \"4/6\".")


class QualifierSetupResponse(_Attrs):
    pools: int
    seeds: int
    live_races: int
    live_races_open: int = Field(..., description="Live races scheduled, open or racing.")
    reviewers: int


class QualifierSettingsResponse(_Attrs):
    description: Optional[str] = None
    event_name: Optional[str] = None
    runs_per_pool: int
    allowed_reattempts: int
    is_active: bool


class QualifierDetailResponse(_Attrs):
    row: QualifierRowResponse
    settings: QualifierSettingsResponse
    queue: List[QualifierQueueCardResponse] = Field(..., description="Runs waiting for review, oldest finish first.")
    leaderboard: List[QualifierBoardLineResponse] = Field(..., description="The top of the leaderboard.")
    leaderboard_total: int
    setup: QualifierSetupResponse


class QualifierPermalinkLineResponse(_Attrs):
    id: int
    url: str
    notes: Optional[str] = None
    live_race: bool = Field(..., description="Kept for live races, not drawn for async runs.")
    par_seconds: Optional[int] = None


class QualifierPoolLineResponse(_Attrs):
    id: int
    name: str
    preset_id: Optional[int] = None
    preset_name: Optional[str] = None
    roll_refusal: Optional[str] = Field(None, description="Why seeds can't be rolled into this pool, or null when they can.")
    permalinks: List[QualifierPermalinkLineResponse]


class QualifierPersonResponse(_Attrs):
    id: int
    name: str
    username: Optional[str] = None


class QualifierReviewersResponse(_Attrs):
    reviewers: List[QualifierPersonResponse] = Field(..., description="People who review this qualifier only.")
    members: List[QualifierPersonResponse] = Field(..., description="The community's active members, to add from.")


class QualifierLiveRaceLineResponse(_Attrs):
    id: int
    title: str
    pool: str
    status: str
    status_label: str
    racetime_slug: Optional[str] = None
    has_seed: bool
