"""Match read endpoints."""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query

from api._match_view import MATCH_PREFETCH, serialize_match
from api.dependencies import ServiceErrorRoute, event_binding, require_admin, require_api_actor
from api.schemas.crew import CrewSignupInfo, MatchCrewResponse
from api.schemas.matches import MatchResponse
from application.errors import require_found
from application.event_context import event_scoped
from application.services import CrewService, MatchWatcherService
from application.tenant_context import require_tenant_id
from models import Match, User

router = APIRouter(
    prefix="/matches",
    tags=["Matches"],
    route_class=ServiceErrorRoute,
    dependencies=[Depends(event_binding(match_id=Match)), Depends(require_api_actor)],
)


@router.get(
    "",
    response_model=List[MatchResponse],
    summary="List matches",
    description=(
        "Matches in this community, soonest first, filtered by match, stage or "
        "tournament id and a scheduled-time range. Each match carries its "
        "tournament, stage, seed, players and approved crew; pending crew "
        "signups are left out.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def get_matches(
    match_id: Optional[List[int]] = Query(None, description="Filter by specific match IDs."),
    stage_id: Optional[List[int]] = Query(None, description="Filter by specific stage IDs."),
    start_date: Optional[datetime] = Query(None, description="Matches scheduled on or after this time."),
    end_date: Optional[datetime] = Query(None, description="Matches scheduled on or before this time."),
    tournament_id: Optional[List[int]] = Query(None, description="Filter by specific tournament IDs."),
    limit: int = Query(default=100, ge=1, le=500, description="Maximum matches to return."),
):
    query = event_scoped(Match.all())

    if match_id:
        query = query.filter(id__in=match_id)
    if stage_id:
        query = query.filter(stage_id__in=stage_id)
    if tournament_id:
        query = query.filter(tournament_id__in=tournament_id)
    if start_date is not None:
        query = query.filter(scheduled_at__gte=start_date)
    if end_date is not None:
        query = query.filter(scheduled_at__lte=end_date)

    query = query.prefetch_related(*MATCH_PREFETCH)
    matches = await query.order_by('scheduled_at').limit(limit)
    return [serialize_match(match) for match in matches]


@router.get(
    "/watching",
    response_model=List[MatchResponse],
    summary="List matches you are watching",
    description=(
        "Matches you are watching, soonest first. Watching a match sends you a "
        "Discord DM at each step of its run (checked in, started, finished, "
        "confirmed).\n\n"
        "**Who can call this:** Any member."
    ),
)
async def get_watched_matches(actor: User = Depends(require_api_actor)):
    match_ids = await MatchWatcherService().list_watched_match_ids(actor)
    if not match_ids:
        return []
    matches = (
        await Match.filter(id__in=match_ids, tenant_id=require_tenant_id())
        .prefetch_related(*MATCH_PREFETCH)
        .order_by('scheduled_at')
    )
    return [serialize_match(match) for match in matches]


@router.get(
    "/{match_id}",
    response_model=MatchResponse,
    summary="Get a single match",
    description=(
        "One match with its tournament, stage, seed, players and approved crew. "
        "Pending crew signups are left out; `GET /matches/{match_id}/crew` lists "
        "them.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def get_match(match_id: int):
    match = require_found(
        await Match.filter(id=match_id, tenant_id=require_tenant_id())
        .prefetch_related(*MATCH_PREFETCH)
        .first(),
        "Match",
    )
    return serialize_match(match)


@router.get(
    "/{match_id}/crew",
    response_model=MatchCrewResponse,
    summary="List a match's crew, including pending signups",
    description=(
        "Every commentator, tracker and broadcaster signup on a match, approved or not, with "
        "when each approved crew member confirmed. `GET /matches/{match_id}` "
        "shows approved crew only.\n\n"
        "**Who can call this:** Staff, or Admins or Crew coordinators of this match's tournament."
    ),
)
async def get_match_crew(match_id: int, actor: User = Depends(require_admin)):
    match = await CrewService().get_match_crew(match_id, actor)
    return MatchCrewResponse(
        match_id=match.id,
        commentators=[
            CrewSignupInfo.model_validate(c, from_attributes=True)
            for c in match.commentators  # type: ignore[attr-defined]
        ],
        trackers=[
            CrewSignupInfo.model_validate(t, from_attributes=True)
            for t in match.trackers  # type: ignore[attr-defined]
        ],
        broadcasters=[
            CrewSignupInfo.model_validate(b, from_attributes=True)
            for b in match.broadcasters  # type: ignore[attr-defined]
        ],
    )
