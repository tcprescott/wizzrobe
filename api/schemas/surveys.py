"""Schemas for post-event survey endpoints."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from application.services import SurveyResults
from application.services.survey import response_answers
from application.services.survey.results import Breakdown
from models import Survey, SurveyAudience, SurveyQuestion, SurveyQuestionKind, SurveyResponse, SurveySection


class SurveyQuestionResponse(BaseModel):
    id: int = Field(..., description="Question ID.")
    prompt: str = Field(..., description="The question as respondents read it.")
    kind: str = Field(..., description="`rating` (1-5), `nps` (0-10), `yes_no`, `choice_one`, `choice_many` or `comment`.")
    options: Optional[List[str]] = Field(None, description="The choices of a `choice_one` or `choice_many` question.")
    required: bool = Field(..., description="Whether it must be answered before sending.")
    position: int = Field(..., description="Order inside its section, from 0.")
    condition: Optional[str] = Field(None, description="The one condition a respondent must meet to see it; see `GET /surveys/conditions`.")
    condition_args: Optional[Dict[str, Any]] = Field(
        None, description="For `answer`: `{question_id, values}`, the earlier question and the answers that show this one.",
    )


class SurveySectionResponse(BaseModel):
    id: int = Field(..., description="Section ID.")
    title: str = Field(..., description="Section title.")
    audience: str = Field(..., description="Who sees it: `everyone`, `each_tournament`, `each_position`, `crew` or `checked_in`.")
    position: int = Field(..., description="Order in the survey, from 0.")
    questions: List[SurveyQuestionResponse] = Field(default_factory=list)


class SurveySummaryResponse(BaseModel):
    id: int = Field(..., description="Survey ID.")
    title: str = Field(..., description="Survey title.")
    status: str = Field(..., description="`draft`, `open` or `closed`.")
    allow_anonymous: bool = Field(..., description="Whether respondents may answer anonymously.")
    sent_at: Optional[datetime] = Field(None, description="When it was sent (UTC).")
    closes_at: Optional[datetime] = Field(None, description="When it stops taking answers (UTC).")
    recipients: int = Field(0, description="How many people it was sent to.")
    answered: int = Field(0, description="How many of them sent answers.")


class SurveyResponseModel(SurveySummaryResponse):
    intro: str = Field('', description="Plain text shown above the parts.")
    sections: List[SurveySectionResponse] = Field(default_factory=list)


class BreakdownResponse(BaseModel):
    count: int = Field(..., description="How many answers.")
    average: Optional[float] = Field(None, description="Average, for a rating or 0-10 question.")
    score: Optional[int] = Field(None, description="Recommend score (-100 to 100), for a 0-10 question.")
    counts: Dict[str, int] = Field(default_factory=dict, description="Answers per option.")
    hidden: bool = Field(False, description="True when fewer than 5 answered, so only the count is shown.")


class CommentResponse(BaseModel):
    text: str = Field(..., description="What the respondent wrote. Plain text: never render it as markup.")
    who: str = Field(..., description="Their name, or `Anonymous` (with their group when it has 5 or more people).")
    group: str = Field('', description="The tournament or position it was about, when that can be shown.")


class QuestionResultResponse(BaseModel):
    question_id: int
    section_id: int
    section_title: str
    prompt: str
    kind: str
    overall: BreakdownResponse
    groups: Dict[str, BreakdownResponse] = Field(
        default_factory=dict, description="Per tournament or position, for a repeating section.",
    )
    comments: List[CommentResponse] = Field(default_factory=list)


class SurveyResultsResponse(BaseModel):
    survey_id: int
    recipients: int = Field(..., description="How many people it was sent to.")
    opened: int = Field(..., description="How many opened it.")
    responses: int = Field(..., description="How many sent answers.")
    anonymous: int = Field(..., description="How many of those answered anonymously.")
    questions: List[QuestionResultResponse]


class SurveyAnswerResponse(BaseModel):
    question_id: int
    scope: str = Field(..., description="`t:<tournament id>` or `p:<position id>` for a repeating section, else empty.")
    scope_label: str = Field(..., description="The tournament or position name the answer is about.")
    value: Any = Field(None, description="The number, choice or list of choices.")
    text: Optional[str] = Field(None, description="A comment's text. Plain text: never render it as markup.")


class SubmittedResponse(BaseModel):
    id: int
    user_id: Optional[int] = Field(None, description="Who answered; null when they answered anonymously.")
    user_name: Optional[str] = None
    anonymous: bool
    groups: List[str] = Field(default_factory=list, description="The groups the respondent was in.")
    submitted_on: Optional[str] = Field(None, description="The day it was sent (UTC), never the time.")
    answers: List[SurveyAnswerResponse]


class ConditionResponse(BaseModel):
    key: str
    label: str


class SurveyCreateRequest(BaseModel):
    title: str = Field(..., max_length=120)
    intro: str = Field('', max_length=2000)
    allow_anonymous: bool = True
    from_template: bool = Field(True, description="Start from the starter questions.")


class SurveyUpdateRequest(BaseModel):
    title: Optional[str] = Field(None, max_length=120)
    intro: Optional[str] = Field(None, max_length=2000)
    allow_anonymous: Optional[bool] = None
    closes_at: Optional[datetime] = Field(None, description="When it stops taking answers (UTC). The only field an open survey takes.")


class SectionRequest(BaseModel):
    title: str = Field(..., max_length=120)
    audience: SurveyAudience = SurveyAudience.EVERYONE


class SectionUpdateRequest(BaseModel):
    title: Optional[str] = Field(None, max_length=120)
    audience: Optional[SurveyAudience] = None


class MoveRequest(BaseModel):
    step: int = Field(..., description="-1 moves it one place up, 1 one place down.")


class QuestionRequest(BaseModel):
    prompt: str = Field(..., max_length=280)
    kind: SurveyQuestionKind
    options: Optional[List[str]] = Field(None, description="2 to 10 choices, for `choice_one` and `choice_many`.")
    required: bool = False
    condition: Optional[str] = Field(None, description="A key from `GET /surveys/conditions`, or null to always show it.")
    condition_args: Optional[Dict[str, Any]] = Field(None, description="For `answer`: `{question_id, values}`.")


class QuestionUpdateRequest(BaseModel):
    prompt: Optional[str] = Field(None, max_length=280)
    kind: Optional[SurveyQuestionKind] = None
    options: Optional[List[str]] = None
    required: Optional[bool] = None
    condition: Optional[str] = Field(None, description="Send an empty string to clear it.")
    condition_args: Optional[Dict[str, Any]] = None


# Built from service results by both the REST router and the MCP tools, so the
# two surfaces return the same shapes.

def question_out(q: SurveyQuestion) -> SurveyQuestionResponse:
    return SurveyQuestionResponse(
        id=q.id, prompt=q.prompt, kind=q.kind.value, options=q.options, required=q.required,
        position=q.position, condition=q.condition, condition_args=q.condition_args,
    )


def section_out(s: SurveySection) -> SurveySectionResponse:
    return SurveySectionResponse(
        id=s.id, title=s.title, audience=s.audience.value, position=s.position,
        questions=[question_out(q) for q in s.questions],
    )


def _summary(s: Survey, sent: int = 0, answered: int = 0) -> Dict[str, Any]:
    return dict(
        id=s.id, title=s.title, status=s.status.value, allow_anonymous=s.allow_anonymous,
        sent_at=s.sent_at, closes_at=s.closes_at, recipients=sent, answered=answered,
    )


def summary_out(s: Survey, sent: int = 0, answered: int = 0) -> SurveySummaryResponse:
    return SurveySummaryResponse(**_summary(s, sent, answered))


def survey_out(s: Survey) -> SurveyResponseModel:
    return SurveyResponseModel(**_summary(s), intro=s.intro or '', sections=[section_out(x) for x in s.sections])


def _breakdown(b: Breakdown) -> BreakdownResponse:
    return BreakdownResponse(count=b.count, average=b.average, score=b.score, counts=b.counts, hidden=b.hidden)


def results_out(survey_id: int, results: SurveyResults) -> SurveyResultsResponse:
    return SurveyResultsResponse(
        survey_id=survey_id, recipients=results.recipients.get('sent', 0),
        opened=results.recipients.get('opened', 0), responses=results.responses, anonymous=results.anonymous,
        questions=[
            QuestionResultResponse(
                question_id=r.question_id, section_id=r.section_id, section_title=r.section_title,
                prompt=r.prompt, kind=r.kind.value, overall=_breakdown(r.overall),
                groups={k: _breakdown(v) for k, v in r.groups.items()},
                comments=[CommentResponse(text=c.text, who=c.who, group=c.group) for c in r.comments],
            )
            for r in results.questions
        ],
    )


def response_out(r: SurveyResponse) -> SubmittedResponse:
    return SubmittedResponse(
        id=r.id, user_id=r.user_id, user_name=r.user.preferred_name if r.user else None,
        anonymous=r.anonymous, groups=[g.get('label', '') for g in r.groups or []],
        submitted_on=r.submitted_on.isoformat() if r.submitted_on else None,
        answers=[SurveyAnswerResponse(**a) for a in response_answers(r)],
    )
