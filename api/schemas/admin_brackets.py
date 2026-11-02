"""Schemas for the app's Admin → Brackets screen."""

from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class _Attrs(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class BracketNextStepResponse(_Attrs):
    key: Optional[str] = Field(
        None, description="The button the step offers: `enroll`, `start`, `report`, `complete`, `advance`, or null for none.",
    )
    title: str = Field(..., description="The step, such as \"Enroll and seed, then start\".")
    detail: str = Field(..., description="The counts behind it.")
    done: int = Field(..., description="Progress so far (enrolled, or matches reported).")
    total: int = Field(..., description="What progress counts towards.")


class BracketStageRowResponse(_Attrs):
    id: int
    tournament_id: int
    name: str
    stage_order: int = Field(..., description="0-based place in the tournament's chain of stages.")
    stage_label: str = Field(..., description="1-based, as players see it: \"Stage 1\".")
    format: str
    format_label: str
    state: str = Field(..., description="`draft`, `active`, `complete` or `cancelled`.")
    state_label: str
    config: List[str] = Field(..., description="The stage's rules in plain words, one phrase each.")
    next_step: BracketNextStepResponse
    can_complete: bool = Field(..., description="Swiss and round robin finish by hand; elimination stages finish themselves.")
    can_advance: bool = Field(..., description="Complete, and the next stage is a draft that draws from this one.")


class BracketTournamentResponse(_Attrs):
    id: int
    name: str
    is_active: bool
    stages: List[BracketStageRowResponse]


class BracketFormatOption(BaseModel):
    value: str
    label: str


class BracketsBoardResponse(BaseModel):
    tournaments: List[BracketTournamentResponse] = Field(
        ..., description="This event's tournaments that aren't on Challonge, active first, each with its stages in order.",
    )
    formats: List[BracketFormatOption]


class BracketSeedEntryResponse(_Attrs):
    id: int = Field(..., description="The entry (an entrant's place in this stage).")
    entrant_id: int
    name: str
    seed: Optional[int] = None
    group_number: Optional[int] = None
    status: str
    final_rank: Optional[int] = None


class BracketRosterEntrantResponse(_Attrs):
    id: int = Field(..., description="The entrant (on the tournament's roster).")
    name: str
    user_id: Optional[int] = None
    status: str = Field(..., description="`active` or `dropped`.")
    enrolled: bool = Field(..., description="Already has an entry in this stage.")


class BracketSideResponse(_Attrs):
    entry_id: Optional[int] = None
    name: str
    score: Optional[int] = None


class BracketStageMatchResponse(_Attrs):
    id: int
    label: str = Field(..., description="Round name, group and board, such as \"Semifinals · Match 2\".")
    round: int
    state: str = Field(..., description="`open` or `complete` (pending matchups are left out).")
    best_of: Optional[int] = None
    side1: BracketSideResponse
    side2: BracketSideResponse
    winner_entry_id: Optional[int] = None
    forfeit: bool


class BracketStageDetailResponse(_Attrs):
    row: BracketStageRowResponse
    tournament_name: str
    entries: List[BracketSeedEntryResponse] = Field(..., description="Seeded first, by seed.")
    roster: List[BracketRosterEntrantResponse] = Field(..., description="The tournament's whole roster, by name.")
    matches: List[BracketStageMatchResponse] = Field(..., description="Open matchups first, then results; empty for a draft.")


class QualifierSeedingResponse(_Attrs):
    seeds: Dict[int, int] = Field(..., description="Entry id → seed, best leaderboard place first. Apply with `PATCH /brackets/{id}/seeds`.")
    unmatched: List[str] = Field(..., description="Entrants the leaderboard doesn't cover, left out of `seeds`.")
