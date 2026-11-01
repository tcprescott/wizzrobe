"""Schemas for a player's own Home and Schedule reads, and booking a time."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ScheduleEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: str = Field(..., description="`match` (you play), `crew` (you commentate, track or broadcast) or `shift` (volunteer).")
    starts_at: datetime
    ends_at: datetime
    title: str = Field(..., description="`vs <opponent>` for a match, the crew role, or the shift's position.")
    detail: str = Field(..., description="Tournament, matchup or shift label, ready to print.")
    match_id: Optional[int] = None
    assignment_id: Optional[int] = Field(None, description="The volunteer assignment, for a shift.")
    status: Optional[str] = Field(None, description="For a match: `scheduled`, `checked_in`, `live` or `finished`.")
    stage: Optional[str] = Field(None, description="The stage the match runs on, if any.")
    station: Optional[str] = Field(None, description="Your station, once assigned.")
    room_url: Optional[str] = Field(None, description="The open racetime.gg room, if the match has one.")
    needs_acknowledgment: bool = Field(False, description="You still need to confirm you've seen this match's time.")
    late_minutes: Optional[int] = Field(None, description="How late you said you'd be.")
    can_report_late: bool = Field(False, description="Whether `POST /matches/{match_id}/running-late` would take a heads-up now.")
    approved: Optional[bool] = Field(None, description="For a crew slot: whether staff approved it.")
    checked_in: bool = Field(False, description="For a shift: whether you've checked in.")


class BookableResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source: str = Field(..., description="`bracket` (run in Wizzrobe), `challonge` or `startgg`.")
    key: int = Field(..., description="The matchup's id within its source.")
    tournament_id: int
    tournament: str
    source_label: Optional[str] = Field(None, description="The bracket site's name, when it isn't Wizzrobe.")
    opponent: str
    round_label: str
    window_text: Optional[str] = Field(None, description="When this round may be booked, ready to print.")
    blocked: Optional[str] = Field(None, description="Why it can't be booked yet, when it can't.")
    if_needed: bool = Field(False, description="A set game that's only played if the set gets that far.")
    best_of: int = 1


class NeedResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: str = Field(..., description="`book` (pick a time), `agree` (your opponent asked to move or call off a match) or `acknowledge` (confirm you've seen a match's time).")
    title: str
    detail: str
    match_id: Optional[int] = None
    request_id: Optional[int] = Field(None, description="The reschedule request, for `agree`.")
    bookable: Optional[BookableResponse] = Field(None, description="The matchup, for `book`.")
    proposed_at: Optional[datetime] = None
    original_at: Optional[datetime] = None
    reason: Optional[str] = None
    agreed: bool = Field(False, description="For `agree`: you already agreed and staff haven't decided yet.")


class HomeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    zone: Optional[str] = Field(None, description="The timezone to show times in. Null means use the device's own.")
    next_up: Optional[ScheduleEntryResponse] = Field(None, description="Your next match that hasn't finished.")
    needs_you: List[NeedResponse]
    today: List[ScheduleEntryResponse] = Field(..., description="The rest of today, without `next_up`.")


class ScheduleResponse(BaseModel):
    zone: Optional[str] = Field(None, description="The timezone to show times in. Null means use the device's own.")
    items: List[ScheduleEntryResponse]


class TimeCheckResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    refusal: Optional[str] = Field(None, description="Why booking at this time would be refused, if it would.")
    clashes: List[str] = Field(..., description="Reasons the time might not work. Booking anyway is allowed.")


class BookRequest(BaseModel):
    scheduled_at: datetime = Field(..., description="When to play, as an ISO 8601 instant with an offset.")


class BookedResponse(BaseModel):
    match_id: int
    scheduled_at: datetime


class RunningLateRequest(BaseModel):
    minutes: int = Field(..., description="How late you expect to be: 10, 20 or 30 minutes.")
    note: Optional[str] = Field(None, max_length=280)
