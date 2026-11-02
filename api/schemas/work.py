"""Schemas for the app's Work tab: your areas, the volunteer schedule grid and the desk."""

from datetime import date, datetime
from typing import Dict, Iterable, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from api.schemas.check_in import CheckInEntrantResponse, CheckInEventResponse
from application.services.check_in_rules import active_badges
from models import CheckInEntrant


class DeskAreaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    open_events: int = Field(..., description="Check-in events open right now.")
    event_id: Optional[int] = Field(None, description="The open event, when exactly one is open; the desk opens straight on it.")
    event_name: str = Field('', description="That event's name.")
    checked_in: int = Field(0, description="People checked in at that event.")
    expected: int = Field(0, description="People on its roster who still hold a badge or comp, plus anyone already checked in.")


class ScheduleAreaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    on_shift_now: int = Field(..., description="Volunteers on a published shift right now.")
    my_next_shift_at: Optional[datetime] = Field(None, description="When your next shift starts (UTC), if you have one coming.")


class AvailabilityAreaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    windows: int = Field(..., description="How many availability windows you've saved.")


class WorkAreasResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    zone: Optional[str] = Field(None, description="The clock to print times on; null for the device's own.")

    desk: Optional[DeskAreaResponse] = Field(None, description="The check-in desk; null when you can't work it.")
    schedule: Optional[ScheduleAreaResponse] = Field(None, description="The volunteer schedule; null when you can't see it.")
    availability: Optional[AvailabilityAreaResponse] = Field(None, description="Your volunteer availability; null when you don't volunteer here.")
    proctor: bool = Field(False, description="Whether you can open Proctor Station.")


class GridPersonResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    is_viewer: bool = Field(..., description="This is you.")
    called_off: bool = Field(False, description="Staff sent them home from this shift.")


class GridShiftResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    starts_at: datetime
    ends_at: datetime
    label: str = Field('', description="The shift's own label, if it has one.")
    start_hour: float = Field(..., description="Hours after the day's local midnight the block starts (clipped to the grid).")
    end_hour: float = Field(..., description="Hours after the day's local midnight the block ends (clipped to the grid).")
    lane: int = Field(0, description="Which side-by-side lane this block sits in when shifts overlap.")
    lanes: int = Field(1, description="How many lanes its group of overlapping shifts shares.")
    slots_needed: int
    open_slots: int = Field(..., description="Places still unfilled.")
    people: List[GridPersonResponse]


class GridColumnResponse(BaseModel):
    position_id: int
    position: str
    shifts: List[GridShiftResponse]


class GridRoleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    position_id: int
    name: str
    visible: bool


class VolunteerScheduleResponse(BaseModel):
    zone: Optional[str] = Field(None, description="The clock to print times on; null for the device's own.")
    days: List[date] = Field(..., description="The event's days, in order.")
    day: Optional[date] = Field(None, description="The day shown; null when the event has no dates yet.")
    midnight: Optional[datetime] = Field(None, description="That day's local midnight (UTC). Hours in the grid count from here.")
    first_hour: int = Field(0, description="The first hour row (hours after midnight; can pass 24 for overnight).")
    last_hour: int = Field(0, description="The hour the grid ends.")
    columns: List[GridColumnResponse] = Field(default_factory=list, description="One column per role shown, in your order.")
    roles: List[GridRoleResponse] = Field(..., description="Every role you can show, in your order, and whether it's shown.")
    hidden_with_shifts: int = Field(0, description="Roles with shifts that day that you've hidden.")
    customized: bool = Field(False, description="Your columns differ from the community's default order.")


class ScheduleColumnRequest(BaseModel):
    position_id: int
    visible: bool = True


class ScheduleColumnsRequest(BaseModel):
    columns: List[ScheduleColumnRequest] = Field(
        ..., max_length=64,
        description="The roles in the order to show them, each shown or hidden. Roles left out go at the end, shown.",
    )


class DeskRowResponse(CheckInEntrantResponse):
    badge_codes: List[str] = Field(default_factory=list, description="Codes of the badges they hold, for the desk search.")
    username: Optional[str] = Field(None, description="The linked member's Discord username, for the desk search.")
    has_discord: bool = Field(False, description="The linked account has Discord, so the check-in DM can be re-sent.")
    suggestions: int = Field(0, description="Likely accounts for an unlinked row (`GET /check-in/entrants/{id}/suggestions`).")
    signed_in_with: Optional[str] = Field(None, description="How they signed in to Matcherino (`discord`, `twitch`, `google`…), to help the desk find their account.")

    @classmethod
    def from_desk_row(
        cls, entrant: CheckInEntrant, volunteer_ids: Iterable[int], suggestions: Dict[int, int],
    ) -> 'DeskRowResponse':
        base = CheckInEntrantResponse.from_row(entrant, volunteer_ids)
        user = entrant.user
        return cls(
            **base.model_dump(),
            badge_codes=[b.code for b in active_badges(entrant.passes) if b.code],
            username=user.username if user else None,
            has_discord=bool(user and user.discord_id),
            suggestions=suggestions.get(entrant.id, 0),
            signed_in_with=entrant.auth_provider or None,
        )


class DeskResponse(BaseModel):
    zone: Optional[str] = Field(None, description="The clock to print times on; null for the device's own.")
    event: CheckInEventResponse
    can_manage: bool = Field(..., description="Staff: you may add walk-ups, remove them, and check in someone without a badge.")
    rows: List[DeskRowResponse]


class DeskMemberResponse(BaseModel):
    id: int
    name: str = Field(..., description="Their display name.")
    username: Optional[str] = Field(None, description="Their Discord username.")
