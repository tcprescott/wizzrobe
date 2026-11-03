"""The Discord pane of Admin → Settings: the linked server, role mappings and the events mirror.

Reads and writes go through ``DiscordSettingsService``, which calls the same
link, mapping and events services the website uses, so checks and audit rows
match. Removing a mapping and switching a tournament use the existing
``/discord-role-mappings`` and ``/discord-events`` routes.
"""

from typing import List

from fastapi import APIRouter, Depends, status

from api.dependencies import (
    ServiceErrorRoute,
    event_binding,
    require_api_actor,
    require_staff,
    require_staff_write,
)
from api.schemas.discord_settings import (
    DiscordEventsOverviewResponse,
    DiscordGrantOption,
    DiscordLinkResponse,
    DiscordMappingOptionsResponse,
    DiscordMappingRow,
    DiscordMappingsAddRequest,
    DiscordMappingsAddResponse,
    DiscordMirrorTournament,
    DiscordPick,
    DiscordRoleOption,
    DiscordSyncResponse,
)
from application.services import DiscordSettingsService
from application.services.discord.discord_settings_service import mapping_labels
from models import User

router = APIRouter(
    prefix="/settings/discord",
    tags=["Discord settings"],
    route_class=ServiceErrorRoute,
    dependencies=[Depends(event_binding())],
)

STAFF_ONLY = "**Who can call this:** Staff."


@router.get(
    "",
    response_model=DiscordLinkResponse,
    summary="Get the community's Discord link",
    description=(
        "Which Discord server this community is linked to, whether the bot can reach it now, and which "
        "of the pane's cards the caller may change. Connecting a server happens on the website, because "
        "Discord's sign-in comes back to it.\n\n"
        "**Who can call this:** Staff or Sync admin."
    ),
)
async def get_link(actor: User = Depends(require_api_actor)):
    link = await DiscordSettingsService().link_status(actor)
    return DiscordLinkResponse(
        linked=link.guild_id is not None,
        guild_id=str(link.guild_id) if link.guild_id is not None else None,
        server_name=link.server_name,
        reachable=link.server_name is not None,
        can_manage_roles=link.can_manage_roles,
        can_manage_events=link.can_manage_events,
    )


@router.post(
    "/disconnect",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Disconnect the Discord server",
    description=(
        "Unlinks the community's Discord server and records it in the audit log. Role sync and the events "
        "mirror stop until a server is connected again. The bot stays in the server. Fails with 400 when "
        "no server is linked.\n\n" + STAFF_ONLY
    ),
)
async def disconnect(actor: User = Depends(require_staff_write)):
    await DiscordSettingsService().disconnect(actor)


@router.get(
    "/mappings",
    response_model=List[DiscordMappingRow],
    summary="List role mappings for the linked server",
    description=(
        "The linked server's role mappings, by Discord role name, each with what it grants and where. "
        "Empty when no server is linked.\n\n" + STAFF_ONLY
    ),
)
async def list_mappings(actor: User = Depends(require_staff)):
    rows = []
    for m in await DiscordSettingsService().list_mappings(actor):
        grants, applies_to = mapping_labels(m)
        rows.append(DiscordMappingRow(
            id=m.id,
            discord_role_id=str(m.discord_role_id),
            discord_role_name=m.discord_role_name,
            grants=grants,
            applies_to=applies_to,
            grants_nothing=m.app_role is not None and m.app_role.is_per_event and m.event is None,
        ))
    return rows


@router.get(
    "/mapping-options",
    response_model=DiscordMappingOptionsResponse,
    summary="What a new role mapping can use",
    description=(
        "The linked server's roles the bot can hand out, what a mapping can grant, and the tournaments and "
        "events a grant can land on. Fails with 400 when no server is linked or Discord can't be reached.\n\n"
        + STAFF_ONLY
    ),
)
async def mapping_options(actor: User = Depends(require_staff)):
    options = await DiscordSettingsService().mapping_options(actor)
    return DiscordMappingOptionsResponse(
        discord_roles=[DiscordRoleOption(id=str(r['id']), name=r['name'], color=r['color']) for r in options.discord_roles],
        grants=[DiscordGrantOption(value=g.value, label=g.label, kind=g.kind) for g in options.grants],  # type: ignore[arg-type]
        tournaments=[DiscordPick(id=i, name=n) for i, n in options.tournaments],
        events=[DiscordPick(id=i, name=n) for i, n in options.events],
        default_event_id=options.default_event_id,
    )


@router.post(
    "/mappings",
    response_model=DiscordMappingsAddResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add role mappings",
    description=(
        "Maps every Discord role in `discord_roles` onto every role and tournament grant picked, and records "
        "each in the audit log. A pair that already exists is skipped, not an error. Members pick the grants "
        "up at their next sign-in or the next Sync everyone.\n\n"
        "Fails with 400 when nothing is picked to grant, a tournament grant has no `tournament_id`, or no "
        "server is linked.\n\n" + STAFF_ONLY
    ),
)
async def add_mappings(body: DiscordMappingsAddRequest, actor: User = Depends(require_staff_write)):
    created, skipped = await DiscordSettingsService().add_mappings(
        actor,
        discord_roles=[(int(r.id), r.name) for r in body.discord_roles],
        app_roles=body.app_roles,
        tournament_grants=body.tournament_grants,
        tournament_id=body.tournament_id,
        event_id=body.event_id,
    )
    return DiscordMappingsAddResponse(created=created, skipped=skipped)


@router.post(
    "/sync",
    response_model=DiscordSyncResponse,
    summary="Sync everyone's roles now",
    description=(
        "Applies the current mappings to the community's members and to everyone in the server holding a "
        "mapped role, creating accounts for those who have never signed in. Can take a while on a big "
        "server. Recorded in the audit log. Fails with 400 when no server is linked.\n\n" + STAFF_ONLY
    ),
)
async def sync_everyone(actor: User = Depends(require_staff_write)):
    return DiscordSyncResponse(**await DiscordSettingsService().sync_everyone(actor))


@router.get(
    "/events",
    response_model=DiscordEventsOverviewResponse,
    summary="Get the Discord events mirror",
    description=(
        "Whether the bot can create events in the linked server, and each of the event's tournaments with "
        "its switch and how many of its matches are in Discord now.\n\n"
        "**Who can call this:** Staff or Sync admin."
    ),
)
async def events_overview(actor: User = Depends(require_api_actor)):
    overview = await DiscordSettingsService().events_overview(actor)
    return DiscordEventsOverviewResponse(
        problem=overview.problem,
        tournaments=[
            DiscordMirrorTournament(id=t.id, name=t.name, enabled=t.enabled, mirrored=t.mirrored)
            for t in overview.tournaments
        ],
    )
