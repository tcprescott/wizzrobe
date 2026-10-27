"""Post-event survey endpoints: read results, and build surveys.

Gated by the ``SURVEYS`` feature flag at the router and again inside
``SurveyService``. Staff only. These build a survey (its sections, questions
and conditions) and read what came back; sending, reminding and closing stay
buttons in Admin → Surveys, so nothing here DMs anyone. Deleting is for drafts:
answers people gave are deleted only from the app.

Comment text is what respondents typed. It's returned as plain text and must
never be rendered as markup.
"""

from typing import List

from fastapi import APIRouter, Depends, Query, status

from api.dependencies import ServiceErrorRoute, event_binding, require_staff, require_staff_write
from api.schemas.surveys import (
    ConditionResponse,
    MoveRequest,
    QuestionRequest,
    QuestionUpdateRequest,
    SectionRequest,
    SectionUpdateRequest,
    SubmittedResponse,
    SurveyCreateRequest,
    SurveyQuestionResponse,
    SurveyResponseModel,
    SurveyResultsResponse,
    SurveySectionResponse,
    SurveySummaryResponse,
    SurveyUpdateRequest,
    conditions_in,
    conditions_out,
    question_out,
    response_out,
    results_out,
    section_out,
    summary_out,
    survey_out,
)
from application.services import SurveyService
from application.services.survey import conditions
from models import Survey, SurveyQuestion, SurveySection, User

router = APIRouter(
    prefix="/surveys", tags=["Surveys"], route_class=ServiceErrorRoute,
    dependencies=[Depends(event_binding(survey_id=Survey, section_id=SurveySection, question_id=SurveyQuestion))],
)

MAX_ROWS = 500


@router.get(
    "",
    response_model=List[SurveySummaryResponse],
    summary="List the event's surveys",
    description=(
        "Lists the surveys in the event (the community's default event unless `?event=` names "
        "another), newest first, with how many people each went to and how many answered."
        "\n\n**Who can call this:** Staff."
    ),
)
async def list_surveys(actor: User = Depends(require_staff)):
    rows = await SurveyService().list_overview(actor)
    return [summary_out(r.survey, r.sent, r.answered) for r in rows]


@router.get(
    "/conditions",
    response_model=List[ConditionResponse],
    summary="List the conditions a question can wait on",
    description=(
        "The keys a question's or section's `conditions` take. A question has up to 3 and a "
        "section 1, and all of them must hold. `answer` waits on an earlier question's answer "
        "and takes `question_id` and `values`."
        "\n\n**Who can call this:** Staff."
    ),
)
async def list_conditions(actor: User = Depends(require_staff)):
    return [ConditionResponse(key=k, label=v) for k, v in conditions.choices().items()]


@router.get(
    "/{survey_id}",
    response_model=SurveyResponseModel,
    summary="Get a survey with its sections and questions",
    description="Returns the survey, its sections in order and each section's questions.\n\n**Who can call this:** Staff.",
)
async def get_survey(survey_id: int, actor: User = Depends(require_staff)):
    return survey_out(await SurveyService().get_survey(actor, survey_id))


@router.get(
    "/{survey_id}/results",
    response_model=SurveyResultsResponse,
    summary="Get a survey's results",
    description=(
        "Counts per question, split by tournament or position for a repeating section. A group "
        "with fewer than 5 answers shows only its count. Anonymous comments carry their group "
        "only when it has 5 or more people.\n\n**Who can call this:** Staff."
    ),
)
async def get_results(survey_id: int, actor: User = Depends(require_staff)):
    return results_out(survey_id, await SurveyService().results(actor, survey_id))


@router.get(
    "/{survey_id}/responses",
    response_model=List[SubmittedResponse],
    summary="List a survey's submitted responses",
    description=(
        "Every submitted response, newest first, with its answers. An anonymous one carries no "
        "user and only the day it was sent.\n\n**Who can call this:** Staff."
    ),
)
async def list_responses(
    survey_id: int,
    limit: int = Query(100, ge=1, le=MAX_ROWS, description="Maximum rows to return."),
    offset: int = Query(0, ge=0, description="Rows to skip."),
    actor: User = Depends(require_staff),
):
    rows = await SurveyService().list_responses(actor, survey_id, limit=limit, offset=offset)
    return [response_out(r) for r in rows]


@router.post(
    "",
    response_model=SurveyResponseModel,
    status_code=status.HTTP_201_CREATED,
    summary="Create a draft survey",
    description=(
        "Creates a draft in the event, filled from the starter questions unless `from_template` "
        "is false. Send it from Admin → Surveys.\n\n**Who can call this:** Staff."
    ),
)
async def create_survey(body: SurveyCreateRequest, actor: User = Depends(require_staff_write)):
    survey = await SurveyService().create_survey(
        actor, body.title, from_template=body.from_template, intro=body.intro, allow_anonymous=body.allow_anonymous,
    )
    return survey_out(survey)


