"""Schemas for native bracket endpoints (thin wrappers over ``BracketService``).

Response models are ``from_attributes`` so ``BracketService`` ORM rows serialize
directly; enum fields (``format``, ``state``, ``status``) are typed as their
enums so FastAPI's JSON serialization emits the ``.value``.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from application.services.match.match_status import MatchStatus
from models import (
    BracketEntrantStatus,
    BracketEntryStatus,
    BracketFormat,
    BracketMatchGameState,
    BracketMatchState,
    BracketState,
)

_FORMAT = "Bracket format: `single_elim`, `double_elim`, `swiss` or `round_robin`."
_STATE = (
    "Stage state: `draft` (being set up, staff-only), `active` (running), `complete` "
    "(final ranks written) or `cancelled` (abandoned, staff-only)."
)
_CONFIG = (
    "Per-stage settings as a JSON object. Known keys include `grand_final_reset`, "
    "`default_best_of`, `swiss_rounds`, `group_count`, `win_points`, `draw_points`, "
    "`loss_points`, `bye_points`, `tiebreakers`, `rounds` and `advancement`; unknown "
    "keys are rejected."
)
_ENTRANT_STATUS = "Tournament-wide roster state: `active` or `dropped`."
_ENTRY_STATUS = "Participation in this stage: `active`, `dropped` or `eliminated`."


class BracketResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="Bracket (stage) id.")
    tournament_id: int = Field(..., description="The tournament this stage belongs to.")
    name: str = Field(..., description="Stage name, such as \"Groups\" or \"Top 8\".")
    format: BracketFormat = Field(..., description=_FORMAT)
    state: BracketState = Field(..., description=_STATE)
    stage_order: int = Field(..., description="Position in the tournament's stage sequence, starting at 0.")
    config: Optional[Dict[str, Any]] = Field(None, description=_CONFIG)
    created_at: datetime = Field(..., description="When the stage was created (UTC).")
    updated_at: datetime = Field(..., description="When the stage last changed (UTC).")


class BracketEntrantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="Entrant id. An entrant keeps this id across every stage.")
    tournament_id: int = Field(..., description="The tournament whose roster the entrant is on.")
    display_name: str = Field(..., description="Name shown in the bracket.")
    user_id: Optional[int] = Field(
        None, description="Linked user account. Null for a placeholder, whose matchups can't be scheduled yet."
    )
    status: BracketEntrantStatus = Field(..., description=_ENTRANT_STATUS)
    created_at: datetime = Field(..., description="When the entrant was added (UTC).")
    updated_at: datetime = Field(..., description="When the entrant last changed (UTC).")


class BracketEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="Entry id: one entrant's place in one stage.")
    bracket_id: int = Field(..., description="The stage this entry belongs to.")
    entrant_id: int = Field(..., description="The roster entrant behind the entry.")
    seed: Optional[int] = Field(None, description="Seed, 1 or greater. Null when unseeded.")
    group_number: Optional[int] = Field(None, description="Pool number in a round-robin stage. Null otherwise.")
    final_rank: Optional[int] = Field(None, description="Final placing, written when the stage completes. Null until then.")
    status: BracketEntryStatus = Field(..., description=_ENTRY_STATUS)
    created_at: datetime = Field(..., description="When the entry was created (UTC).")
    updated_at: datetime = Field(..., description="When the entry last changed (UTC).")


class BracketMatchGameResponse(BaseModel):
    """One game of a bracket match's best-of-N series."""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="Game id.")
    bracket_match_id: int = Field(..., description="The matchup this game belongs to.")
    game_number: int = Field(..., description="Game number within the series, starting at 1.")
    match_id: Optional[int] = Field(None, description="The scheduled match that plays this game.")
    winner_entry_id: Optional[int] = Field(None, description="Entry that won the game. Null until decided.")
    forfeit: bool = Field(False, description="Whether the game was decided by forfeit.")
    state: BracketMatchGameState = Field(
        ...,
        description="Game state: `scheduled`, `complete` or `cancelled` (not needed once the series was decided).",
    )
    cancelled_reason: Optional[str] = Field(None, description="Why the game was cancelled, when it was.")
    created_at: datetime = Field(..., description="When the game was booked (UTC).")
    updated_at: datetime = Field(..., description="When the game last changed (UTC).")


class BracketMatchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="Bracket match (matchup) id.")
    bracket_id: int = Field(..., description="The stage this matchup belongs to.")
    round: int = Field(..., description="Round number. Losers-bracket rounds in double elimination are negative.")
    position: int = Field(..., description="Position within the round.")
    group_number: Optional[int] = Field(None, description="Pool number in a round-robin stage. Null otherwise.")
    entry1_id: Optional[int] = Field(None, description="First entry. Null until it is known.")
    entry2_id: Optional[int] = Field(None, description="Second entry. Null until it is known.")
    winner_id: Optional[int] = Field(None, description="Winning entry. Null until decided.")
    entry1_score: Optional[int] = Field(None, description="First entry's reported score, if scores were reported.")
    entry2_score: Optional[int] = Field(None, description="Second entry's reported score, if scores were reported.")
    forfeit: bool = Field(False, description="Whether the result was a forfeit, disqualification or walkover.")
    state: BracketMatchState = Field(
        ...,
        description="Matchup state: `pending` (an entry isn't known yet), `open` (playable) or `complete` (winner recorded).",
    )
    # The derived cross-surface status (docs/features/brackets.md): what the
    # web bracket paints and what the Discord DM says, so an API
    # consumer sees "live" while a game is being raced rather than having to
    # re-derive it from the games' Match timestamps. Populated on the list reads;
    # None where the matchup's games were not resolved.
    status: Optional[MatchStatus] = Field(
        None,
        description=(
            "The status the web bracket shows: `pending`, `unscheduled`, `scheduled`, `checked_in`, "
            "`live`, `awaiting_result`, `complete`, `cancelled` or `needs_reschedule`. Filled in on "
            "`GET /brackets/{bracket_id}/matches` and `/open-matches`; null elsewhere."
        ),
    )
    winner_to_id: Optional[int] = Field(None, description="Matchup the winner moves on to. Null for a final.")
    winner_to_slot: Optional[int] = Field(None, description="Slot (1 or 2) the winner takes in that matchup.")
    loser_to_id: Optional[int] = Field(
        None, description="Matchup the loser drops to, in double elimination. Null otherwise."
    )
    loser_to_slot: Optional[int] = Field(None, description="Slot (1 or 2) the loser takes in that matchup.")
    best_of: Optional[int] = Field(
        None, description="Series length set for this matchup. Null uses the round's best-of, else 1."
    )
    created_at: datetime = Field(..., description="When the matchup was generated (UTC).")
    updated_at: datetime = Field(..., description="When the matchup last changed (UTC).")
    # Empty until a game is scheduled — rows are created lazily, so a best-of-3
    # with one game played reports one game, not three.
    games: List[BracketMatchGameResponse] = Field(
        [], description="Games booked so far. Empty until the first game is scheduled."
    )


class StandingsRowResponse(BaseModel):
    """One entry's placement, with the entrant's name already joined in."""

    model_config = ConfigDict(from_attributes=True)

    entry_id: int = Field(..., description="The stage entry this row ranks.")
    entrant_id: int = Field(..., description="The roster entrant behind the entry.")
    display_name: str = Field(..., description="Entrant's name.")
    seed: Optional[int] = Field(None, description="The entry's seed, if seeded.")
    # `status` is this stage's participation; `entrant_status` is the
    # tournament-wide roster state. They move independently — dropping an
    # entrant does not cascade into the stage entry — so check both to render
    # "dropped".
    status: BracketEntryStatus = Field(..., description=_ENTRY_STATUS)
    entrant_status: BracketEntrantStatus = Field(
        ..., description=_ENTRANT_STATUS + " Independent of `status`; check both to show a drop."
    )
    rank: int = Field(..., description="Current rank from the standings calculation. Tied entries share a rank.")
    final_rank: Optional[int] = Field(
        None, description="Rank written when the stage completed. Null until then; can differ from `rank` where staff broke a tie."
    )
    points: float = Field(..., description="Standings points.")
    wins: int = Field(..., description="Matches won.")
    draws: int = Field(..., description="Matches drawn.")
    losses: int = Field(..., description="Matches lost.")
    byes: int = Field(..., description="Byes received.")
    tiebreakers: Dict[str, float] = Field({}, description="Tiebreaker values, keyed by tiebreaker name.")
    tied_with: List[int] = Field([], description="Entry ids still sharing this rank after tiebreakers.")


class StandingsGroupResponse(BaseModel):
    """One pool's ranked rows; ``group_number`` is null for a single pool."""

    model_config = ConfigDict(from_attributes=True)

    group_number: Optional[int] = Field(None, description="Pool number. Null for a Swiss stage or a single pool.")
    rows: List[StandingsRowResponse] = Field(..., description="Entries in rank order.")


class ScheduleGameRequest(BaseModel):
    """Schedule the next game of a series. The game number is server-assigned."""

    scheduled_date: str = Field(..., description="Date as `YYYY-MM-DD`, in the community's timezone.")
    scheduled_time: str = Field(..., description="Time as `HH:MM` (24-hour), in the community's timezone.")
    stage_id: Optional[int] = Field(
        None, description="Stage to run the match on. Only Staff and admins of this tournament may set it."
    )
    comment: Optional[str] = Field(None, description="Optional note for the match.")


class LinkGameRequest(BaseModel):
    """Attach an existing scheduled ``Match`` to a matchup as its next game."""

    scheduled_match_id: int = Field(
        ..., description="An existing match in the same tournament whose players are exactly this matchup's two entrants."
    )


