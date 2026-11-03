"""Schemas for the app's Admin → Members screen."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from models import AccommodationStatus


class MemberRoleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    role: str = Field(..., description="The role's value, as `POST /users/{id}/roles` takes it.")
    label: str = Field(..., description="The role's name as people read it.")
    event_name: Optional[str] = Field(
        None, description="Set when the role is held for another event than the one asked about.",
    )


class MemberAdaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    request_id: int
    status: str = Field(..., description="`new`, `acknowledged` or `arranged`.")
    label: str
    changed_since_arranged: bool = Field(..., description="The member edited an arranged request; staff haven't reviewed it.")


class MemberRowResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: Optional[str] = None
    preferred_name: str = Field(..., description="Display name, else username.")
    pronouns: Optional[str] = None
    avatar_url: Optional[str] = None
    is_active: bool = Field(..., description="False when the account is deactivated platform-wide.")
    challonge: Optional[str] = Field(None, description="Linked Challonge name or id.")
    roles: List[MemberRoleResponse]
    tournaments_admin: int = Field(..., description="Tournaments here they admin.")
    tournaments_crew_coordinator: int = Field(..., description="Tournaments here they coordinate crew for.")
    joined_at: Optional[datetime] = None
    joined_via: Optional[str] = Field(None, description="How they joined, ready to print.")
    joined_recently: bool = Field(..., description="Joined in the last 7 days.")
    ada: Optional[MemberAdaResponse] = Field(None, description="Their open ADA request. Only when the community takes them.")
    patron_badge: Optional[str] = Field(None, description="Their Patreon patron badge label, unless they hide it. Null when Patreon is off.")


class MembersSwitchesResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    accommodations: bool = Field(..., description="ADA requests are on: show the ADA column and tab.")
    emergency_contacts: bool = Field(..., description="Emergency contacts are on: offer Emergency contact… on a row.")
    challonge: bool = Field(..., description="Challonge is on: show the Challonge column.")


class RoleOptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    role: str
    label: str
    per_event: bool = Field(..., description="Granted for one event (the one asked about) rather than the whole community.")


class MembersBoardResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    members: List[MemberRowResponse]
    join_requests: int = Field(..., description="Join requests waiting on staff.")
    ada_action_needed: int = Field(..., description="ADA requests staff still have to act on.")
    switches: MembersSwitchesResponse
    zone: Optional[str] = Field(None, description="The zone to print times in; null means the device's own.")
    role_options: List[RoleOptionResponse] = Field(..., description="The roles staff can grant here, for Add role.")


class AddableAccountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    preferred_name: str
    avatar_url: Optional[str] = None


class DiscordImportPreviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    server_name: str
    people: int = Field(..., description="People in the Discord server, bots left out.")
    new_accounts: int = Field(..., description="Accounts the import would create.")
    new_members: int = Field(..., description="People the import would add here.")
    already_members: int
    bots: int
    removed: int = Field(..., description="People removed from this community, who stay out.")


class DiscordImportResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    accounts_created: int
    members_added: int
    already_members: int
    bots: int
    removed: int


class EmergencyContactViewResponse(BaseModel):
    found: bool = Field(..., description="False when the member hasn't given one. The look is logged either way.")
    name: Optional[str] = None
    relationship: Optional[str] = None
    phone: Optional[str] = None
    updated_at: Optional[datetime] = None


class AdaRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    preferred_name: str
    username: str
    status: str = Field(..., description="`new`, `acknowledged`, `arranged` or `withdrawn`.")
    label: str
    details: Optional[str] = Field(None, description="What the member asked for, in their words.")
    staff_notes: Optional[str] = Field(None, description="Staff-only notes.")
    changed_since_arranged: bool
    arranged_details: Optional[str] = Field(None, description="What staff arranged against, when the member has since changed it.")
    created_at: datetime
    updated_at: datetime


class AdaRequestUpdate(BaseModel):
    status: AccommodationStatus = Field(..., description="`new`, `acknowledged` or `arranged`. Only the member can withdraw.")
    staff_notes: Optional[str] = Field(None, description="Staff-only notes; null clears them.")


class StaffMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000, description="The text of the DM.")
