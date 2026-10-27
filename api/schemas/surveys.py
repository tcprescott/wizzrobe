"""Schemas for post-event survey endpoints."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from models import SurveyAudience, SurveyQuestionKind


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
