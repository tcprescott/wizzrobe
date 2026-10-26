"""Request schemas for user & role write actions."""

from typing import List, Optional

from pydantic import BaseModel, Field

from models import Role


class UserCreateRequest(BaseModel):
    username: str = Field(..., description="The account's username. Can't be blank.")
    discord_id: int = Field(
        ...,
        description=(
            "Discord user ID. Required, and must not already belong to another Wizzrobe account; "
            "the person signs in with it."
        ),
    )
    display_name: Optional[str] = Field(
        None, description="The name to show the person by. Can't contain links or Discord mentions.",
    )
    pronouns: Optional[str] = Field(
        None, description="The person's pronouns. Can't contain links or Discord mentions.",
    )


class UserSelfUpdate(BaseModel):
    display_name: Optional[str] = Field(
        None,
        description=(
            "The name to be shown by, without links or Discord mentions. An empty string "
            "clears it. Omit to leave it unchanged."
        ),
    )
    pronouns: Optional[str] = Field(
        None,
        description=(
            "Your pronouns, without links or Discord mentions. An empty string clears them. "
            "Omit to leave them unchanged."
        ),
    )
    dm_notifications: Optional[bool] = Field(
        None, description="Whether to get notifications as Discord DMs. Omit to leave it unchanged.",
    )


class UserProfileUpdate(BaseModel):
    display_name: Optional[str] = Field(
        None,
        description=(
            "The name to show the person by, without links or Discord mentions. An empty "
            "string clears it. Omit to leave it unchanged."
        ),
    )
    pronouns: Optional[str] = Field(
        None,
        description=(
            "The person's pronouns, without links or Discord mentions. An empty string "
            "clears them. Omit to leave them unchanged."
        ),
    )


class UserAdminUpdate(BaseModel):
    is_active: Optional[bool] = Field(
        None,
        description=(
            "Set false to deactivate the account, which locks it out of every community. "
            "Omit to leave it unchanged."
        ),
    )


class RoleRequest(BaseModel):
    role: Role = Field(
        ...,
        description=(
            "The community role to grant. One of: staff, proctor, stream_manager, triforce_submitter, volunteer_coordinator, equipment_manager, volunteer, check_in_desk, preset_manager, sync_admin, qualifier_admin. "
            "super_admin is refused here."
        ),
    )
    event: Optional[str] = Field(
        None,
        description=(
            "The event slug a per-event role is granted in (proctor, stream_manager, "
            "triforce_submitter, volunteer_coordinator, equipment_manager, volunteer, "
            "check_in_desk). Omitted, it's the community's default event. Ignored for "
            "staff and the online admin roles, which cover every event."
        ),
    )


class TournamentEnrollmentUpdate(BaseModel):
    tournament_ids: List[int] = Field(
        ...,
        description=(
            "The full set of tournament IDs the user should be enrolled in. Tournaments left out "
            "are withdrawn from, and an empty list withdraws from all of them."
        ),
    )
