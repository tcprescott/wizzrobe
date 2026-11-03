"""Schemas for the Discord pane of Admin → Settings (api/routers/discord_settings.py).

Discord ids here are strings: a JSON number can't hold a Discord snowflake
exactly, and these endpoints exist for a browser client.
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from models import Role, TournamentGrant


class DiscordLinkResponse(BaseModel):
    """Which Discord server the community is linked to, and what the caller may change here."""

    linked: bool = Field(..., description="Whether a Discord server is linked to this community.")
    guild_id: Optional[str] = Field(None, description="The linked server's Discord id, as a string. Null when none is linked.")
    server_name: Optional[str] = Field(
        None, description="The linked server's name. Null when none is linked or the bot can't reach it right now."
    )
    reachable: bool = Field(..., description="Whether the bot can see the linked server right now. False when none is linked.")
    can_manage_roles: bool = Field(..., description="Whether the caller may connect, disconnect and edit role mappings (Staff).")
    can_manage_events: bool = Field(..., description="Whether the caller may change the Discord events mirror (Staff or Sync admin).")


class DiscordMappingRow(BaseModel):
    """One Discord role mapping, labelled the way the pane shows it."""

    id: int = Field(..., description="ID of the mapping. Remove it with `DELETE /api/discord-role-mappings/{mapping_id}`.")
    discord_role_id: str = Field(..., description="ID of the Discord role, as a string.")
    discord_role_name: str = Field(..., description="Name of the Discord role, as recorded when the mapping was made.")
    grants: str = Field(..., description="What holding the role grants, for example `Proctor` or `Tournament Admin`.")
    applies_to: str = Field(
        ..., description="Where it applies: `Community-wide`, an event's name, or a tournament's name."
    )
    grants_nothing: bool = Field(..., description="True when a per-event grant's event is gone, so the mapping does nothing.")


class DiscordRoleOption(BaseModel):
    id: str = Field(..., description="ID of the Discord role, as a string.")
    name: str = Field(..., description="Name of the Discord role.")
    color: int = Field(..., description="The role's colour as a 24-bit RGB integer. 0 means Discord's default grey.")


class DiscordGrantOption(BaseModel):
    value: str = Field(..., description="The role or tournament grant's value.")
    label: str = Field(..., description="How the pane names it.")
    kind: Literal['community', 'event', 'tournament'] = Field(
        ..., description="Where it lands: across the community, in one event, or on one tournament."
    )


class DiscordPick(BaseModel):
    id: int = Field(..., description="ID.")
    name: str = Field(..., description="Name.")


class DiscordMappingOptionsResponse(BaseModel):
    """What the Add mappings dialog offers."""

    discord_roles: List[DiscordRoleOption] = Field(
        ..., description="Roles in the linked server the bot can hand out, sorted by name."
    )
    grants: List[DiscordGrantOption] = Field(..., description="What a mapping can grant.")
    tournaments: List[DiscordPick] = Field(..., description="Active tournaments a tournament grant can land on.")
    events: List[DiscordPick] = Field(..., description="Events a per-event role can land in, archived ones left out.")
    default_event_id: Optional[int] = Field(None, description="The community's default event, preselected in the dialog.")


class DiscordMappingsAddRequest(BaseModel):
    """Map every picked Discord role onto every picked grant."""

    discord_roles: List[DiscordRoleOption] = Field(
        ..., min_length=1, description="The Discord roles to map. `color` is ignored."
    )
    app_roles: List[Role] = Field(default_factory=list, description="Roles to grant. `super_admin` is refused.")
    tournament_grants: List[TournamentGrant] = Field(
        default_factory=list, description="Per-tournament authority to grant. Needs `tournament_id`."
    )
    tournament_id: Optional[int] = Field(None, description="The tournament the tournament grants land on.")
    event_id: Optional[int] = Field(
        None, description="The event per-event roles land in. Null uses the community's default event."
    )


class DiscordMappingsAddResponse(BaseModel):
    created: int = Field(..., description="Mappings created.")
    skipped: int = Field(..., description="Pairs that already existed, left as they were.")


class DiscordSyncChange(BaseModel):
    name: str = Field(..., description="The person's display name.")
    granted: List[str] = Field(..., description="What they gained.")
    revoked: List[str] = Field(..., description="What they lost.")


class DiscordSyncResponse(BaseModel):
    """What one Sync everyone did."""

    users_processed: int = Field(..., description="People checked.")
    users_created: int = Field(..., description="Accounts created for server members who had never signed in.")
    granted: int = Field(..., description="Grants added across everyone.")
    revoked: int = Field(..., description="Grants taken away across everyone.")
    skipped: int = Field(..., description="People who couldn't be checked, usually because Discord didn't answer.")
    created: List[str] = Field(..., description="Display names of the accounts created.")
    changed: List[DiscordSyncChange] = Field(..., description="Whose grants changed, and how.")


class DiscordMirrorTournament(BaseModel):
    id: int = Field(..., description="Tournament id. Switch it with `PATCH /api/discord-events/tournaments/{tournament_id}`.")
    name: str = Field(..., description="Tournament name.")
    enabled: bool = Field(..., description="Whether its scheduled matches are mirrored as Discord events.")
    mirrored: int = Field(..., description="How many of its matches are in Discord as events right now.")


class DiscordEventsOverviewResponse(BaseModel):
    """The Discord events card: whether syncing can run, and each tournament's switch."""

    problem: Optional[str] = Field(
        None, description="Why events can't sync to the server right now. Null when they can. Switches are locked while set."
    )
    tournaments: List[DiscordMirrorTournament] = Field(..., description="The event's tournaments, by name.")
