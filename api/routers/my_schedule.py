"""A player's own Home and Schedule, and booking a time, for Wizzrobe's app.

Screen-shaped reads: each returns what one tab of the app draws, already
merged across matches, crew slots and shifts, so the app doesn't rebuild the
joins. Times are UTC instants; ``zone`` says which clock to print them on.
"""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query

from api.dependencies import ServiceErrorRoute, event_binding, require_api_actor, require_write_actor
from api.schemas.my_schedule import (
    BookableResponse,
    BookedResponse,
    BookRequest,
    HomeResponse,
    ScheduleEntryResponse,
    ScheduleResponse,
    TimeCheckResponse,
)
from application.services import PlayerHomeService
from application.utils.timezone import to_utc_aware
from models import User

router = APIRouter(
    prefix='/me', tags=['My schedule'], route_class=ServiceErrorRoute, dependencies=[Depends(event_binding())],
)

Source = Literal['bracket', 'challonge', 'startgg']


@router.get(
    '/home',
    response_model=HomeResponse,
    summary='Your Home tab',
    description=(
        "Your next match, what's waiting on you (times to pick, an opponent's request to "
        "move a match, matches to acknowledge) and the rest of today.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def my_home(actor: User = Depends(require_api_actor)) -> HomeResponse:
    return HomeResponse.model_validate(await PlayerHomeService().home(actor), from_attributes=True)


@router.get(
    '/schedule',
    response_model=ScheduleResponse,
    summary='Your Schedule tab',
    description=(
        "Your matches, crew slots and volunteer shifts in this event. Upcoming ones soonest "
        "first, or with `past=true` the ones that have ended, latest first.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def my_schedule(
    past: bool = Query(False, description="Show what has ended instead of what's coming."),
    actor: User = Depends(require_api_actor),
) -> ScheduleResponse:
    service = PlayerHomeService()
    items = await service.schedule(actor, past=past)
    return ScheduleResponse(
        zone=await service.viewer_zone(actor),
        items=[ScheduleEntryResponse.model_validate(i, from_attributes=True) for i in items],
    )


@router.get(
    '/bookable',
    response_model=list[BookableResponse],
    summary='Matches waiting on you to pick a time',
    description=(
        "Your bracket matchups with no time yet, whether the bracket runs in Wizzrobe, on "
        "Challonge or on start.gg. A best-of set lists one row per game still to book.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def my_bookable(actor: User = Depends(require_api_actor)) -> list[BookableResponse]:
    return [
        BookableResponse.model_validate(b, from_attributes=True)
        for b in await PlayerHomeService().bookable(actor)
    ]


@router.get(
    '/bookable/{source}/{key}/check',
    response_model=TimeCheckResponse,
    summary='Check a time before booking it',
    description=(
        "Says whether booking this matchup at `at` would be refused (outside the round's "
        "booking window) and lists anything that might make the time a bad one: a player "
        "already busy, a full room, past closing. Books nothing. Fails with 400 when the "
        "matchup isn't yours to book.\n\n"
        "**Who can call this:** A player in the matchup."
    ),
)
async def check_time(
    source: Source,
    key: int,
    at: datetime = Query(..., description="The time to check, as an ISO 8601 instant with an offset."),
    actor: User = Depends(require_api_actor),
) -> TimeCheckResponse:
    found = await PlayerHomeService().check_time(actor, source, key, to_utc_aware(at))
    return TimeCheckResponse.model_validate(found, from_attributes=True)


@router.post(
    '/bookable/{source}/{key}/book',
    response_model=BookedResponse,
    summary='Pick a time for your match',
    description=(
        "Books the matchup at `scheduled_at` and tells your opponent. A best-of set books "
        "its next game. Fails with 400 when the matchup isn't waiting on you, the time is "
        "outside the round's booking window or the tournament's hours, or someone booked "
        "it first.\n\n"
        "**Who can call this:** A player in the matchup."
    ),
)
async def book(
    source: Source, key: int, body: BookRequest, actor: User = Depends(require_write_actor),
) -> BookedResponse:
    match = await PlayerHomeService().book(actor, source, key, to_utc_aware(body.scheduled_at))
    return BookedResponse(match_id=match.id, scheduled_at=match.scheduled_at)
