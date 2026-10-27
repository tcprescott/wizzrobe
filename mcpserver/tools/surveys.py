"""Post-event surveys: read results, and build surveys.

Mirrors ``api/routers/surveys.py``: staff only, gated behind ``SURVEYS``. The
writes build a survey (sections, questions, conditions) and nothing more;
sending, reminding and closing stay buttons in Admin → Surveys, so no tool here
DMs anyone, and only a draft can be deleted.

Comment text in results and responses is what respondents typed: plain text.
"""

from typing import List, Optional

from mcp.server.mcpserver import MCPServer

from api.schemas.surveys import (
    ConditionResponse,
    SubmittedResponse,
    SurveyQuestionResponse,
    SurveyResponseModel,
    SurveyResultsResponse,
    SurveySectionResponse,
    SurveySummaryResponse,
    question_out,
    response_out,
    results_out,
    section_out,
    summary_out,
    survey_out,
)
from application.api_scopes import ApiScope
from application.services import SurveyService
from application.services.survey import conditions
from mcpserver.auth import Gate, current_actor
from mcpserver.registry import register
from mcpserver.schemas import TenantArg
from mcpserver.tools._args import clamp
from models import FeatureFlag, Survey, SurveyAudience, SurveyQuestion, SurveyQuestionKind, SurveySection

_ROWS = {'survey_id': Survey, 'section_id': SurveySection, 'question_id': SurveyQuestion}


def _user():
    return current_actor().user


async def list_surveys(tenant: TenantArg = None) -> List[SurveySummaryResponse]:
    """The event's surveys, newest first, with how many people each went to and how many answered."""
    rows = await SurveyService().list_overview(_user())
    return [summary_out(r.survey, r.sent, r.answered) for r in rows]


async def get_survey(survey_id: int, tenant: TenantArg = None) -> SurveyResponseModel:
    """One survey with its sections and their questions, in order."""
    return survey_out(await SurveyService().get_survey(_user(), survey_id))


async def list_survey_conditions(tenant: TenantArg = None) -> List[ConditionResponse]:
    """The keys a question's `condition` takes. `answer` waits on an earlier question's answer."""
    return [ConditionResponse(key=k, label=v) for k, v in conditions.choices().items()]


async def get_survey_results(survey_id: int, tenant: TenantArg = None) -> SurveyResultsResponse:
    """Counts per question, split by tournament or position for a repeating section.

    A group with fewer than 5 answers shows only its count; an anonymous comment
    carries its group only when that group has 5 or more people.
    """
    return results_out(survey_id, await SurveyService().results(_user(), survey_id))


async def list_survey_responses(
    survey_id: int, tenant: TenantArg = None, limit: int = 100, offset: int = 0,
) -> List[SubmittedResponse]:
    """Submitted responses, newest first, with their answers. An anonymous one has no user."""
    rows = await SurveyService().list_responses(
        _user(), survey_id, limit=clamp(limit, low=1, high=500), offset=max(0, offset),
    )
    return [response_out(r) for r in rows]


async def create_survey(
    title: str, tenant: TenantArg = None, from_template: bool = True, intro: str = '',
    allow_anonymous: bool = True,
) -> SurveyResponseModel:
    """Create a draft survey in the event, from the starter questions unless `from_template` is false.

    Staff send it from Admin → Surveys; there's no tool for that.
    """
    return survey_out(await SurveyService().create_survey(
        _user(), title, from_template=from_template, intro=intro, allow_anonymous=allow_anonymous,
    ))


async def duplicate_survey(survey_id: int, tenant: TenantArg = None) -> SurveyResponseModel:
    """Copy a survey's sections, questions and conditions into a new draft."""
    return survey_out(await SurveyService().duplicate(_user(), survey_id))


async def update_survey(
    survey_id: int, tenant: TenantArg = None, title: Optional[str] = None, intro: Optional[str] = None,
    allow_anonymous: Optional[bool] = None,
) -> SurveyResponseModel:
    """Change a draft's title, intro or anonymity switch. Leave a field out to keep it."""
    fields = {k: v for k, v in (('title', title), ('intro', intro), ('allow_anonymous', allow_anonymous))
              if v is not None}
    return survey_out(await SurveyService().update_survey(_user(), survey_id, **fields))


async def delete_survey(survey_id: int, tenant: TenantArg = None) -> str:
    """Delete a draft survey. A sent one, and its answers, can only be deleted in the app."""
    await SurveyService().delete_survey(_user(), survey_id, drafts_only=True)
    return 'Deleted.'


async def add_survey_section(
    survey_id: int, title: str, tenant: TenantArg = None, audience: SurveyAudience = SurveyAudience.EVERYONE,
) -> SurveySectionResponse:
    """Add a section to the end of a draft.

    `each_tournament` and `each_position` repeat once for every tournament a
    person played or position they worked, so keep those short.
    """
    service = SurveyService()
    section = await service.add_section(_user(), survey_id, title, audience)
    survey = await service.get_survey(_user(), survey_id)
    return section_out(next(s for s in survey.sections if s.id == section.id))


