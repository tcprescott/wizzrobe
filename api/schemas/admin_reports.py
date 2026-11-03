"""Schemas for the app's Admin › Reports screen."""

from datetime import date, datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class ReportEntryResponse(BaseModel):
    key: str = Field(..., description='The report, as `?report=` names it: `capacity`, `stages`, `audit`, ...')
    title: str
    icon: str = Field(..., description='Material Symbols icon name.')
    description: str


class RangeJumpResponse(BaseModel):
    label: str = Field(..., description='`Whole event`, or a day such as `Sat 10-24`.')
    start: date
    end: date


class ReportsMenuResponse(BaseModel):
    overview: bool = Field(..., description='Whether this viewer gets the Overview (a volunteer coordinator does not).')
    reports: List[ReportEntryResponse] = Field(..., description='The reports this viewer may open, in display order.')
    event_start: date = Field(..., description="The event's first day, the default range start.")
    event_end: date = Field(..., description="The event's last day, the default range end.")
    jumps: List[RangeJumpResponse] = Field(..., description='Range shortcuts: the whole event, then each day of a week-or-shorter event.')
    zone: str = Field(..., description='The IANA timezone the date parameters are read in and the report days follow.')


class OverviewKpiResponse(BaseModel):
    key: str
    label: str
    value: str = Field(..., description='The number as shown, e.g. `212 / 240` or `78%`.')
    detail: str = Field(
        ..., description='The line under it. For `peak_players`, the peak time as UTC ISO 8601 (empty when no matches).',
    )
    tone: str = Field(..., description='`neutral`, `ok`, `pending` or `bad`.')
    report: str = Field(..., description='The report this number links to.')


class CapacitySeriesResponse(BaseModel):
    interval_minutes: int = Field(..., description='Spacing between points: 15, 30 or 60 depending on the range.')
    max_capacity: int = Field(..., description='The configured player capacity, drawn as a line.')
    times: List[datetime] = Field(..., description='Each point (UTC).')
    players: List[int] = Field(..., description='Players at once at each point.')
    on_stream: List[int] = Field(..., description='Of those, players in matches on a stage.')
    off_stream: List[int] = Field(..., description='Of those, players in matches off stage.')


class StageBookingResponse(BaseModel):
    match_id: int
    tournament_name: str
    start: datetime = Field(..., description='Booking start, clipped to the range (UTC).')
    end: datetime = Field(..., description='Booking end, clipped to the range (UTC).')


class StageBookingsResponse(BaseModel):
    stages: Dict[int, List[StageBookingResponse]] = Field(..., description='Bookings per stage id, in start order.')


class VolunteerCoverageRow(BaseModel):
    shift_id: int
    position: str
    label: str
    starts_at: datetime
    ends_at: datetime
    filled: int = Field(..., description='Assigned, drafts included.')
    needed: int
    understaffed: bool
    drafts: int
    acknowledged: int


class ExportableTournament(BaseModel):
    id: int
    name: str
    active: bool
    test: bool
    players: int
    matches: int
    finished: int
    bracket_site: Optional[str] = Field(None, description='Where its bracket lives, when it has one.')


class ActivityExtentResponse(BaseModel):
    first: Optional[date] = Field(None, description='Earliest local date with any activity; null with no history.')
    last: Optional[date] = Field(None, description='Latest local date with any activity; null with no history.')


class TelemetryEventRow(BaseModel):
    id: int
    created_at: datetime
    user_id: Optional[int] = None
    user_name: Optional[str] = Field(None, description="The person's display name, when the row has one.")
    category: str = Field(..., description='`page`, `interaction`, `event`, ...')
    event_type: str
    path: Optional[str] = None
    details: Optional[str] = Field(None, description='The stored details as text (usually JSON).')


class TelemetryEventPage(BaseModel):
    total: int = Field(..., description='Events matching the filters, across all pages.')
    limit: int
    offset: int
    items: List[TelemetryEventRow]


class AuditChangeResponse(BaseModel):
    field: str
    before: Optional[str] = None
    after: Optional[str] = None


class AuditFact(BaseModel):
    label: str
    value: str


class AuditRowResponse(BaseModel):
    id: int
    created_at: datetime
    actor_id: Optional[int] = None
    actor_name: str = Field(..., description='Who did it, or `Member #<id>` for a removed account.')
    action: str = Field(..., description='The raw `verb.object` action.')
    label: str = Field(..., description='The action in words, e.g. `Match rescheduled`.')
    area: Optional[str] = Field(None, description='The area it belongs to, e.g. `Matches`.')
    subject: str = Field('', description='What it was about, e.g. `Match #412` or a member name.')
    summary: str = Field('', description='One line of what happened.')
    changes: List[AuditChangeResponse] = Field(default_factory=list, description='Before/after per changed field.')
    changed_fields: List[str] = Field(default_factory=list, description='Fields changed when values were not recorded.')
    facts: List[AuditFact] = Field(default_factory=list, description='Other details, labelled.')
    raw: str = Field('', description='The stored details, pretty-printed.')
    match_id: Optional[int] = None


class AuditAreaResponse(BaseModel):
    key: str
    label: str


class AuditPageResponse(BaseModel):
    total: int
    limit: int
    offset: int
    areas: List[AuditAreaResponse] = Field(..., description='The Area filter choices.')
    items: List[AuditRowResponse]
