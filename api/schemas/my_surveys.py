"""Schemas for taking a survey in the app, and for staff previewing one.

The parts and questions are what one person sees. A question's ``shows_if``
lists the earlier answers it waits on, so the form can show a follow-up the
moment its answer is picked without a round trip.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from pydantic import BaseModel, Field

from application.services.survey import plan
from application.services.survey.conditions import answer_entries
from application.services.survey.survey_response_service import RespondentView
from models import Survey, SurveyQuestion


class ShowsIf(BaseModel):
    question_id: int = Field(..., description='The earlier question it waits on.')
    values: List[Any] = Field(
        ..., description='It shows when that answer is one of these (for a pick-many, when any picked choice is).',
    )


class RespondentQuestion(BaseModel):
    id: int
    prompt: str
    kind: str = Field(..., description='`rating`, `nps`, `yes_no`, `choice_one`, `choice_many`, `comment` or `agree_scale`.')
    options: Optional[List[str]] = Field(None, description='Choices, or an agree scale\'s statements.')
    required: bool
    other_option: bool = False
    none_option: Optional[str] = None
    min_picks: Optional[int] = None
    max_picks: Optional[int] = None
    long_answer: bool = False
    shows_if: List[ShowsIf] = Field(
        default_factory=list,
        description='All must hold for it to show. An answer is looked up in this part, then in the unscoped part.',
    )


class RespondentPart(BaseModel):
    key: str = Field(..., description='`<section id>|<scope>`; send it back to save the part.')
    title: str
    description: str = ''
    reason: str = Field(..., description='Why this person gets it, e.g. `You played ALTTPR Main`.')
    icon: str = Field(..., description='Material Symbols icon name.')
    scope: str
    shows_if: List[ShowsIf] = Field(default_factory=list, description='The whole part waits on these answers.')
    questions: List[RespondentQuestion]
    answers: Dict[int, Any] = Field(
        default_factory=dict,
        description='Question id → what is saved: the value, the comment text, or `{value, other}` for an Other pick.',
    )
    answered: int = Field(0, description='Questions showing now that have an answer.')
    showing: int = Field(0, description='Questions showing now.')
    shown: bool = Field(True, description='Whether the part shows, given the answers so far.')
    seconds: int = Field(..., description='About how long it takes.')


class MissingAnswerResponse(BaseModel):
    part_key: str
    part_title: str
    question_id: int
    prompt: str
    every_statement: bool = Field(False, description='An agree scale with some statements still unanswered.')


class RespondentSurvey(BaseModel):
    id: int
    title: str
    intro: Optional[str] = None
    closes_at: Optional[datetime] = None
    allow_anonymous: bool
    after_send: str = Field(..., description='What the page says after sending.')
    open: bool = Field(..., description='False once it closed or this person sent their answers.')
    submitted: bool
    anonymous: Optional[bool] = Field(None, description='Once sent: whether it went without their name.')
    seconds: int = Field(..., description='About how long their parts take in all.')
    parts: List[RespondentPart]
    missing: List[MissingAnswerResponse] = Field(
        default_factory=list, description='Required questions still unanswered, in order; Send refuses while any remain.',
    )


class MyOpenSurvey(BaseModel):
    id: int
    title: str
    closes_at: Optional[datetime] = None


class SavePartRequest(BaseModel):
    answers: Dict[int, Any] = Field(
        ..., description='Question id → answer, in the shape `answers` returns. A blank answer clears it.',
    )


class SubmitRequest(BaseModel):
    anonymous: bool = Field(False, description='Send without the name, for good. Refused if the survey asks for names.')


class PreviewPathResponse(BaseModel):
    persona: str = Field(..., description='Pass as `persona` to `GET /surveys/{id}/preview`.')
    label: str
    parts: int
    questions: int
    seconds: int
    hidden: List[str] = Field(default_factory=list, description='What they won\'t see, and why, one line each.')


def _shows_if(entries: Optional[Sequence[Dict[str, Any]]]) -> List[ShowsIf]:
    return [
        ShowsIf(question_id=int(e.get('question_id') or 0), values=list(e.get('values') or []))
        for e in answer_entries(entries)
    ]


def _question(q: SurveyQuestion) -> RespondentQuestion:
    return RespondentQuestion(
        id=q.id, prompt=q.prompt, kind=q.kind.value, options=q.options, required=q.required,
        other_option=q.other_option, none_option=q.none_option, min_picks=q.min_picks, max_picks=q.max_picks,
        long_answer=q.long_answer, shows_if=_shows_if(q.conditions),
    )


def _part(p: plan.Part, view: Optional[RespondentView]) -> RespondentPart:
    return RespondentPart(
        key=p.key, title=p.title, description=p.description, reason=p.reason, icon=p.icon, scope=p.scope,
        shows_if=_shows_if(p.follows), questions=[_question(q) for q in p.questions],
        answers=view.answers_in(p) if view else {},
        answered=view.answered(p) if view else 0,
        showing=len(view.showing(p)) if view else len(p.questions),
        shown=plan.part_shown(p, view.answers) if view else True,
        seconds=plan.seconds([p]),
    )


def respondent_out(view: RespondentView) -> RespondentSurvey:
    s = view.survey
    return RespondentSurvey(
        id=s.id, title=s.title, intro=s.intro, closes_at=s.closes_at, allow_anonymous=s.allow_anonymous,
        after_send=view.after_send, open=view.open, submitted=view.submitted, anonymous=view.anonymous,
        seconds=plan.seconds(view.shown_parts), parts=[_part(p, view) for p in view.parts],
        missing=[MissingAnswerResponse(**m.__dict__) for m in view.missing()] if view.open else [],
    )


def preview_out(survey: Survey, parts: List[plan.Part]) -> RespondentSurvey:
    return RespondentSurvey(
        id=survey.id, title=survey.title, intro=survey.intro, closes_at=survey.closes_at,
        allow_anonymous=survey.allow_anonymous,
        after_send=(survey.after_send or '').strip() or plan.DEFAULT_AFTER_SEND,
        open=False, submitted=False, seconds=plan.seconds(parts), parts=[_part(p, None) for p in parts],
    )
