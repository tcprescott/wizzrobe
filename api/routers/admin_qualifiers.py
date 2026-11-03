"""REST for the app's Admin → Qualifiers screen.

The qualifiers the caller runs, and one qualifier's review queue, leaderboard top
and setup, shaped for the screen. Writes go through the existing
`/async-qualifiers` routes.
"""

from typing import List

from fastapi import APIRouter, Depends

from api.dependencies import ServiceErrorRoute, event_binding, require_api_actor
from api.schemas.admin_qualifiers import (
    QualifierBoardResponse,
    QualifierDetailResponse,
    QualifierLiveRaceLineResponse,
    QualifierPoolLineResponse,
    QualifierReviewersResponse,
)
from application.services import AdminQualifiersService
from models import AsyncQualifier, User

router = APIRouter(
    prefix='/admin/qualifiers',
    tags=['Admin: qualifiers'],
    route_class=ServiceErrorRoute,
    dependencies=[Depends(event_binding(qualifier_id=AsyncQualifier))],
)

_WHO = "**Who can call this:** Staff, Qualifier Admins, and the qualifier's own reviewers."


@router.get(
    '/board', response_model=QualifierBoardResponse, summary='The Qualifiers board',
    description=(
        "Every qualifier the caller runs or reviews, open ones first, each with how many runs wait "
        "for review.\n\n" + _WHO
    ),
)
async def board(actor: User = Depends(require_api_actor)):
    return await AdminQualifiersService().board(actor)


@router.get(
    '/{qualifier_id}', response_model=QualifierDetailResponse, summary='One qualifier',
    description=(
        "The review queue (with each runner's other attempts and the claim lock), the top of the "
        "leaderboard, and counts for the setup pages.\n\n" + _WHO
    ),
)
async def detail(qualifier_id: int, actor: User = Depends(require_api_actor)):
    return await AdminQualifiersService().detail(actor, qualifier_id)


@router.get(
    '/{qualifier_id}/pools', response_model=List[QualifierPoolLineResponse], summary="A qualifier's pools and seeds",
    description="Each pool with its preset, whether it can roll seeds, and its permalinks.\n\n" + _WHO,
)
async def pools(qualifier_id: int, actor: User = Depends(require_api_actor)):
    return await AdminQualifiersService().pools(actor, qualifier_id)


@router.get(
    '/{qualifier_id}/live-races', response_model=List[QualifierLiveRaceLineResponse],
    summary="A qualifier's live races",
    description="Each live race, newest first, with its pool, state and racetime room.\n\n" + _WHO,
)
async def live_races(qualifier_id: int, actor: User = Depends(require_api_actor)):
    return await AdminQualifiersService().live_races(actor, qualifier_id)


@router.get(
    '/{qualifier_id}/reviewers', response_model=QualifierReviewersResponse, summary="A qualifier's reviewers",
    description=(
        "The people who review this qualifier only (Staff and Qualifier Admins review every one), "
        "and the community's members to add from.\n\n" + _WHO
    ),
)
async def reviewers(qualifier_id: int, actor: User = Depends(require_api_actor)):
    return await AdminQualifiersService().reviewers(actor, qualifier_id)
