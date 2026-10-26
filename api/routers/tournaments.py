"""Tournament endpoints."""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query

from api.dependencies import ServiceErrorRoute, require_api_actor
from api.schemas.bracket_overview import TournamentBracketResponse
from api.schemas.tournaments import TournamentResponse
from application.errors import require_found
from application.services import BracketOverviewService, TournamentService
from models import User

router = APIRouter(
    prefix="/tournaments",
    tags=["Tournaments"],
    route_class=ServiceErrorRoute,
    dependencies=[Depends(require_api_actor)],
)


@router.get(
    "",
    response_model=List[TournamentResponse],
    summary="List tournaments",
    description=(
        "Returns this community's tournaments. Inactive tournaments are included only for "
        "admins (Staff, tournament admins and the other admin roles), and only unless you ask "
        "for active only.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def list_tournaments(
    active_only: bool = Query(False, description="Return only active tournaments"),
    actor: User = Depends(require_api_actor),
):
    return await TournamentService().list_for_viewer(actor, active_only=active_only)


@router.get(
    "/{tournament_id}",
    response_model=TournamentResponse,
    summary="Get a tournament",
    description=(
        "Returns one of this community's tournaments. An inactive tournament is 404 unless "
        "you're an admin.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def get_tournament(tournament_id: int, actor: User = Depends(require_api_actor)):
    return require_found(
        await TournamentService().get_for_viewer(actor, tournament_id), "Tournament"
    )


@router.get(
    "/{tournament_id}/bracket",
    response_model=TournamentBracketResponse,
    summary="Get a tournament's whole bracket",
    description=(
        "Returns a native, start.gg or Challonge bracket in one shape: phases, pools, every set "
        "with its two sides, where each side comes from and where the winner and loser go, scores, "
        "results, the Wizzrobe matches booked for it, and standings. start.gg and Challonge come "
        "from Wizzrobe's synced copy (`last_synced_at`), never a live call to the site. Pool counts "
        "and standings cover the whole pool even when filters drop sets. Entrants' `user_id` is "
        "filled in only for Staff and this tournament's admins. 404 when the tournament is "
        "inactive (to non-admins), a native tournament has no published stage (to non-Staff), or "
        "the bracket site's feature is off.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def get_tournament_bracket(
    tournament_id: int,
    phase: Optional[str] = Query(None, description="Only this phase: its id or name."),
    pool: Optional[str] = Query(None, description='Only this pool: its id or name, like "A1".'),
    state: Optional[str] = Query(None, description="Only sets in this state: `pending`, `open` or `complete`."),
    entrant: Optional[str] = Query(None, description="Only sets this entrant plays in: part of a name, or an entrant id."),
    include_sets: bool = Query(True, description="False for the phases, pools and standings without any sets."),
    actor: User = Depends(require_api_actor),
):
    return await BracketOverviewService().get_tournament_bracket(
        actor, tournament_id, phase=phase, pool=pool, state=state, entrant=entrant, include_sets=include_sets,
    )
