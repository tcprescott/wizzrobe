"""A tournament's match form: the questions players answer before a match.

Staff build it under the tournament; players read and answer it on their own
match. Seed rules read the answers, and answers lock once the seed rolls.
"""

from fastapi import APIRouter, Depends

from api.dependencies import ServiceErrorRoute, event_binding, require_api_actor, require_write_actor
from api.schemas.match_form import MatchFormEditorResponse, MatchFormUpdate, PlayerAnswer, PlayerFormResponse
from application.services import MatchFormService
from models import Match, Tournament, User

router = APIRouter(
    tags=['Match form'], route_class=ServiceErrorRoute,
    dependencies=[Depends(event_binding(tournament_id=Tournament, match_id=Match))],
)

_STAFF = "**Who can call this:** Staff, a preset manager, or an admin of this tournament."
_PLAYER = "**Who can call this:** The match's players."


@router.get(
    '/admin/tournaments/{tournament_id}/match-form', response_model=MatchFormEditorResponse,
    summary="A tournament's match form",
    description="The questions in order, each with the seed rules that read it.\n\n" + _STAFF,
)
async def get_match_form(tournament_id: int, actor: User = Depends(require_api_actor)):
    return {'tournament_id': tournament_id, 'questions': await MatchFormService().get_form(tournament_id, actor)}


@router.put(
    '/admin/tournaments/{tournament_id}/match-form', response_model=MatchFormEditorResponse,
    summary='Replace the match form',
    description=(
        "Saves the whole list. A bad question fails with 400 naming it (`Question 2: …`), and so does "
        "removing a question a seed rule still reads. Answers to a removed question go with it."
        "\n\n" + _STAFF
    ),
)
async def save_match_form(tournament_id: int, body: MatchFormUpdate, actor: User = Depends(require_write_actor)):
    questions = [q.model_dump(mode='json', exclude_none=True) for q in body.questions]
    return {'tournament_id': tournament_id, 'questions': await MatchFormService().save_form(tournament_id, questions, actor)}


@router.get(
    '/matches/{match_id}/form', response_model=PlayerFormResponse, summary='Your match form',
    description=(
        "The questions this match asks you, your answers, and the other players' answers each "
        "question lets you see. Empty when there's nothing to answer.\n\n" + _PLAYER
    ),
)
async def get_player_form(match_id: int, actor: User = Depends(require_api_actor)):
    return await MatchFormService().player_form(match_id, actor)


@router.put(
    '/matches/{match_id}/form/{key}', response_model=PlayerFormResponse, summary='Answer a match form question',
    description=(
        "Saves, changes or (with a null `value`) clears your answer. Fails with 400 once the seed is "
        "rolled, or when the value isn't one of the question's choices.\n\n" + _PLAYER
    ),
)
async def answer_player_form(match_id: int, key: str, body: PlayerAnswer, actor: User = Depends(require_write_actor)):
    return await MatchFormService().answer(match_id, actor, key, body.value)
