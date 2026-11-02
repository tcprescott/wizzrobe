"""Schemas for the app's Admin → Check-in Roster screen."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from models import CheckInEventStatus, CheckInTierLanyard


class OptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    value: str
    label: str


class CheckInAdminEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    status: str = Field(..., description="`draft`, `open` or `closed`.")
    matcherino_venue_id: Optional[int] = Field(None, description="Empty for a walk-up or comps-only event.")
    comp_roles: List[str] = Field(..., description="Roles whose holders are comped.")
    comp_volunteers: bool
    sync_interval_minutes: int
    last_synced_at: Optional[datetime] = None
    last_sync_error: Optional[str] = None
    last_sync_count: Optional[int] = None


class MatcherinoLoginResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    configured: bool = Field(..., description="A Matcherino login is saved.")
    matcherino_user_id: Optional[str] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None
    alert_user_id: Optional[int] = Field(None, description="Who gets a DM when Matcherino refuses the login.")
    alert_user: Optional[str] = None


class CheckInBoardResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    events: List[CheckInAdminEventResponse]
    login: MatcherinoLoginResponse
    comp_role_options: List[OptionResponse]
    lanyard_options: List[OptionResponse]
    volunteer_comp_hours: Optional[float] = Field(
        None, description="The hours a volunteer needs for a comp; null when no comp tier is set.",
    )


class CheckInEventWrite(BaseModel):
    name: str
    matcherino_venue_id: Optional[int] = Field(None, ge=1, description="Leave empty for a walk-up-only event.")
    status: CheckInEventStatus = CheckInEventStatus.DRAFT
    sync_interval_minutes: int = Field(5, ge=1, le=120)
    comp_roles: List[str] = Field(default_factory=list)
    comp_volunteers: bool = False


class CheckInTierResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    amount_cents: int
    lanyard: str


class LanyardUpdate(BaseModel):
    lanyard: CheckInTierLanyard


class VenuePreviewResponse(BaseModel):
    venue_id: int
    title: str
    badge_types: List[str]


class MatcherinoLoginWrite(BaseModel):
    refresh_token: str = Field(..., description="The refresh token copied from a signed-in Matcherino tab.")


class AlertUserWrite(BaseModel):
    user_id: Optional[int] = Field(None, description="A staff member, or null for nobody.")


class PersonOption(BaseModel):
    id: int
    name: str
