"""Taking a survey: the parts sent to you, saving each part, and sending your answers.

Only the person a survey was sent to can open it; anyone else gets a 404, so
the call never confirms the survey exists. Behind the Surveys flag.
"""

from typing import List

from fastapi import APIRouter, Depends

from api.dependencies import ServiceErrorRoute, event_binding, require_api_actor, require_feature, require_write_actor
from api.schemas.my_surveys import MyOpenSurvey, RespondentSurvey, SavePartRequest, SubmitRequest, respondent_out
from application.services import SurveyResponseService
from models import FeatureFlag, Survey, User

router = APIRouter(
    prefix='/me/surveys', tags=['My surveys'], route_class=ServiceErrorRoute,
    dependencies=[Depends(require_feature(FeatureFlag.SURVEYS)), Depends(event_binding(survey_id=Survey))],
)

_WHO = '\n\n**Who can call this:** The person the survey was sent to.'


@router.get(
    '',
    response_model=List[MyOpenSurvey],
    summary='List surveys waiting on me',
    description='Open surveys sent to you that you have not answered yet.\n\n**Who can call this:** Any member.',
)
async def my_open_surveys(actor: User = Depends(require_api_actor)) -> List[MyOpenSurvey]:
    surveys = await SurveyResponseService().my_open_surveys(actor)
    return [MyOpenSurvey(id=s.id, title=s.title, closes_at=s.closes_at) for s in surveys]


@router.get(
    '/{survey_id}',
    response_model=RespondentSurvey,
    summary='Open a survey sent to me',
    description=(
        'Your parts of the survey, with what you have saved so far and which required questions are '
        'still unanswered. Marks the survey opened.' + _WHO
    ),
)
async def open_survey(survey_id: int, actor: User = Depends(require_api_actor)) -> RespondentSurvey:
    return respondent_out(await SurveyResponseService().open_survey(actor, survey_id))


@router.post(
    '/{survey_id}/parts/{part_key}/open',
    status_code=204,
    summary='Note that I opened a part',
    description='Counts the part as reached, for the drop-off report. Does nothing once you have sent.' + _WHO,
)
async def open_part(survey_id: int, part_key: str, actor: User = Depends(require_write_actor)) -> None:
    await SurveyResponseService().open_part(actor, survey_id, part_key)


@router.put(
    '/{survey_id}/parts/{part_key}',
    response_model=RespondentSurvey,
    summary='Save my answers to one part',
    description=(
        'Saves the answers to one part (question id → answer); a blank answer clears it. Refused once '
        'the survey closed or you sent. Returns the whole survey again, since an answer can show or '
        'hide later questions and parts.' + _WHO
    ),
)
async def save_part(
    survey_id: int, part_key: str, body: SavePartRequest, actor: User = Depends(require_write_actor),
) -> RespondentSurvey:
    return respondent_out(await SurveyResponseService().save_part(actor, survey_id, part_key, body.answers))


@router.post(
    '/{survey_id}/submit',
    response_model=RespondentSurvey,
    summary='Send my answers',
    description=(
        'Sends what you saved. Refused while a required question is unanswered. Anonymous strips your '
        'name for good and keeps only the day.' + _WHO
    ),
)
async def submit(survey_id: int, body: SubmitRequest, actor: User = Depends(require_write_actor)) -> RespondentSurvey:
    service = SurveyResponseService()
    await service.submit(actor, survey_id, anonymous=body.anonymous)
    return respondent_out(await service.open_survey(actor, survey_id))


@router.post(
    '/{survey_id}/decline',
    status_code=204,
    summary='Not interested',
    description='No more reminders for this survey. The link keeps working.' + _WHO,
)
async def decline(survey_id: int, actor: User = Depends(require_write_actor)) -> None:
    await SurveyResponseService().decline(actor, survey_id)
