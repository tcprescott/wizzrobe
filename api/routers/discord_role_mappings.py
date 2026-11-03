"""Discord-role-to-app-role mapping endpoints (Staff only).

Reads require STAFF in this community; mutations additionally reject read-only
tokens. ``DiscordRoleMappingService`` re-enforces the Staff check
(``can_grant_roles``) and writes audit logs.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status

from api.dependencies import ServiceErrorRoute, require_staff, require_staff_write
from api.schemas.discord_role_mappings import (
    DiscordRoleMappingCreate,
    DiscordRoleMappingResponse,
)
from application.services import DiscordRoleMappingService, EventService
from models import User

router = APIRouter(
    prefix="/discord-role-mappings",
    tags=["Discord Role Mappings"],
    route_class=ServiceErrorRoute,
)


@router.get(
    "",
    response_model=List[DiscordRoleMappingResponse],
    summary="List Discord role mappings (Staff only)",
    description=(
        "Returns the community's Discord role mappings, sorted by Discord role name. Each one says which "
        "role or tournament authority members holding that Discord role receive.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def list_mappings(
    guild_id: Optional[int] = Query(None, description="Only mappings for this Discord server ID."),
    actor: User = Depends(require_staff),
):
    service = DiscordRoleMappingService()
    if guild_id is not None:
        return await service.list_mappings(guild_id)
    return await service.list_all_mappings()


@router.post(
    "",
    response_model=DiscordRoleMappingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a Discord role mapping (Staff only)",
    description=(
        "Maps a Discord role to either a community-wide role or one tournament's admin or crew coordinator "
        "slot, and records it in the audit log. Members pick up the grant at their next Discord role sync, "
        "usually when they sign in.\n\n"
        "Fails with 400 when both or neither of `app_role` and `tournament_grant` are set, `app_role` is "
        "`super_admin`, a community-wide role names a tournament, a tournament grant has no `tournament_id`, "
        "or the same mapping already exists.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def add_mapping(
    body: DiscordRoleMappingCreate, actor: User = Depends(require_staff_write),
):
    return await DiscordRoleMappingService().add_mapping(
        guild_id=body.guild_id,
        discord_role_id=body.discord_role_id,
        discord_role_name=body.discord_role_name,
        app_role=body.app_role,
        tournament_grant=body.tournament_grant,
        tournament_id=body.tournament_id,
        event_id=await EventService.id_for_slug(body.event),
        actor=actor,
    )


@router.delete(
    "/{mapping_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a Discord role mapping (Staff only)",
    description=(
        "Removes the mapping and records it in the audit log. Members who got a role through it lose that "
        "role at their next Discord role sync.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def remove_mapping(mapping_id: int, actor: User = Depends(require_staff_write)):
    await DiscordRoleMappingService().remove_mapping(mapping_id, actor=actor)
