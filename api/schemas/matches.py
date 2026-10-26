"""Response schemas for match-related endpoints."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from api.schemas.common import UserBase


class TournamentBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="The tournament's id.")
    name: str = Field(..., description="The tournament's display name.")
    description: Optional[str] = Field(None, description="Staff-written description of the tournament, in markdown.")
    seed_generator: Optional[str] = Field(
        None,
        description=(
            "Name of the randomizer the tournament rolls seeds with when it has no "
            "preset attached, or null when it rolls no seeds."
        ),
    )
    bracket_url: Optional[str] = Field(None, description="Link to the tournament's bracket, if it has one.")
    rules_url: Optional[str] = Field(None, description="Link to the tournament's rules, if it has one.")
    tournament_format: Optional[str] = Field(None, description="Free-text format label staff gave the tournament.")
    average_match_duration: Optional[int] = Field(None, description="Typical match length, in minutes.")
    max_match_duration: Optional[int] = Field(None, description="Longest a match is expected to run, in minutes.")


class StageBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="The stage's id.")
    name: str = Field(..., description="The stage's display name.")
    stream_url: Optional[str] = Field(None, description="Where the stage is streamed, if anywhere.")


class GeneratedSeedBase(BaseModel):
    """A rolled seed and its provenance.

    ``settings_snapshot`` is the settings as sent to the randomizer at roll time,
    which is what answers "what settings did this seed use?" after the preset has
    since been edited. Exposed on the authenticated match read only — a
    spoiler-adjacent setting is precisely what a viewer must not see mid-race.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="The seed's id.")
    seed_url: str = Field(..., description="Link to the seed on the randomizer's site.")
    seed_info: Optional[str] = Field(None, description="Extra text the randomizer returned about the seed, such as a hash.")
    randomizer: Optional[str] = Field(None, description="Name of the randomizer that rolled the seed.")
    settings_snapshot: Optional[dict] = Field(
        None,
        description=(
            "The settings sent to the randomizer when the seed was rolled. Later "
            "edits to the preset don't change it."
        ),
    )
    provider_meta: Optional[dict] = Field(
        None, description="What the roll took, such as attempts and timing, as reported by the seed provider."
    )
    created_at: datetime = Field(..., description="When the seed was rolled, in UTC.")


class PlayerInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(
        ...,
        description=(
            "Id of this player's slot in the match. Station assignments and "
            "`winner_id` use this id, not the user id."
        ),
    )
    user: UserBase = Field(..., description="The player.")
    finish_rank: Optional[int] = Field(
        None, description="Where the player placed, 1 for the winner, or null before a result is recorded."
    )
    assigned_station: Optional[str] = Field(None, description="The station the player sits at, or null when unassigned.")


class CommentatorInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="Id of the commentator signup.")
    user: UserBase = Field(..., description="The commentator.")
    approved: bool = Field(..., description="Whether the signup is approved. Match reads list approved crew only.")


class TrackerInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="Id of the tracker signup.")
    user: UserBase = Field(..., description="The tracker.")
    approved: bool = Field(..., description="Whether the signup is approved. Match reads list approved crew only.")


class BroadcasterInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="Id of the broadcaster signup.")
    user: UserBase = Field(..., description="The broadcaster.")
    approved: bool = Field(..., description="Whether the signup is approved. Match reads list approved crew only.")


class MatchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="The match's id.")
    tournament: TournamentBase = Field(..., description="The tournament the match belongs to.")
    title: Optional[str] = Field(None, description="Custom display title, or null to use the default of tournament and players.")
    stage: Optional[StageBase] = Field(None, description="The stage the match is played on, or null when it has none.")
    generated_seed: Optional[GeneratedSeedBase] = Field(None, description="The match's seed, or null before one is rolled.")
    scheduled_at: Optional[datetime] = Field(None, description="When the match is scheduled to start, in UTC.")
    seated_at: Optional[datetime] = Field(None, description="When the players were checked in (seated), in UTC.")
    finished_at: Optional[datetime] = Field(None, description="When the match was marked finished, in UTC.")
    comment: Optional[str] = Field(None, description="Free-text note on the match, from whoever scheduled or last edited it.")
    # The proctor's dispute flag. Confirming clears it.
    needs_review: bool = Field(
        False,
        description=(
            "True when a proctor flagged the recorded result for an admin to "
            "check. Confirming the match clears it."
        ),
    )
    players: List[PlayerInfo] = Field(default_factory=list, description="The match's players.")
    commentators: List[CommentatorInfo] = Field(default_factory=list, description="Approved commentators.")
    trackers: List[TrackerInfo] = Field(default_factory=list, description="Approved trackers.")
    broadcasters: List[BroadcasterInfo] = Field(
        default_factory=list,
        description="Approved broadcasters. Empty in an event without broadcasters.",
    )
    created_at: datetime = Field(..., description="When the match was created, in UTC.")
    updated_at: datetime = Field(..., description="When the match was last changed, in UTC.")
