"""Group race endpoints: the Group races board, its actions, and what the racetime settings forms pick from."""

from dataclasses import asdict
from typing import List

from fastapi import APIRouter, Depends, Query, status

from api.dependencies import ServiceErrorRoute, event_binding, require_api_actor, require_write_actor
from api.schemas.group_races import (
    GroupRaceCreateRequest,
    GroupRaceDetailResponse,
    GroupRaceResponse,
    PickOption,
    RoomOptionsResponse,
    SeriesPickResponse,
)
from application.services import GroupRaceBoardService, GroupRaceService
from models import AsyncQualifierLiveRace, Match, User

router = APIRouter(
    prefix="/group-races", tags=["Group races"], route_class=ServiceErrorRoute,
    dependencies=[Depends(event_binding(match_id=Match, live_race_id=AsyncQualifierLiveRace))],
)

WHO = (
    '**Who can call this:** Staff, or Admins of the tournament for a tournament race; '
    "Qualifier admin or the qualifier's admins for a qualifier live race."
)


def _row(view) -> GroupRaceResponse:
    data = asdict(view)
    data.pop('entrants'), data.pop('entrants_problem')
    return GroupRaceResponse(**data)


def _detail(view) -> GroupRaceDetailResponse:
    return GroupRaceDetailResponse(**asdict(view))


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


@router.get(
    "",
    response_model=List[GroupRaceResponse],
    summary="List group races",
    description=(
        "This event's group races (matches in group race tournaments) and qualifier live races "
        'with a start time, each with what the bot has done so far. `finished=false` (the default) '
        'lists what is still to run, from 12 hours back and two weeks ahead, earliest first; '
        '`finished=true` lists the last two weeks of finished races, newest first.\n\n' + WHO
    ),
)
async def list_group_races(
    finished: bool = Query(False, description='List finished races instead of the ones still to run.'),
    actor: User = Depends(require_api_actor),
):
    return [_row(v) for v in await GroupRaceBoardService().list_races(actor, finished=finished)]


@router.get(
    "/series",
    response_model=List[SeriesPickResponse],
    summary="List where a group race can be added",
    description=(
        "This event's group race tournaments, then each active qualifier's pools with their live race "
        'permalinks: the choices for `POST /group-races`.\n\n' + WHO
    ),
)
async def list_series(actor: User = Depends(require_api_actor)):
    return [
        SeriesPickResponse(
            kind=s.kind, id=s.id, name=s.name,
            permalinks=[PickOption(id=str(pid), name=label) for pid, label in s.permalinks],
        )
        for s in await GroupRaceBoardService().series(actor)
    ]


@router.post(
    "",
    response_model=GroupRaceDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a group race by hand",
    description=(
        'Adds a race with no players to a group race tournament (`tournament_id`), or a live race '
        'with a start time to a qualifier pool (`pool_id`, with the `permalink_id` it is raced on). '
        'The bot opens, posts and seeds it on time like any other. 400 when the start is in the '
        "past, the tournament isn't a group race series, or the permalink isn't in the pool.\n\n" + WHO
    ),
)
async def create_group_race(body: GroupRaceCreateRequest, actor: User = Depends(require_write_actor)):
    return _detail(await GroupRaceBoardService().create(
        actor, title=body.title, scheduled_at=body.scheduled_at,
        tournament_id=body.tournament_id, pool_id=body.pool_id, permalink_id=body.permalink_id,
    ))


def _board() -> GroupRaceBoardService:
    return GroupRaceBoardService()


GET_ABOUT = (
    "One race with who's in it: the room's entrants, read live from racetime while it's open, "
    'or the results once it has finished. 404 when it is not a group race.\n\n' + WHO
)
OPEN_ABOUT = 'Opens the racetime room before its time and posts it in Discord.\n\n' + WHO
SEED_ABOUT = (
    'Posts the seed in the open room now, and locks the room if the series locks at seed time. '
    "400 when the room isn't open.\n\n" + WHO
)
ANNOUNCE_ABOUT = (
    'Posts the open room in Discord again. 400 when the room isn\'t open, the series has no '
    'channel, or Discord refuses.\n\n' + WHO
)
LOCK_ABOUT = "Turns the open room invite-only now and says so in the room. 400 when it isn't open or is already locked.\n\n" + WHO
CANCEL_ABOUT = (
    'Calls the race off: a tournament race is cancelled like any match, a qualifier live race is '
    'removed, and an open room is marked cancelled. 400 once it has finished.\n\n' + WHO
)