async def update_survey_section(
    section_id: int, tenant: TenantArg = None, title: Optional[str] = None,
    audience: Optional[SurveyAudience] = None,
) -> SurveySectionResponse:
    """Change a draft section's title or audience. Leave a field out to keep it."""
    fields = {k: v for k, v in (('title', title), ('audience', audience)) if v is not None}
    service = SurveyService()
    section = await service.update_section(_user(), section_id, **fields)
    survey = await service.get_survey(_user(), section.survey_id)
    return section_out(next(s for s in survey.sections if s.id == section_id))


async def delete_survey_section(section_id: int, tenant: TenantArg = None) -> str:
    """Delete a draft section and its questions."""
    await SurveyService().delete_section(_user(), section_id)
    return 'Deleted.'


async def move_survey_section(section_id: int, step: int, tenant: TenantArg = None) -> str:
    """Move a draft section one place up (`step` -1) or down (1)."""
    await SurveyService().move_section(_user(), section_id, step)
    return 'Moved.'


async def add_survey_question(
    section_id: int, prompt: str, kind: SurveyQuestionKind, tenant: TenantArg = None,
    options: Optional[List[str]] = None, required: bool = False, condition: Optional[str] = None,
    condition_question_id: Optional[int] = None, condition_values: Optional[List[str]] = None,
) -> SurveyQuestionResponse:
    """Add a question to the end of a draft section.

    `options` holds 2 to 10 choices for `choice_one` and `choice_many`.
    `condition` is a key from `list_survey_conditions`; for `answer`, name the
    earlier question in `condition_question_id` and the answers that show this
    one in `condition_values` (numbers as strings for a rating).
    """
    question = await SurveyService().add_question(
        _user(), section_id, prompt, kind, options=options, required=required, condition=condition,
        condition_args=_answer_args(condition, condition_question_id, condition_values),
    )
    return question_out(question)


async def update_survey_question(
    question_id: int, tenant: TenantArg = None, prompt: Optional[str] = None,
    kind: Optional[SurveyQuestionKind] = None, options: Optional[List[str]] = None,
    required: Optional[bool] = None, condition: Optional[str] = None,
    condition_question_id: Optional[int] = None, condition_values: Optional[List[str]] = None,
) -> SurveyQuestionResponse:
    """Change a draft question. Leave a field out to keep it; `condition` "" always shows it."""
    fields: dict = {k: v for k, v in (('prompt', prompt), ('kind', kind), ('options', options),
                                      ('required', required)) if v is not None}
    if condition is not None:
        fields['condition'] = condition
        fields['condition_args'] = _answer_args(condition, condition_question_id, condition_values)
    return question_out(await SurveyService().update_question(_user(), question_id, **fields))


async def delete_survey_question(question_id: int, tenant: TenantArg = None) -> str:
    """Delete a draft question. Refused while a later question's condition follows it."""
    await SurveyService().delete_question(_user(), question_id)
    return 'Deleted.'


async def move_survey_question(question_id: int, step: int, tenant: TenantArg = None) -> str:
    """Move a draft question one place up (`step` -1) or down (1) inside its section."""
    await SurveyService().move_question(_user(), question_id, step)
    return 'Moved.'


def _answer_args(condition: Optional[str], question_id: Optional[int], values: Optional[List[str]]) -> Optional[dict]:
    if condition != conditions.ANSWER:
        return None
    parsed = [int(v) if isinstance(v, str) and v.isdigit() else v for v in values or []]
    return {'question_id': question_id, 'values': parsed}


def register_tools(mcp: MCPServer) -> None:
    feature = FeatureFlag.SURVEYS

    def read(fn, title: str) -> None:
        register(mcp, fn, gate=Gate.STAFF, scope=ApiScope.STAFF_READ, feature=feature, title=title, event=_ROWS)

    def write(fn, title: str, *, destructive: bool = False) -> None:
        register(mcp, fn, gate=Gate.STAFF, scope=ApiScope.STAFF_WRITE, feature=feature, title=title,
                 write=True, destructive=destructive, event=_ROWS)

    read(list_surveys, 'List surveys')
    read(get_survey, 'Get survey')
    read(list_survey_conditions, 'List survey conditions')
    read(get_survey_results, 'Get survey results')
    read(list_survey_responses, 'List survey responses')
    write(create_survey, 'Create survey')
    write(duplicate_survey, 'Duplicate survey')
    write(update_survey, 'Update survey')
    write(delete_survey, 'Delete draft survey', destructive=True)
    write(add_survey_section, 'Add survey section')
    write(update_survey_section, 'Update survey section')
    write(delete_survey_section, 'Delete survey section', destructive=True)
    write(move_survey_section, 'Move survey section')
    write(add_survey_question, 'Add survey question')
    write(update_survey_question, 'Update survey question')
    write(delete_survey_question, 'Delete survey question', destructive=True)
    write(move_survey_question, 'Move survey question')
