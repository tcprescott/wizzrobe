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
    SurveyCommentsResponse,
    SurveyConditionModel,
    SurveyQuestionResponse,
    SurveyResponseModel,
    SurveyResultsResponse,
    SurveySectionResponse,
    SurveySummaryResponse,
    comments_out,
    conditions_in,
    question_out,
    response_out,
    results_out,
    section_out,
    summary_out,
    survey_out,
)
from application.api_scopes import ApiScope
from application.services import SurveyCommentService, SurveyService
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
    """The keys a question's or section's `conditions` take. `answer` waits on an earlier question's answer."""
    return [ConditionResponse(key=k, label=v) for k, v in conditions.choices().items()]


async def get_survey_results(
    survey_id: int, tenant: TenantArg = None, group: Optional[str] = None,
) -> SurveyResultsResponse:
    """Counts per question, split by tournament or position for a repeating section.

    Groups with fewer than 5 answers fold into `Other`; an anonymous comment
    carries its group only when that group has 5 or more people. Pass `group`
    (a key from the result's `groups`) to narrow every number to that group.
    """
    return results_out(survey_id, await SurveyService().results(_user(), survey_id, group=group))


async def list_survey_comments(
    survey_id: int, tenant: TenantArg = None, unread_only: bool = False,
) -> SurveyCommentsResponse:
    """Every comment and `Other` write-in on a survey, newest first, with staff's read marks and tags.

    Read marks and tags are shared by all staff and never shown to respondents.
    Comment text is what people typed: plain text, never markup.
    """
    inbox = await SurveyCommentService().inbox(_user(), survey_id)
    out = comments_out(inbox)
    if unread_only:
        out.comments = [c for c in out.comments if not c.read]
    return out


async def mark_survey_comment(
    survey_id: int, answer_id: int, tenant: TenantArg = None,
    read: Optional[bool] = None, tags: Optional[List[int]] = None,
) -> str:
    """Mark one survey comment read (`read` true) or unread (false), and/or set its tags (ids from
    `list_survey_comments`). Leave a field out to keep it. Shared by all staff."""
    await SurveyCommentService().mark(_user(), survey_id, answer_id, read=read, tags=tags)
    return 'Saved.'


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
    allow_anonymous: Optional[bool] = None, after_send: Optional[str] = None,
) -> SurveyResponseModel:
    """Change a survey's title, intro, "after you send" line or anonymity switch. Leave a field out to keep it.

    Once sent, only the wording (title, intro, after_send) can change."""
    fields = {k: v for k, v in (('title', title), ('intro', intro), ('allow_anonymous', allow_anonymous),
                                ('after_send', after_send)) if v is not None}
    return survey_out(await SurveyService().update_survey(_user(), survey_id, **fields))


async def delete_survey(survey_id: int, tenant: TenantArg = None) -> str:
    """Delete a draft survey. A sent one, and its answers, can only be deleted in the app."""
    await SurveyService().delete_survey(_user(), survey_id, drafts_only=True)
    return 'Deleted.'


async def add_survey_section(
    survey_id: int, title: str, tenant: TenantArg = None, audience: SurveyAudience = SurveyAudience.EVERYONE,
    conditions: Optional[List[SurveyConditionModel]] = None, description: Optional[str] = None,
) -> SurveySectionResponse:
    """Add a section to the end of a draft.

    `each_tournament` and `each_position` repeat once for every tournament a
    person played or position they worked, so keep those short. `conditions`
    holds at most one entry, shaped as for `add_survey_question`; an `answer`
    one waits on a question in an earlier section. `description` is one or two
    plain-text lines under the title.
    """
    service = SurveyService()
    section = await service.add_section(_user(), survey_id, title, audience, conditions=conditions_in(conditions),
                                        description=description)
    survey = await service.get_survey(_user(), survey_id)
    return section_out(next(s for s in survey.sections if s.id == section.id))


async def update_survey_section(
    section_id: int, tenant: TenantArg = None, title: Optional[str] = None,
    audience: Optional[SurveyAudience] = None, conditions: Optional[List[SurveyConditionModel]] = None,
    description: Optional[str] = None,
) -> SurveySectionResponse:
    """Change a section's title, description, audience or condition. Once sent, only title and description.

    Leave a field out to keep it; `[]` clears the condition and `""` the description.
    """
    fields: dict = {k: v for k, v in (('title', title), ('audience', audience), ('description', description))
                    if v is not None}
    if conditions is not None:
        fields['conditions'] = conditions_in(conditions)
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
    options: Optional[List[str]] = None, required: bool = False,
    conditions: Optional[List[SurveyConditionModel]] = None, other_option: bool = False,
    none_option: Optional[str] = None, min_picks: Optional[int] = None, max_picks: Optional[int] = None,
    long_answer: bool = False,
) -> SurveyQuestionResponse:
    """Add a question to the end of a draft section.

    `options` holds 2 to 10 choices for `choice_one` and `choice_many`, or 2 to 10
    statements for `agree_scale` (each answered Strongly disagree 1 to Strongly agree 5).
    `conditions` holds up to 3 entries that must all hold, each `{"key": …}`
    with a key from `list_survey_conditions`; an `answer` entry also names the
    earlier question in `question_id` and the answers that show this one in
    `values` (any of them).

    Choice questions can add an `Other` choice with a write-in (`other_option`).
    A `choice_many` can add an exclusive "none" choice (`none_option`, its
    label) and pick limits (`min_picks`, `max_picks`, e.g. max 3 for "pick your
    top 3"). `long_answer` makes a `comment` a tall box of up to 3000 characters.
    """
    question = await SurveyService().add_question(
        _user(), section_id, prompt, kind, options=options, required=required, conditions=conditions_in(conditions),
        other_option=other_option, none_option=none_option, min_picks=min_picks, max_picks=max_picks,
        long_answer=long_answer,
    )
    return question_out(question)


async def update_survey_question(
    question_id: int, tenant: TenantArg = None, prompt: Optional[str] = None,
    kind: Optional[SurveyQuestionKind] = None, options: Optional[List[str]] = None,
    required: Optional[bool] = None, conditions: Optional[List[SurveyConditionModel]] = None,
    other_option: Optional[bool] = None, none_option: Optional[str] = None, min_picks: Optional[int] = None,
    max_picks: Optional[int] = None, long_answer: Optional[bool] = None,
) -> SurveyQuestionResponse:
    """Change a question (once sent, only its `prompt`). Leave a field out to keep it; `conditions` `[]` always shows it.

    `none_option` `""` and a pick limit of 0 remove them.
    """
    fields: dict = {k: v for k, v in (('prompt', prompt), ('kind', kind), ('options', options),
                                      ('required', required), ('other_option', other_option),
                                      ('none_option', none_option), ('min_picks', min_picks),
                                      ('max_picks', max_picks), ('long_answer', long_answer)) if v is not None}
    if conditions is not None:
        fields['conditions'] = conditions_in(conditions)
    return question_out(await SurveyService().update_question(_user(), question_id, **fields))


async def delete_survey_question(question_id: int, tenant: TenantArg = None) -> str:
    """Delete a draft question. Refused while a later question's or section's condition follows it."""
    await SurveyService().delete_question(_user(), question_id)
    return 'Deleted.'


async def move_survey_question(question_id: int, step: int, tenant: TenantArg = None) -> str:
    """Move a draft question one place up (`step` -1) or down (1) inside its section."""
    await SurveyService().move_question(_user(), question_id, step)
    return 'Moved.'


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
    read(list_survey_comments, 'List survey comments')
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
    write(mark_survey_comment, 'Mark survey comment')
