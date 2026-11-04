"""Group race endpoints: what the racetime settings forms can pick from."""

from fastapi import APIRouter, Depends

from api.dependencies import ServiceErrorRoute, require_api_actor
from api.schemas.group_races import PickOption, RoomOptionsResponse
from application.services import GroupRaceService
from models import User

router = APIRouter(prefix="/group-races", tags=["Group races"], route_class=ServiceErrorRoute)


@router.get(
    "/options",
    response_model=RoomOptionsResponse,
    summary="List racetime settings choices",
    description=(
        'The racetime bots, race room profiles, Discord text channels and Discord roles a '
        "tournament's Racetime settings or a qualifier's live race settings can pick from. "
        'When Discord can\'t be read the channel and role lists are empty and '
        '`discord_problem` says why.\n\n'
        '**Who can call this:** Staff, tournament admins and crew coordinators, or Qualifier admin.'
    ),
)
async def room_options(actor: User = Depends(require_api_actor)):
    options = await GroupRaceService().room_options(actor)
    return RoomOptionsResponse(
        bots=[PickOption(id=str(b.id), name=f'{b.category} ({b.name})') for b in options.bots],
        profiles=[PickOption(id=str(p.id), name=p.name) for p in options.profiles],
        channels=[PickOption(**c) for c in options.channels],
        roles=[PickOption(**r) for r in options.roles],
        discord_problem=options.discord_problem,
    )
