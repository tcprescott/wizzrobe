"""The admin Match Schedule for Wizzrobe's app: the board and one match's pane.

Screen-shaped reads: the board comes back grouped as the run sheet draws it,
each row carrying what the viewer may do to it. Every action on a match is an
existing `/matches/{match_id}/…` endpoint.
"""

from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Response, status

from api.dependencies import ServiceErrorRoute, event_binding, require_api_actor, require_write_actor
from api.schemas.admin_match_schedule import (
    BoardResponse,
    BookRequest,
    LinkRequest,
    MatchFormResponse,
    MatchFormSave,
    MatchPaneResponse,
    SavedMatch,
    Suggestion,
    SuggestRequest,
    TimeCheck,
    TimeCheckRequest,
    UnbookedItem,
)
from application.services import AdminMatchFormService, AdminScheduleService
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


_EDITORS = "**Who can call this:** Staff, or Admins of a tournament (for their tournaments)."


@router.get(
    '/form',
    response_model=MatchFormResponse,
    summary="The New/Edit match form",
    description=(
        "The options the form offers (tournaments you may schedule into with who is enrolled, "
        "stages, community members) and, with `match_id`, that match's current values.\n\n" + _EDITORS
    ),
)
async def match_form(
    match_id: Optional[int] = Query(None, description="The match to edit; leave out for a new one."),
    actor: User = Depends(require_api_actor),
) -> MatchFormResponse:
    return MatchFormResponse.model_validate(await AdminMatchFormService().form(actor, match_id))


@router.post(
    '/matches',
    response_model=SavedMatch,
    status_code=status.HTTP_201_CREATED,
    summary="Create a match from the form",
    description=(
        "Creates the match with the same DMs as the website's Create Match, then puts it on the "
        "stage. Players may be left empty to hold the slot. Fails with 400 for a time outside "
        "the tournament's hours or someone listed as both player and crew.\n\n" + _EDITORS
    ),
)
async def create_from_form(body: MatchFormSave, actor: User = Depends(require_write_actor)) -> SavedMatch:
    return SavedMatch(match_id=await AdminMatchFormService().save(actor, None, body.model_dump()))


@router.put(
    '/matches/{match_id}',
    response_model=SavedMatch,
    summary="Save the Edit match form",
    description=(
        "Saves every field the form shows, including stage and stream candidate. Moving the time "
        "or changing players asks them to acknowledge again, as on the website.\n\n" + _EDITORS
    ),
)
async def save_form(match_id: int, body: MatchFormSave, actor: User = Depends(require_write_actor)) -> SavedMatch:
    return SavedMatch(match_id=await AdminMatchFormService().save(actor, match_id, body.model_dump()))


@router.post(
    '/check-time',
    response_model=TimeCheck,
    summary="Check a time before booking it",
    description=(
        "What the website's \"Check this time\" sheet shows: clashes for these players, and for a "
        "bracket matchup the round window the time falls outside of. Nothing is saved.\n\n" + _EDITORS
    ),
)
async def check_time(body: TimeCheckRequest, actor: User = Depends(require_api_actor)) -> TimeCheck:
    return TimeCheck.model_validate(await AdminMatchFormService().check_time(
        actor, tournament_id=body.tournament_id, player_ids=body.player_ids, at=body.scheduled_at,
        match_id=body.match_id, source=body.source, key=body.key,
    ))


@router.post(
    '/suggest-time',
    response_model=Suggestion,
    summary="Suggest a time for these players",
    description=(
        "The best start time for the players' availability, the tournament's hours and the round's "
        "window. Fails with 400 when nothing fits.\n\n" + _EDITORS
    ),
)
async def suggest_time(body: SuggestRequest, actor: User = Depends(require_api_actor)) -> Suggestion:
    return Suggestion(scheduled_at=await AdminMatchFormService().suggest(
        actor, tournament_id=body.tournament_id, player_ids=body.player_ids,
        match_id=body.match_id, source=body.source, key=body.key,
    ))


@router.get(
    '/unbooked',
    response_model=List[UnbookedItem],
    summary="Bracket matchups nobody has booked yet",
    description=(
        "Every open matchup on a Wizzrobe, Challonge or start.gg bracket that you may book, with "
        "matchups still waiting on their players. Grouped by tournament, in bracket order.\n\n" + _EDITORS
    ),
)
async def unbooked(actor: User = Depends(require_api_actor)) -> List[UnbookedItem]:
    return [UnbookedItem.model_validate(r) for r in await AdminMatchFormService().unbooked(actor)]


@router.post(
    '/unbooked/{source}/{key}/book',
    response_model=SavedMatch,
    status_code=status.HTTP_201_CREATED,
    summary="Book a matchup",
    description=(
        "Books one game: the match for a best-of-1, the next free game of a set, or a slot ahead "
        "of undecided players. Fails with 400 outside the round's window unless "
        "`allow_outside_round`, or when the matchup was booked meanwhile.\n\n" + _EDITORS
    ),
)
async def book(source: str, key: int, body: BookRequest, actor: User = Depends(require_write_actor)) -> SavedMatch:
    return SavedMatch(match_id=await AdminMatchFormService().book(
        actor, source, key, body.scheduled_at, allow_outside_round=body.allow_outside_round,
    ))


@router.post(
    '/unbooked/{source}/{key}/link',
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Link a match already on the schedule to a matchup",
    description=(
        "Records a match scheduled by hand as this matchup's, so its result counts for the bracket. "
        "For a matchup still waiting on players, re-attaches a booking that lost its matchup.\n\n" + _EDITORS
    ),
)
async def link(
    source: str, key: int, body: LinkRequest,
    ahead: bool = Query(False, description="The matchup's players aren't decided yet."),
    actor: User = Depends(require_write_actor),
):
    await AdminMatchFormService().link(actor, source, key, body.match_id, ahead=ahead)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
