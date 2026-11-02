"""REST for the app's Admin → Brackets screen.

The board of native bracket stages and one stage's detail, both shaped for the
screen. Writes go through the existing `/brackets` routes.
"""

from fastapi import APIRouter, Depends

from api.dependencies import ServiceErrorRoute, event_binding, require_api_actor
from api.schemas.admin_brackets import (
    BracketsBoardResponse,
    BracketStageDetailResponse,
    QualifierSeedingResponse,
)
from application.services import AdminBracketsService
from models import Bracket, User

router = APIRouter(
    prefix='/admin/brackets',
    tags=['Admin: brackets'],
    route_class=ServiceErrorRoute,
    dependencies=[Depends(event_binding(bracket_id=Bracket))],
)

_STAFF = "**Who can call this:** Staff."


@router.get(
    '/board', response_model=BracketsBoardResponse, summary='The Brackets board',
    description=(
        "Every tournament in this event that isn't on Challonge, active first, with its native "
        "bracket stages in order. Each stage says the one step it's up to and the counts behind it.\n\n"
        + _STAFF
    ),
)
async def board(actor: User = Depends(require_api_actor)):
    board = await AdminBracketsService().board(actor)
    return BracketsBoardResponse(
        tournaments=board.tournaments,  # type: ignore[arg-type]
        formats=[{'value': v, 'label': lbl} for v, lbl in board.formats],  # type: ignore[misc]
    )


@router.get(
    '/{bracket_id}', response_model=BracketStageDetailResponse, summary='One bracket stage',
    description=(
        "A stage's next step, its seeded entries, the tournament roster (who is or isn't enrolled), "
        "and its open matchups and results.\n\n" + _STAFF
    ),
)
async def stage(bracket_id: int, actor: User = Depends(require_api_actor)):
    return await AdminBracketsService().stage(actor, bracket_id)


@router.get(
    '/{bracket_id}/qualifier-seeds', response_model=QualifierSeedingResponse,
    summary='Work out seeds from a qualifier',
    description=(
        "Seeds the stage's entries by an async qualifier's leaderboard, best place first, matched on "
        "linked accounts. Nothing is saved: send `seeds` to `PATCH /brackets/{bracket_id}/seeds`. "
        "Fails with 400 when no entry is on the leaderboard.\n\n" + _STAFF
    ),
)
async def qualifier_seeds(bracket_id: int, qualifier_id: int, actor: User = Depends(require_api_actor)):
    return await AdminBracketsService().seeds_from_qualifier(actor, bracket_id, qualifier_id)