@router.post(
    "/{survey_id}/duplicate",
    response_model=SurveyResponseModel,
    status_code=status.HTTP_201_CREATED,
    summary="Copy a survey into a new draft",
    description="A new draft with the same sections, questions and conditions.\n\n**Who can call this:** Staff.",
)
async def duplicate_survey(survey_id: int, actor: User = Depends(require_staff_write)):
    return survey_out(await SurveyService().duplicate(actor, survey_id))


@router.patch(
    "/{survey_id}",
    response_model=SurveyResponseModel,
    summary="Change a survey's title, intro, anonymity or close time",
    description=(
        "Only the fields you send change. A sent survey takes only `closes_at`.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def update_survey(survey_id: int, body: SurveyUpdateRequest, actor: User = Depends(require_staff_write)):
    fields = body.model_dump(exclude_unset=True)
    return survey_out(await SurveyService().update_survey(actor, survey_id, **fields))


@router.delete(
    "/{survey_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a draft survey",
    description="Deletes a draft. A sent survey is deleted only from Admin → Surveys.\n\n**Who can call this:** Staff.",
)
async def delete_survey(survey_id: int, actor: User = Depends(require_staff_write)):
    await SurveyService().delete_survey(actor, survey_id, drafts_only=True)


@router.post(
    "/{survey_id}/sections",
    response_model=SurveySectionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a section to a draft",
    description=(
        "Adds a section at the end. `each_tournament` and `each_position` repeat once per "
        "tournament played or position worked.\n\n**Who can call this:** Staff."
    ),
)
async def add_section(survey_id: int, body: SectionRequest, actor: User = Depends(require_staff_write)):
    section = await SurveyService().add_section(
        actor, survey_id, body.title, body.audience, conditions=conditions_in(body.conditions),
    )
    return SurveySectionResponse(id=section.id, title=section.title, audience=section.audience.value,
                                 position=section.position, conditions=conditions_out(section.conditions))


@router.patch(
    "/sections/{section_id}",
    response_model=SurveySectionResponse,
    summary="Change a section's title, audience or condition",
    description=(
        "Only the fields you send change. A section's condition may wait on a question in an "
        "earlier section. Drafts only.\n\n**Who can call this:** Staff."
    ),
)
async def update_section(section_id: int, body: SectionUpdateRequest, actor: User = Depends(require_staff_write)):
    fields = body.model_dump(exclude_unset=True)
    if 'conditions' in fields:
        fields['conditions'] = conditions_in(body.conditions) or []
    service = SurveyService()
    section = await service.update_section(actor, section_id, **fields)
    survey = await service.get_survey(actor, section.survey_id)
    return section_out(next(s for s in survey.sections if s.id == section_id))


@router.delete(
    "/sections/{section_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a section and its questions",
    description="Drafts only.\n\n**Who can call this:** Staff.",
)
async def delete_section(section_id: int, actor: User = Depends(require_staff_write)):
    await SurveyService().delete_section(actor, section_id)


@router.post(
    "/sections/{section_id}/move",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Move a section up or down",
    description="`step` -1 moves it one place up, 1 one place down. Drafts only.\n\n**Who can call this:** Staff.",
)
async def move_section(section_id: int, body: MoveRequest, actor: User = Depends(require_staff_write)):
    await SurveyService().move_section(actor, section_id, body.step)


@router.post(
    "/sections/{section_id}/questions",
    response_model=SurveyQuestionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a question to a section",
    description=(
        "Adds a question at the end of the section. Fails with 400 when a choice question has "
        "fewer than 2 or more than 10 choices, it has more than 3 conditions, or an `answer` "
        "condition names a question that isn't earlier in the survey. Drafts only.\n\n**Who can call this:** Staff."
    ),
)
async def add_question(section_id: int, body: QuestionRequest, actor: User = Depends(require_staff_write)):
    question = await SurveyService().add_question(
        actor, section_id, body.prompt, body.kind, options=body.options, required=body.required,
        conditions=conditions_in(body.conditions),
    )
    return question_out(question)


@router.patch(
    "/questions/{question_id}",
    response_model=SurveyQuestionResponse,
    summary="Change a question",
    description=(
        "Only the fields you send change. Send `conditions` as `[]` to always show it. "
        "Drafts only.\n\n**Who can call this:** Staff."
    ),
)
async def update_question(question_id: int, body: QuestionUpdateRequest, actor: User = Depends(require_staff_write)):
    fields = body.model_dump(exclude_unset=True)
    if 'conditions' in fields:
        fields['conditions'] = conditions_in(body.conditions) or []
    return question_out(await SurveyService().update_question(actor, question_id, **fields))


@router.delete(
    "/questions/{question_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a question",
    description=(
        "Fails with 400 while a later question's or section's condition follows it. Drafts only.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def delete_question(question_id: int, actor: User = Depends(require_staff_write)):
    await SurveyService().delete_question(actor, question_id)


@router.post(
    "/questions/{question_id}/move",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Move a question up or down inside its section",
    description="`step` -1 moves it one place up, 1 one place down. Drafts only.\n\n**Who can call this:** Staff.",
)
async def move_question(question_id: int, body: MoveRequest, actor: User = Depends(require_staff_write)):
    await SurveyService().move_question(actor, question_id, body.step)
