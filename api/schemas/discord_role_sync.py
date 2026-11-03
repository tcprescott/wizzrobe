"""Schemas for the Discord Roles from Wizzrobe pane of Admin → Event settings (api/routers/discord_role_sync.py).

Discord ids here are strings: a JSON number can't hold a Discord snowflake
exactly, and these endpoints exist for a browser client.
"""

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from models import CheckInTierLanyard, DiscordRoleRuleKind


class RoleSyncRole(BaseModel):
    """A role in the linked Discord server, and whether the bot can hand it out."""

    id: str = Field(..., description="ID of the Discord role, as a string.")
    name: str = Field(..., description="Name of the role in Discord.")
    color: Optional[str] = Field(None, description="The role's colour as `#rrggbb`, or null for Discord's default.")
    problem: Optional[str] = Field(
        None, description="Why the bot can't give this role, with the fix. Null when it can.",
    )


class RoleSyncLine(BaseModel):
    """One line of the checklist: who it covers, and the role given to them, if any."""

    group: Literal['players', 'badges', 'tournaments'] = Field(..., description="Which group the line sits under.")
    kind: DiscordRoleRuleKind = Field(..., description="Who the rule covers.")
    tournament_id: Optional[int] = Field(None, description="The tournament, for a `tournament` line.")
    lanyard: Optional[CheckInTierLanyard] = Field(None, description="The badge, for a `badge` line.")
    label: str = Field(..., description="The line's title, for example `In a match right now` or a tournament's name.")
    caption: Optional[str] = Field(None, description="A short note under the title, or null.")
    active: bool = Field(..., description="False for an inactive tournament that still has a role.")
    discord_role_id: Optional[str] = Field(None, description="The role given, as a string. Null when the line is off.")
    discord_role_name: Optional[str] = Field(None, description="The role's name when it was picked. Null when the line is off.")
    holding: Optional[int] = Field(None, description="How many people covered by the line held the role at the last sync.")
    problem: Optional[str] = Field(
        None, description="Why the last sync skipped this rule, with the fix. Null when it works.",
    )


class RoleSyncUnreachable(BaseModel):
    """Someone a rule covers who can't get the role."""

    name: str = Field(..., description="The person's display name.")
    reason: Literal['no_discord', 'not_in_server'] = Field(
        ..., description="`no_discord`: no Discord account linked. `not_in_server`: not in the linked server.",
    )


class RoleSyncOverviewResponse(BaseModel):
    """Everything the pane shows."""

    last_synced_at: Optional[datetime] = Field(None, description="When the last finished sync ran (UTC). Null before the first.")
    last_error: Optional[str] = Field(None, description="Why the last sync stopped, or null when it finished.")
    in_place: int = Field(..., description="How many roles Wizzrobe has given and still holds in place.")
    unreachable: List[RoleSyncUnreachable] = Field(..., description="People a rule covers who can't get the role (capped).")
    roles: List[RoleSyncRole] = Field(
        ..., description="The server's roles, highest first, the ones the bot can give before the ones it can't.",
    )
    roles_error: Optional[str] = Field(None, description="Why the server's roles couldn't be read. Rules can't change until they can.")
    lines: List[RoleSyncLine] = Field(..., description="Every line a role can be set on, in display order.")


class RoleSyncRuleUpdate(BaseModel):
    """Set or turn off the role for one line."""

    kind: DiscordRoleRuleKind = Field(..., description="Who the rule covers.")
    tournament_id: Optional[int] = Field(None, description="Required for `tournament`: the tournament in this event.")
    lanyard: Optional[CheckInTierLanyard] = Field(None, description="Required for `badge`: which badge.")
    discord_role_id: Optional[str] = Field(
        None, description="The Discord role to give, as a string, or null to turn the line off.",
    )


class RoleSyncResultResponse(BaseModel):
    """What a sync did."""

    added: int = Field(..., description="Roles given.")
    removed: int = Field(..., description="Roles taken back.")
    in_place: int = Field(..., description="Roles already where they belong.")
    error: Optional[str] = Field(None, description="Why the sync stopped, or null when it finished.")
