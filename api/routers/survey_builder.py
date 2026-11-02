"""The app's survey builder and preview: the time box and checks, AI suggestions, comment tags.

Staff only, behind the Surveys flag (the services check both again). Send,
remind and close DM people, so only Wizzrobe's own app may call them: a
personal token or another app gets a 403 (Thomas's call).
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status

from api.dependencies import (
    ServiceErrorRoute,
    event_binding,
    require_app_staff_write,
    require_feature,
    require_staff,
    require_staff_write,
)
from api.schemas.my_surveys import PreviewPathResponse, RespondentSurvey, preview_out
from api.schemas.survey_builder import (
    BuilderResponse,
    KeepRequest,
    PlaceRequest,
    SendRequest,
    SendResult,
    SentContextResponse,
    SuggestionBatchResponse,
    TagRequest,
    batch_out,
    builder_out,
)
from api.schemas.surveys import (
    SurveyCommentTagResponse,
    SurveyQuestionResponse,
    SurveySummaryResponse,
    question_out,
    summary_out,
)
from application.services import SurveyCommentService, SurveyService, SurveySuggestionService
from application.services.survey import plan
from models import FeatureFlag, Survey, SurveyQuestion, SurveySection, User

router = APIRouter(
    prefix='/surveys', tags=['Surveys'], route_class=ServiceErrorRoute,
    dependencies=[
        Depends(require_feature(FeatureFlag.SURVEYS)),
        Depends(event_binding(survey_id=Survey, section_id=SurveySection, question_id=SurveyQuestion)),
    ],
)

_STAFF = '\n\n**Who can call this:** Staff.'


@router.get(
    '/{survey_id}/builder',
    response_model=BuilderResponse,
    summary="The builder's time box and checks",
    description=(
        'How long the survey takes most people and the person with the most to answer, worked out '
        'over everyone who took part in its event; the checks (empty parts, unreachable questions, '
        'over-long parts...); whether Suggest questions is set up; and any AI drafts still waiting.' + _STAFF
    ),
)
async def builder(survey_id: int, actor: User = Depends(require_staff)) -> BuilderResponse:
    summary = await SurveyService().builder_summary(actor, survey_id)
    suggestions = SurveySuggestionService()
    provider = await suggestions.provider_label(actor)
    batches = list((await suggestions.open_batches(actor, survey_id)).values())
    return builder_out(summary, provider, batches, plan.TARGET_SECONDS, plan.BUDGET_SECONDS)


@router.get(
    '/{survey_id}/preview-paths',
    response_model=List[PreviewPathResponse],
    summary='Who sees what',
    description='One row per kind of person: their parts, questions and time, and what they won\'t see and why.' + _STAFF,
)
async def preview_paths(survey_id: int, actor: User = Depends(require_staff)) -> List[PreviewPathResponse]:
    rows = await SurveyService().preview_paths(actor, survey_id)
    return [
        PreviewPathResponse(
            persona=r.persona, label=r.label, parts=r.parts, questions=r.questions, seconds=r.seconds,
            hidden=[f'{h.title}: {h.prompt} ({h.reason})' if h.prompt else f'{h.title} ({h.reason})' for h in r.hidden],
        )
        for r in rows
    ]


@router.get(
    '/{survey_id}/preview',
    response_model=RespondentSurvey,
    summary='Preview the survey as one kind of person',
    description=(
        'The parts that kind of person would see, in the shape `GET /me/surveys/{id}` returns. '
        '`persona` comes from `preview-paths`; leave it out for every question. Nothing is saved.' + _STAFF
    ),
)
async def preview(
    survey_id: int, persona: Optional[str] = Query(None, description='A `persona` from `preview-paths`.'),
    actor: User = Depends(require_staff),
) -> RespondentSurvey:
    survey, parts = await SurveyService().preview(actor, survey_id, persona or 'everything')
    return preview_out(survey, parts)


@router.post(
    '/questions/{question_id}/place',
    status_code=status.HTTP_204_NO_CONTENT,
    summary='Drop a question somewhere else',
    description=(
        'Puts the question at `index` in `section_id`, which may be another section. Refused when it '
        'would land above a question it follows. Drafts only.' + _STAFF
    ),
)
async def place_question(question_id: int, body: PlaceRequest, actor: User = Depends(require_staff_write)) -> None:
    await SurveyService().place_question(actor, question_id, body.section_id, body.index)


@router.post(
    '/{survey_id}/comment-tags',
    response_model=SurveyCommentTagResponse,
    summary='Add a comment tag',
    description='Up to 20 per survey, names unique ignoring case.' + _STAFF,
)
async def add_tag(survey_id: int, body: TagRequest, actor: User = Depends(require_staff_write)):
    tag = await SurveyCommentService().add_tag(actor, survey_id, body.name)
    return SurveyCommentTagResponse(id=tag.id, name=tag.name, count=tag.count)


@router.delete(
    '/{survey_id}/comment-tags/{tag_id}',
    status_code=status.HTTP_204_NO_CONTENT,
    summary='Delete a comment tag',
    description='Comments that carried it just lose it.' + _STAFF,
)
async def delete_tag(survey_id: int, tag_id: int, actor: User = Depends(require_staff_write)) -> None:
    await SurveyCommentService().delete_tag(actor, survey_id, tag_id)


@router.post(
    '/sections/{section_id}/suggestions',
    response_model=SuggestionBatchResponse,
    summary='Suggest questions for a section',
    description=(
        "Asks the community's AI provider for up to five draft questions. Once per press; capped per "
        'day under Settings → Integrations. Drafts only.' + _STAFF
    ),
)
async def suggest(section_id: int, actor: User = Depends(require_staff_write)) -> SuggestionBatchResponse:
    return batch_out(await SurveySuggestionService().suggest(actor, section_id))


@router.post(
    '/suggestions/{batch_id}/keep',
    response_model=SurveyQuestionResponse,
    summary='Keep a suggested question',
    description='Adds the draft to its section as an ordinary question.' + _STAFF,
)
async def keep(batch_id: int, body: KeepRequest, actor: User = Depends(require_staff_write)):
    return question_out(await SurveySuggestionService().keep(actor, batch_id, body.index))


@router.post(
    '/suggestions/{batch_id}/dismiss',
    status_code=status.HTTP_204_NO_CONTENT,
    summary='Dismiss the rest of a batch of suggestions',
    description=_STAFF.strip(),
)
async def dismiss(batch_id: int, actor: User = Depends(require_staff_write)) -> None:
    await SurveySuggestionService().dismiss(actor, batch_id)


@router.get(
    '/suggestions/{batch_id}/sent',
    response_model=SentContextResponse,
    summary='What was sent to the AI',
    description='The exact text the provider was given for this batch.' + _STAFF,
)
async def sent_context(batch_id: int, actor: User = Depends(require_staff)) -> SentContextResponse:
    return SentContextResponse(context=await SurveySuggestionService().sent_context(actor, batch_id))


_APP = '\n\n**Who can call this:** Staff, from the Wizzrobe app only. Personal tokens and other apps get a 403.'


@router.post(
    '/{survey_id}/send',
    response_model=SendResult,
    summary='Send a survey',
    description=(
        'Opens the draft and DMs everyone who took part in its event. Refused with no questions, '
        'nobody to send to, or a close time in the past.' + _APP
    ),
)
async def send(survey_id: int, body: SendRequest, actor: User = Depends(require_app_staff_write)) -> SendResult:
    return SendResult(people=await SurveyService().send(actor, survey_id, closes_at=body.closes_at))


@router.post(
    '/{survey_id}/remind',
    response_model=SendResult,
    summary='Remind people who have not answered',
    description=(
        'DMs everyone still waiting who didn\'t say "Not interested". Once a day, two in all; the '
        'second is a last call.' + _APP
    ),
)
async def remind(survey_id: int, actor: User = Depends(require_app_staff_write)) -> SendResult:
    return SendResult(people=await SurveyService().remind(actor, survey_id))


@router.post(
    '/{survey_id}/close',
    response_model=SurveySummaryResponse,
    summary='Close a survey',
    description='Stops taking answers. Anything saved but not sent stays unsent.' + _APP,
)
async def close(survey_id: int, actor: User = Depends(require_app_staff_write)) -> SurveySummaryResponse:
    return summary_out(await SurveyService().close(actor, survey_id))
