"""Discord events endpoints: per-tournament opt-in + on-demand reconcile.

All routes delegate to :class:`DiscordEventSyncService`, which gates every
operation on ``AuthService.can_manage_sync`` (STAFF / super-admin / ``SYNC_ADMIN``)
and audits mutations. Reads take any token (``require_api_actor``); writes reject
read-only tokens (``require_write_actor``). Tenancy comes from the token.
"""

from typing import List

from fastapi import APIRouter, Depends

from api.dependencies import ServiceErrorRoute, require_api_actor, require_write_actor
from api.schemas.discord_events import (
    DiscordEventTournamentResponse,
    DiscordEventTournamentUpdateRequest,
    DiscordScheduledEventResponse,
    ReconcileResultResponse,
)
from application.services import DiscordEventSyncService
from models import User

router = APIRouter(prefix="/discord-events", tags=["Discord events"], route_class=ServiceErrorRoute)


@router.get(
    "/tournaments",
    response_model=List[DiscordEventTournamentResponse],
    summary="List tournaments with their Discord-events settings",
    description=(
        "Lists this community's tournaments with their Discord events settings.\n\n"
        "**Who can call this:** Staff or Sync admin."
    ),
)
async def list_tournaments(actor: User = Depends(require_api_actor)):
    return await DiscordEventSyncService().list_tournaments(actor)


@router.get(
    "/events",
    response_model=List[DiscordScheduledEventResponse],
    summary="List mirrored Discord Scheduled Event links",
    description=(
        "Lists the Discord scheduled events Wizzrobe created for this community's matches.\n\n"
        "**Who can call this:** Staff or Sync admin."
    ),
)
async def list_events(actor: User = Depends(require_api_actor)):
    return await DiscordEventSyncService().list_events(actor)


@router.patch(
    "/tournaments/{tournament_id}",
    response_model=DiscordEventTournamentResponse,
    summary="Update a tournament's Discord-events settings",
    description=(
        "Changes whether a tournament's matches appear as scheduled events in the community's "
        "Discord server, and how those events are titled, described and timed. Omitted fields "
        "keep their value; changes reach Discord on the next sync. Fails with 400 while no Discord "
        "server is connected or the bot can't create events in it.\n\n"
        "**Who can call this:** Staff or Sync admin."
    ),
)
async def update_tournament_settings(
    tournament_id: int,
    body: DiscordEventTournamentUpdateRequest,
    actor: User = Depends(require_write_actor),
):
    return await DiscordEventSyncService().update_settings(
        actor, tournament_id, **body.model_dump(exclude_unset=True)
    )


@router.post(
    "/reconcile",
    response_model=ReconcileResultResponse,
    summary="Reconcile the tenant's Discord events now",
    description=(
        "Brings the community's Discord scheduled events in line with its schedule now: "
        "creates events for matches in opted-in tournaments over the next 14 days, updates "
        "ones whose match changed, and removes ones whose match is gone. It only ever touches "
        "events Wizzrobe created for this community. Fails with 400 when no Discord server is "
        "connected.\n\n"
        "**Who can call this:** Staff or Sync admin."
    ),
)
async def reconcile_now(actor: User = Depends(require_write_actor)):
    result = await DiscordEventSyncService().reconcile_now(actor)
    return result.as_dict()
