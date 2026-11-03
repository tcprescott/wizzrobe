"""Schemas for ``/api/event-settings``: one group per pane of Admin → Event settings."""

from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from models import StationFormat, StationSide


class DayHours(BaseModel):
    day: date
    opens: Optional[str] = Field(None, description="`HH:MM` on the event's clock, or null for any time.")
    closes: Optional[str] = Field(None, description="`HH:MM` on the event's clock, or null for any time.")


class EventDatesResponse(BaseModel):
    starts_on: Optional[date] = Field(None, description="Null when staff left it blank.")
    ends_on: Optional[date] = None
    days: List[DayHours] = Field(
        ..., description="Every day the event spans, from the dates or (when blank) its first and last match.",
    )
    timezone_label: str = Field(..., description="The clock the hours are on, the event's own.")


class EventDatesUpdate(BaseModel):
    starts_on: Optional[date] = None
    ends_on: Optional[date] = None


class EventHoursUpdate(BaseModel):
    days: List[DayHours] = Field(..., description="Days left out, or with either time blank, allow any time.")


class CapacitySettings(BaseModel):
    max_players: Optional[int] = Field(None, ge=1, description="Players at once. Null uses 60.")
    max_stages: Optional[int] = Field(None, ge=1, description="Stages at once. Null uses the number of active stages.")


class EventTimezoneResponse(BaseModel):
    follows_community: bool
    mode: str = Field(..., description="This event's own mode when it has one, else the community's.")
    name: str
    community_mode: str
    community_name: str
    options: List[str]


class EventTimezoneUpdate(BaseModel):
    follows_community: bool
    mode: Optional[str] = Field(None, description="`pinned` or `user`; needed unless following the community.")
    name: Optional[str] = None


class CrewSettings(BaseModel):
    broadcasters: bool


class RewardTiersUpdate(BaseModel):
    text: str = Field(..., description="Hour totals separated by commas. Blank uses 8, 12, 16; `0` awards nothing.")


class RewardTiersResponse(BaseModel):
    text: str = Field(..., description="What staff saved, normalised; blank when it uses the default.")
    tiers: List[float] = Field(..., description="The tiers in effect, ascending.")


class StationFormatOption(BaseModel):
    value: StationFormat
    label: str


class StationFormatSettings(BaseModel):
    format: StationFormat


class StationFormatResponse(StationFormatSettings):
    options: List[StationFormatOption]


class StationResponse(BaseModel):
    id: int
    name: str
    section: Optional[str] = None
    side: Optional[StationSide] = None
    position: Optional[int] = None
    sort_order: int
    is_active: bool


class StationCreate(BaseModel):
    name: str = Field(..., max_length=50)
    section: Optional[str] = Field(None, max_length=50)
    side: Optional[StationSide] = None
    position: Optional[int] = None
    sort_order: int = 0


class StationUpdate(BaseModel):
    """Fields left out stay as they are; ``side`` or ``position`` sent as null clears it."""

    name: Optional[str] = Field(None, max_length=50)
    section: Optional[str] = Field(None, max_length=50)
    side: Optional[StationSide] = None
    position: Optional[int] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None


class RoomScreenResponse(BaseModel):
    id: int
    label: str
    revoked: bool
    last_used_at: Optional[datetime] = None
    issued_by: Optional[str] = Field(None, description="Null when the account that issued it has gone.")


class RoomScreenCreate(BaseModel):
    label: str = Field(..., max_length=100)


class RoomScreenIssued(BaseModel):
    screen: RoomScreenResponse
    url: str = Field(..., description="The machine's URL. Shown once: only its hash is stored.")