class SetBestOfRequest(BaseModel):
    """Override one matchup's series length (null = fall back to the round)."""

    best_of: Optional[int] = Field(
        None, description="Odd number, 1 or more. Null clears the override so the round's setting applies."
    )


class BracketCreateRequest(BaseModel):
    tournament_id: int = Field(..., description="The tournament to add a stage to.")
    name: str = Field(..., description="Stage name.")
    format: BracketFormat = Field(..., description=_FORMAT)
    stage_order: int = Field(
        0, description="Position in the tournament's stage sequence, starting at 0. Must not already be taken."
    )
    config: Optional[Dict[str, Any]] = Field(None, description=_CONFIG)


class EntrantCreateRequest(BaseModel):
    tournament_id: int = Field(..., description="The tournament whose roster to add to.")
    display_name: str = Field(..., description="Name shown in the bracket.")
    user_id: Optional[int] = Field(
        None, description="A member of this community to link. Omit for a placeholder you link later."
    )


class BracketUpdateRequest(BaseModel):
    """Edit a DRAFT bracket's definition. Omitted fields are left alone."""

    name: Optional[str] = Field(None, description="New stage name.")
    stage_order: Optional[int] = Field(None, description="New position in the stage sequence. Must not already be taken.")
    config: Optional[Dict[str, Any]] = Field(None, description=_CONFIG + " Replaces the stored settings.")
    format: Optional[BracketFormat] = Field(None, description=_FORMAT)


class EntrantUserRequest(BaseModel):
    """Link a roster entrant to a user account (null unlinks)."""

    user_id: Optional[int] = Field(None, description="A member of this community to link. Null unlinks.")


class RosterImportRequest(BaseModel):
    """Import a tournament's enrolled players as linked entrants."""

    tournament_id: int = Field(..., description="The tournament whose enrolled players to import.")


class RoundMetadataRequest(BaseModel):
    """Replace the per-round metadata (``{round: {best_of, scheduled_at, scheduled_end}}``).

    Null or empty clears it. Unlike the rest of the bracket definition this is
    editable after the stage has started — round chrome never touches the graph.
    ``scheduled_at``/``scheduled_end`` are the round's window (UTC ISO-8601, end
    after start); match-time suggestions for the round are confined to it.
    """

    rounds: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "Settings per round, keyed by round number as a string: "
            "`{\"1\": {\"best_of\": 3, \"scheduled_at\": \"...\", \"scheduled_end\": \"...\"}}`. "
            "`best_of` is an odd number; `scheduled_at` and `scheduled_end` are the round's window "
            "as ISO-8601 UTC timestamps. Null or empty clears all round settings."
        ),
    )


class SetSeedsRequest(BaseModel):
    """Per-entry seeds (``entry_id → seed``); a null seed clears that entry's."""

    seeds: Dict[int, Optional[int]] = Field(
        ...,
        description="Seed per entry id. Seeds are 1 or greater and unique in the stage; null clears an entry's seed. Entries not listed keep theirs.",
    )


class EnrollRequest(BaseModel):
    entrant_id: int = Field(..., description="A roster entrant of the same tournament.")
    seed: Optional[int] = Field(None, description="Optional seed.")
    group_number: Optional[int] = Field(None, description="Pool number, for a round-robin stage.")


class ReportResultRequest(BaseModel):
    winner_entry_id: int = Field(..., description="The winning entry. Must be one of the matchup's two entries.")
    entry1_score: Optional[int] = Field(
        None, description="First entry's score. Send both scores or neither; scores can't be negative."
    )
    entry2_score: Optional[int] = Field(
        None, description="Second entry's score. Send both scores or neither; scores can't be negative."
    )
    forfeit: bool = Field(
        False, description="Mark a forfeit, disqualification or walkover. Lets the winner have the lower score."
    )


class AdvanceStageRequest(BaseModel):
    from_stage_order: int = Field(..., description="The completed stage whose top finishers move into the next stage.")


class CompleteStageRequest(BaseModel):
    """Optional tie resolutions applied while writing ``final_rank``.

    ``{entry_id: rank}`` for entries the standings pass left sharing a rank.
    """

    tie_breaks: Optional[Dict[int, int]] = Field(
        None, description="Final rank per entry id, for entries the standings left tied."
    )


__all__ = [
    'AdvanceStageRequest',
    'BracketCreateRequest',
    'BracketEntrantResponse',
    'BracketEntryResponse',
    'BracketMatchGameResponse',
    'BracketMatchResponse',
    'BracketResponse',
    'BracketUpdateRequest',
    'CompleteStageRequest',
    'EnrollRequest',
    'EntrantCreateRequest',
    'EntrantUserRequest',
    'ReportResultRequest',
    'RosterImportRequest',
    'RoundMetadataRequest',
    'ScheduleGameRequest',
    'SetBestOfRequest',
    'SetSeedsRequest',
    'StandingsGroupResponse',
    'StandingsRowResponse',
]
