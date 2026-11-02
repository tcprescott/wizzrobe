"""The admin Match Schedule for Wizzrobe's app: the board and one match's pane.

Screen-shaped reads: the board comes back grouped as the run sheet draws it,
each row carrying what the viewer may do to it. Every action on a match is an
existing `/matches/{match_id}/…` endpoint.
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query

from api.dependencies import ServiceErrorRoute, event_binding, require_api_actor
from api.schemas.admin_match_schedule import BoardResponse, MatchPaneResponse
from application.services import AdminScheduleService
from models import Match, User

router = APIRouter(
    prefix='/admin/match-schedule', tags=['Admin: Match Schedule'], route_class=ServiceErrorRoute,
    dependencies=[Depends(event_binding(match_id=Match))],
)

_WHO = (
    "**Who can call this:** Staff, Broadcast tech, or Admins and crew coordinators of a "
    "tournament (who see only their tournaments' matches)."
)


@router.get(
    '',
    response_model=BoardResponse,
    summary='The Match Schedule board',
    description=(
        "Every match on a day (or every day), grouped as the run sheet draws them: needs a "
        "decision, live now, up next, waiting for players, confirmed. Each row says what you "
        "may do to it in `can`.\n\n" + _WHO
    ),
)
async def board(
    day: Optional[date] = Query(None, description="A day on the board's clock; leave out for every day."),
    tournament_id: Optional[int] = Query(None),
    stage_id: Optional[int] = Query(None),
    q: str = Query('', max_length=100, description="Matches players, tournament or stage."),
    tz: Optional[str] = Query(None, description="The device's IANA zone, used when neither the community nor you pin one."),
    actor: User = Depends(require_api_actor),
) -> BoardResponse:
    data = await AdminScheduleService().board(
        actor, day=day, tournament_id=tournament_id, stage_id=stage_id, search=q, device_zone=tz,
    )
    return BoardResponse.model_validate(data)


@router.get(
    '/matches/{match_id}',
    response_model=MatchPaneResponse,
    summary="One match on the Match Schedule",
    description=(
        "The board's line for the match plus its pane: what it still needs before it can "
        "start, where it can be rewound to, and when it reached its current step.\n\n" + _WHO
    ),
)
async def match_pane(
    match_id: int,
    tz: Optional[str] = Query(None),
    actor: User = Depends(require_api_actor),
) -> MatchPaneResponse:
    return MatchPaneResponse.model_validate(
        await AdminScheduleService().match(actor, match_id, device_zone=tz),
    )
