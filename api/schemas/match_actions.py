"""Request schemas for match write actions."""

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class MatchCreateRequest(BaseModel):
    tournament_id: int = Field(..., description="The tournament the match belongs to.")
    scheduled_date: str = Field(..., description="Date in the community's timezone, YYYY-MM-DD")
    scheduled_time: str = Field(..., description="Time in the community's timezone, HH:MM")
    player_ids: List[int] = Field(..., min_length=1, description="User IDs of the players")
    comment: Optional[str] = Field(None, description="Free-text note shown on the match.")
    stage_id: Optional[int] = Field(None, description="Stage to put the match on, or null for none.")
    commentator_ids: Optional[List[int]] = Field(
        None, description="User IDs to assign as commentators, already approved. Each is DMed to confirm."
    )
    tracker_ids: Optional[List[int]] = Field(
        None, description="User IDs to assign as trackers, already approved. Each is DMed to confirm."
    )
    is_stream_candidate: bool = Field(
        False, description="Mark the match as a candidate for stream, which alerts people who follow stream candidates."
    )


class MatchRequestCreate(BaseModel):
    """Player-initiated match request. The caller must be among ``player_ids``."""

    tournament_id: int = Field(..., description="The tournament the match belongs to.")
    scheduled_date: str = Field(..., description="Date in the community's timezone, YYYY-MM-DD")
    scheduled_time: str = Field(..., description="Time in the community's timezone, HH:MM")
    player_ids: List[int] = Field(
        ...,
        min_length=1,
        description=(
            "User IDs of the players, including your own. Everyone else must "
            "already be signed up for the tournament."
        ),
    )
    comment: Optional[str] = Field(None, description="Free-text note shown on the match.")


class MatchUpdateRequest(BaseModel):
    tournament_id: Optional[int] = Field(None, description="Move the match to this tournament. Omit to keep it.")
    scheduled_date: Optional[str] = Field(
        None, description="New date in the community's timezone, YYYY-MM-DD. Send with `scheduled_time`."
    )
    scheduled_time: Optional[str] = Field(
        None, description="New time in the community's timezone, HH:MM. Send with `scheduled_date`."
    )
    player_ids: Optional[List[int]] = Field(
        None, description="The full new list of player user IDs, replacing the current one. Omit to keep it."
    )
    commentator_ids: Optional[List[int]] = Field(
        None, description="The full new list of commentator user IDs, replacing the current one. Omit to keep it."
    )
    tracker_ids: Optional[List[int]] = Field(
        None, description="The full new list of tracker user IDs, replacing the current one. Omit to keep it."
    )
    comment: Optional[str] = Field(None, description="New free-text note. Omit to keep the current one.")
    clear_seated: bool = Field(False, description="Clear the check-in time.")
    clear_started: bool = Field(False, description="Clear the start time.")
    clear_finished: bool = Field(False, description="Clear the finish time.")
    clear_confirmed: bool = Field(False, description="Clear the confirmation time.")
    clear_seed: bool = Field(False, description="Remove the rolled seed so a new one can be rolled.")


class SetGameTime(BaseModel):
    scheduled_date: str = Field(..., description="Date in the community's timezone, YYYY-MM-DD")
    scheduled_time: str = Field(..., description="Time in the community's timezone, HH:MM")


class ExtendToSetRequest(BaseModel):
    later_games: List[SetGameTime] = Field(
        ..., min_length=2, max_length=6,
        description="Games 2 and up, in order. Two entries make a best-of-3, four a best-of-5.",
    )
    allow_outside_round: bool = Field(
        False, description="Book a game outside its stage's booking window anyway.",
    )


class StreamCandidateRequest(BaseModel):
    flag: bool = Field(..., description="Set or clear the stream-candidate flag")


class AssignStageRequest(BaseModel):
    stage_id: Optional[int] = Field(None, description="Stage to assign, or null to clear")


class AssignStationsRequest(BaseModel):
    assignments: Dict[int, Optional[str]] = Field(
        ...,
        description=(
            "Map of player slot id (`players[].id` on the match) to station "
            "label, or null to clear that player's station."
        ),
    )


class StationSuggestionResponse(BaseModel):
    assignments: Dict[int, str] = Field(
        ...,
        description="Map of player slot id (`players[].id` on the match) to suggested station label.",
    )
    relaxations: List[str] = Field(
        default_factory=list,
        description=(
            "Seating rules the suggestion could not honour, as sentences to show "
            "the proctor. Empty when both players got opposite sides with no "
            "occupied neighbour."
        ),
    )


class RecordResultRequest(BaseModel):
    winner_id: int = Field(
        ..., description="The winner's player slot id (`players[].id` on the match), not their user id."
    )


class SetReviewRequest(BaseModel):
    needs_review: bool = Field(
        ..., description="True to flag the recorded result for admin review, false to clear it"
    )


class CrewSignupRequest(BaseModel):
    role: str = Field(..., description="The crew role to sign up for: 'commentator' or 'tracker'.")


class SeedResultResponse(BaseModel):
    message: str = Field(..., description="What happened, in a sentence to show the caller.")
    seed_url: Optional[str] = Field(
        None,
        description=(
            "Link to the new seed. Null when the randomizer rolls in the "
            "background, in which case the players are DMed when it lands."
        ),
    )


class MatchSuggestionResponse(BaseModel):
    suggested_at: datetime = Field(..., description="Suggested match start time (UTC)")