@router.get("/matches/{match_id}", response_model=GroupRaceDetailResponse, summary="Get a tournament group race", description=GET_ABOUT)
async def get_match_race(match_id: int, actor: User = Depends(require_api_actor)):
    return _detail(await _board().get_race(actor, 'match', match_id))


@router.post("/matches/{match_id}/open", response_model=GroupRaceDetailResponse, summary="Open a tournament group race room now", description=OPEN_ABOUT)
async def open_match_race(match_id: int, actor: User = Depends(require_write_actor)):
    return _detail(await _board().open_now(actor, 'match', match_id))


@router.post("/matches/{match_id}/seed", response_model=GroupRaceDetailResponse, summary="Post a tournament group race seed now", description=SEED_ABOUT)
async def seed_match_race(match_id: int, actor: User = Depends(require_write_actor)):
    return _detail(await _board().post_seed_now(actor, 'match', match_id))


@router.post("/matches/{match_id}/announce", response_model=GroupRaceDetailResponse, summary="Re-post a tournament group race in Discord", description=ANNOUNCE_ABOUT)
async def announce_match_race(match_id: int, actor: User = Depends(require_write_actor)):
    return _detail(await _board().repost(actor, 'match', match_id))


@router.post("/matches/{match_id}/lock", response_model=GroupRaceDetailResponse, summary="Lock a tournament group race room now", description=LOCK_ABOUT)
async def lock_match_race(match_id: int, actor: User = Depends(require_write_actor)):
    return _detail(await _board().lock_now(actor, 'match', match_id))


@router.delete("/matches/{match_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Cancel a tournament group race", description=CANCEL_ABOUT)
async def cancel_match_race(match_id: int, actor: User = Depends(require_write_actor)):
    await _board().cancel(actor, 'match', match_id)


@router.get("/live-races/{live_race_id}", response_model=GroupRaceDetailResponse, summary="Get a qualifier live race on the board", description=GET_ABOUT)
async def get_live_race(live_race_id: int, actor: User = Depends(require_api_actor)):
    return _detail(await _board().get_race(actor, 'qualifier', live_race_id))


@router.post("/live-races/{live_race_id}/open", response_model=GroupRaceDetailResponse, summary="Open a qualifier live race room now", description=OPEN_ABOUT)
async def open_live_race(live_race_id: int, actor: User = Depends(require_write_actor)):
    return _detail(await _board().open_now(actor, 'qualifier', live_race_id))


@router.post("/live-races/{live_race_id}/seed", response_model=GroupRaceDetailResponse, summary="Post a qualifier live race seed now", description=SEED_ABOUT)
async def seed_live_race(live_race_id: int, actor: User = Depends(require_write_actor)):
    return _detail(await _board().post_seed_now(actor, 'qualifier', live_race_id))


@router.post("/live-races/{live_race_id}/announce", response_model=GroupRaceDetailResponse, summary="Re-post a qualifier live race in Discord", description=ANNOUNCE_ABOUT)
async def announce_live_race(live_race_id: int, actor: User = Depends(require_write_actor)):
    return _detail(await _board().repost(actor, 'qualifier', live_race_id))


@router.post("/live-races/{live_race_id}/lock", response_model=GroupRaceDetailResponse, summary="Lock a qualifier live race room now", description=LOCK_ABOUT)
async def lock_live_race(live_race_id: int, actor: User = Depends(require_write_actor)):
    return _detail(await _board().lock_now(actor, 'qualifier', live_race_id))


@router.delete("/live-races/{live_race_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Cancel a qualifier live race", description=CANCEL_ABOUT)
async def cancel_live_race(live_race_id: int, actor: User = Depends(require_write_actor)):
    await _board().cancel(actor, 'qualifier', live_race_id)
