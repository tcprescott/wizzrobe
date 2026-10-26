"""Race room (racetime room lifecycle) endpoints."""

from typing import List

from fastapi import APIRouter, Depends, status

from api.dependencies import (
    ServiceErrorRoute,
    event_binding,
    require_api_actor,
    require_write_actor,
)
from api.schemas.race_rooms import (
    RaceRoomCancelRequest,
    RaceRoomCreateRequest,
    RaceRoomResponse,
    RaceRoomStatusUpdateRequest,
)
from application.errors import require_found
from application.services import (
    MatchService,
    RaceRoomService,
    RacetimeRoomService,
)
from application.tenant_context import require_tenant_id
from models import Match, RacetimeRoom, User

router = APIRouter(prefix="/race-rooms", tags=["Race rooms"], route_class=ServiceErrorRoute, dependencies=[Depends(event_binding(match_id=Match, room_id=RacetimeRoom))])


async def _load_room_or_404(room_id: int) -> RacetimeRoom:
    return require_found(
        await RacetimeRoom.get_or_none(id=room_id, tenant_id=require_tenant_id()),
        "Race room",
    )


# --- Reads ----------------------------------------------------------------
# Literal single-segment routes are declared before parameterized ones.

@router.get(
    "/open",
    response_model=List[RaceRoomResponse],
    summary="List open race rooms",
    description=(
        "Lists this community's race rooms that are open or in progress.\n\n"
        "**Who can call this:** Staff or Sync admin."
    ),
)
async def list_open(actor: User = Depends(require_api_actor)):
    return await RacetimeRoomService().list_open_rooms_for_current_tenant(actor)


@router.get(
    "/by-match/{match_id}",
    response_model=RaceRoomResponse,
    summary="Get the race room for a match",
    description="Returns the race room opened for a match.\n\n**Who can call this:** Any member.",
)
async def get_by_match(match_id: int, actor: User = Depends(require_api_actor)):
    match = require_found(await MatchService().get_by_id(match_id), "Match")
    return require_found(await RacetimeRoomService().get_for_match(match), "Race room")


# --- Writes ---------------------------------------------------------------

@router.post(
    "",
    response_model=RaceRoomResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Manually open a race room for a match",
    description=(
        "Opens a racetime.gg room for a match now, whatever the tournament's automatic "
        "opening time. If the match already has a room, that room is returned instead. Each "
        "player gets a Discord DM with a button into the room, and when the tournament has a "
        "preset or seed generator a seed is rolled and attached. Fails with 400 when the "
        "tournament has no racetime bot or no racetime goal is set on the tournament or its "
        "room profile.\n\n"
        "**Who can call this:** Staff or Sync admin."
    ),
)
async def create_room(payload: RaceRoomCreateRequest, actor: User = Depends(require_write_actor)):
    return await RaceRoomService().manual_create_room(actor, payload.match_id)


@router.post(
    "/{room_id}/cancel",
    response_model=RaceRoomResponse,
    summary="Cancel a race room",
    description=(
        "Marks the room cancelled in Wizzrobe and keeps the reason in the audit log. It "
        "doesn't close the room on racetime.gg.\n\n"
        "**Who can call this:** Staff or Sync admin."
    ),
)
async def cancel_room(room_id: int, payload: RaceRoomCancelRequest, actor: User = Depends(require_write_actor)):
    room = await _load_room_or_404(room_id)
    await RaceRoomService().cancel_room_as(room, actor=actor, reason=payload.reason)
    return room


@router.patch(
    "/{room_id}/status",
    response_model=RaceRoomResponse,
    summary="Set a race room's cached status",
    description=(
        "Overwrites the room state Wizzrobe has on record. Wizzrobe keeps this in step with "
        "racetime.gg on its own, so this is for correcting it. Setting `in_progress` also "
        "records when the room opened if that wasn't set.\n\n"
        "**Who can call this:** Staff or Sync admin."
    ),
)
async def set_status(room_id: int, payload: RaceRoomStatusUpdateRequest, actor: User = Depends(require_write_actor)):
    room = await _load_room_or_404(room_id)
    return await RacetimeRoomService().set_status_as(room, payload.status, actor=actor)
