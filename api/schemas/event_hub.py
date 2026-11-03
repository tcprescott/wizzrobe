"""Schemas for the app's Event tab: the hub, the run sheet and Watch."""

from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class SideResponse(_Base):
    name: str = Field(..., description="A player's name, or what fills an undecided side, like \"Winner of A3\".")
    undecided: bool = Field(False, description="True while a bracket hasn't decided who plays this side.")


class CrewSlotResponse(_Base):
    role: str = Field(..., description="`commentator`, `tracker` or `broadcaster`: what `POST /matches/{id}/crew` takes.")
    label: str = Field(..., description="Short label to print, like \"Comm\".")
    needed: int = Field(..., description="How many the match wants. 0 for a match off stage that still has someone signed up.")
    filled: int = Field(..., description="Approved signups.")
    names: List[str] = Field(..., description="Who's approved.")
    yours: Optional[str] = Field(None, description="Your own signup: `pending`, `approved`, or null.")
    can_sign_up: bool = Field(False, description="Whether you can sign up for this role now.")


class RunSheetRowResponse(_Base):
    match_id: int
    scheduled_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    status: str = Field(..., description="`scheduled`, `checked_in`, `live` or `finished`.")
    tournament_id: Optional[int] = None
    tournament: str
    round: str = Field(..., description="The match's title, or its bracket stage and game, ready to print. May be empty.")
    sides: List[SideResponse]
    stage: str = Field(..., description="The stage it runs on. Empty when it isn't on one.")
    stage_id: Optional[int] = None
    stations: List[str] = Field(..., description="The players' stations, once assigned.")
    streamed: bool = Field(False, description="The stage has a stream.")
    crew: List[CrewSlotResponse]
    playing: bool = Field(False, description="You play in it.")
    watching: bool = Field(False, description="You watch it (`POST`/`DELETE /matches/{id}/watch`).")


class StreamMatchResponse(_Base):
    match_id: int
    scheduled_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    tournament_id: Optional[int] = None
    tournament: str
    sides: List[SideResponse]
    stage: str = Field('', description="The stage, in the coming-up list.")


class StageCardResponse(_Base):
    id: int
    name: str
    stream_url: Optional[str] = None
    channel: Optional[str] = Field(None, description="The Twitch channel, lowercased, for an embed.")
    channel_name: Optional[str] = Field(None, description="The Twitch channel as spelled, for showing.")
    on_now: Optional[StreamMatchResponse] = Field(None, description="The match seated or playing there now.")
    up_next: List[StreamMatchResponse] = Field(..., description="The next two there, within a day.")


class TournamentCardResponse(_Base):
    id: int
    name: str
    format: str
    status: str = Field(..., description="Bracket status to print, like \"Bracket underway\". May be empty.")
    tone: str = Field(..., description="`live`, `ok`, `pending` or `neutral`, for the status chip.")
    has_bracket: bool = Field(..., description="`GET /tournaments/{id}/bracket` has something to show.")
    bracket_url: Optional[str] = Field(None, description="The bracket on an outside site, when there's no bracket here.")
    rules_url: Optional[str] = None
    prize_total: Optional[Decimal] = Field(None, description="Prize pool plus bonus, when Payouts is on and there is one.")


class EventHubResponse(_Base):
    zone: Optional[str] = Field(None, description="The clock to print times on: the community's pinned zone, else your saved one, else null for the device's.")
    stages: List[StageCardResponse]
    up_next: List[RunSheetRowResponse]
    tournaments: List[TournamentCardResponse]


class WatchResponse(_Base):
    zone: Optional[str] = Field(None, description="The clock to print times on: the community's pinned zone, else your saved one, else null for the device's.")
    stages: List[StageCardResponse]
    coming_up: List[StreamMatchResponse] = Field(..., description="Streamed matches after the stage cards' ones, up to a week out.")
    tournaments: List[TournamentCardResponse]


class RunSheetResponse(_Base):
    zone: Optional[str] = Field(None, description="The clock to print times on: the community's pinned zone, else your saved one, else null for the device's.")
    rows: List[RunSheetRowResponse]
