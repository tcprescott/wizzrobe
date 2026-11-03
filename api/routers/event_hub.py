"""The app's Event tab: the hub, the run sheet and Watch.

Screen-shaped reads built by ``EventHubService``. Crew signups and watching go
through the existing match routes; the bracket screen reads
``GET /tournaments/{id}/bracket``.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query

from api.dependencies import ServiceErrorRoute, event_binding, require_api_actor
from api.schemas.event_hub import EventHubResponse, RunSheetResponse, WatchResponse
from application.services import EventHubService, PlayerHomeService
from models import User

router = APIRouter(
    prefix='/event', tags=['Event'], route_class=ServiceErrorRoute, dependencies=[Depends(event_binding())],
)


@router.get(
    '/hub',
    response_model=EventHubResponse,
    summary="The Event tab's hub",
    description=(
        "What's on each stage now and next, the next few matches anywhere, and a card per "
        "active tournament with its bracket status.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def event_hub(actor: User = Depends(require_api_actor)) -> EventHubResponse:
    body = EventHubResponse.model_validate(await EventHubService().hub(actor), from_attributes=True)
    body.zone = await PlayerHomeService().viewer_zone(actor)
    return body


@router.get(
    '/schedule',
    response_model=RunSheetResponse,
    summary='The run sheet',
    description=(
        "Every match scheduled in `[start, end)`, soonest first, with its crew slots and whether "
        "you play in it or watch it. Ask for one local day at a time; more than 48 hours is 400.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def run_sheet(
    start: datetime = Query(..., description="Window start, an instant."),
    end: datetime = Query(..., description="Window end, an instant."),
    actor: User = Depends(require_api_actor),
) -> RunSheetResponse:
    rows = await EventHubService().run_sheet(actor, start, end)
    return RunSheetResponse.model_validate(
        {'zone': await PlayerHomeService().viewer_zone(actor), 'rows': rows}, from_attributes=True,
    )


@router.get(
    '/watch',
    response_model=WatchResponse,
    summary='The Watch screen',
    description=(
        "Each stage's stream with what's on now and next, the streamed matches coming up over "
        "the next week, and the tournaments.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def watch(actor: User = Depends(require_api_actor)) -> WatchResponse:
    body = WatchResponse.model_validate(await EventHubService().watch(), from_attributes=True)
    body.zone = await PlayerHomeService().viewer_zone(actor)
    return body
