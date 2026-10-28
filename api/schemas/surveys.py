"""Schemas for post-event survey endpoints."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from application.services import SurveyResults
from application.services.survey import response_answers
from application.services.survey.comments import CommentInbox, InboxComment
from application.services.survey.dropoff import Dropoff
from application.services.survey.results import Breakdown
from models import Survey, SurveyAudience, SurveyQuestion, SurveyQuestionKind, SurveyResponse, SurveySection


class SurveyConditionModel(BaseModel):
    key: str = Field(..., description="A key from `GET /surveys/conditions`.")
    question_id: Optional[int] = Field(None, description="For `answer`: the earlier question it waits on.")
    values: Optional[List[Any]] = Field(
        None, description="For `answer`: the answers to that question that show this one (any of them).",
    )


_CONDITIONS_DOC = (
    "Conditions the respondent must all meet to see it (no OR); see `GET /surveys/conditions`. "
    "A question takes up to 3, a section 1."
)
_OTHER_DOC = (
    "`choice_one`/`choice_many`: adds an `Other` choice with a write-in. The write-in comes back as the answer's `text`."
)
_NONE_DOC = "`choice_many` only: the label of an exclusive \"none\" choice, listed last. It can't be picked with anything else."
_PICKS_DOC = "`choice_many` only: the fewest / most choices a respondent may pick (\"none\" aside). Null for no limit."
_LONG_DOC = "`comment` only: a tall box taking up to 3000 characters instead of 1000."
_DESCRIPTION_DOC = "One or two plain-text lines under the section title (up to 240 characters)."


class SurveyQuestionResponse(BaseModel):
    id: int = Field(..., description="Question ID.")
    prompt: str = Field(..., description="The question as respondents read it.")
    kind: str = Field(..., description="`rating` (1-5), `nps` (0-10), `yes_no`, `choice_one`, `choice_many`, `comment` or `agree_scale` (each statement Strongly disagree 1 to Strongly agree 5).")
    options: Optional[List[str]] = Field(None, description="The choices of a `choice_one` or `choice_many` question, or the statements of an `agree_scale`.")
    required: bool = Field(..., description="Whether it must be answered before sending.")
    other_option: bool = Field(False, description=_OTHER_DOC)
    none_option: Optional[str] = Field(None, description=_NONE_DOC)
    min_picks: Optional[int] = Field(None, description=_PICKS_DOC)
    max_picks: Optional[int] = Field(None, description=_PICKS_DOC)
    long_answer: bool = Field(False, description=_LONG_DOC)
    position: int = Field(..., description="Order inside its section, from 0.")
    conditions: List[SurveyConditionModel] = Field(default_factory=list, description=_CONDITIONS_DOC)
    original_prompt: Optional[str] = Field(None, description="The wording before it was first changed after answers came in.")
    reworded_at: Optional[datetime] = Field(None, description="When the wording first changed after answers came in (UTC).")
    reworded_after: Optional[int] = Field(None, description="How many responses had been sent before that change.")


class SurveySectionResponse(BaseModel):
    id: int = Field(..., description="Section ID.")
    title: str = Field(..., description="Section title.")
    description: Optional[str] = Field(None, description=_DESCRIPTION_DOC)
    audience: str = Field(..., description="Who sees it: `everyone`, `each_tournament`, `each_position`, `crew` or `checked_in`.")
    position: int = Field(..., description="Order in the survey, from 0.")
    conditions: List[SurveyConditionModel] = Field(default_factory=list, description=_CONDITIONS_DOC)
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
    after_send: str = Field('', description="Shown after someone sends their answers; blank shows a default line.")
    sections: List[SurveySectionResponse] = Field(default_factory=list)


class BreakdownResponse(BaseModel):
    count: int = Field(..., description="How many answers.")
    average: Optional[float] = Field(None, description="Average, for a rating or 0-10 question.")
    score: Optional[int] = Field(None, description="Recommend score (-100 to 100), for a 0-10 question.")
    counts: Dict[str, int] = Field(default_factory=dict, description="Answers per option.")
    hidden: bool = Field(False, description="True when fewer than 5 answered, so only the count is shown.")
    top_share: Optional[int] = Field(None, description="Rating only: percent who rated it 4 or 5.")
    statements: Dict[str, List[int]] = Field(
        default_factory=dict,
        description="Agree scale only: per statement, how many answered each level, Strongly disagree first.",
    )


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
        default_factory=dict,
        description="Per tournament or position, for a repeating section. Groups under 5 answers are folded "
                    "into `Other`, with the smallest shown groups joining it until it has 5.",
    )
    folded: List[str] = Field(default_factory=list, description="The groups folded into `Other`.")
    condition: str = Field('', description="Who was asked, for a question only some people saw.")
    comments: List[CommentResponse] = Field(default_factory=list)


class DropoffPartResponse(BaseModel):
    title: str = Field(..., description="The section, with the tournament or position for a repeat.")
    opened: int = Field(..., description="People who opened this part.")
    answered: int = Field(..., description="People who saved an answer in it.")
    stopped: int = Field(..., description="People who opened it last and haven't sent the survey.")


class DropoffResponse(BaseModel):
    started: int = Field(..., description="People who saved at least one part.")
    declined: int = Field(..., description="People who pressed Not interested on a survey DM.")
    median_seconds: Optional[int] = Field(None, description="Median time from first save to send; "
                                                            "empty under 5 finishers.")
    estimate_seconds: Optional[int] = Field(None, description="The survey's own time estimate, median per person.")
    parts: List[DropoffPartResponse]


class SurveyGroupResponse(BaseModel):
    key: str = Field('', description="Pass as `group` to narrow the results to this group.")
    label: str
    sent: int = Field(..., description="How many people in it were sent the survey.")
    responded: int = Field(..., description="How many of them sent answers.")


class SurveyResultsResponse(BaseModel):
    survey_id: int
    recipients: int = Field(..., description="How many people it was sent to.")
    opened: int = Field(..., description="How many opened it.")
    responses: int = Field(..., description="How many sent answers.")
    anonymous: int = Field(..., description="How many of those answered anonymously.")
    questions: List[QuestionResultResponse]
    dropoff: Optional[DropoffResponse] = Field(
        None, description="Where people stopped, counted in people. Left out when narrowed to a group.",
    )
    group: Optional[str] = Field(None, description="The group the numbers are narrowed to, if any.")
    groups: List[SurveyGroupResponse] = Field(
        default_factory=list, description="Groups the results can be narrowed to (each with 5 or more answers).",
    )
    audiences: List[SurveyGroupResponse] = Field(
        default_factory=list, description="Response rate for players, volunteers and crew (key is empty).",
    )


class SurveyCommentResponse(BaseModel):
    answer_id: int = Field(..., description="Pass to the mark call to mark this comment read or tag it.")
    question_id: int
    prompt: str
    section_title: str
    scope_label: str = Field('', description="The tournament or position it was about, for a repeating section.")
    who: str = Field(..., description="Their name, or `Anonymous` (with their group when it has 5 or more people).")
    group: str = Field('', description="The group shown beside an anonymous comment, when it can be.")
    text: str = Field(..., description="What the respondent wrote. Plain text: never render it as markup.")
    write_in: bool = Field(False, description="An `Other` write-in on a choice question, not a comment question.")
    read: bool = Field(False, description="Marked read by a staff member. Shared by all staff.")
    read_at: Optional[datetime] = None
    read_by: str = Field('', description="Who marked it read.")
    tags: List[int] = Field(default_factory=list, description="Ids of the survey's comment tags on it.")


class SurveyCommentTagResponse(BaseModel):
    id: int
    name: str
    count: int = Field(0, description="How many comments carry it.")


class SurveyCommentsResponse(BaseModel):
    survey_id: int
    unread: int
    tags: List[SurveyCommentTagResponse] = Field(
        default_factory=list, description="Staff-only tags. Respondents never see them.",
    )
    comments: List[SurveyCommentResponse] = Field(default_factory=list, description="Newest first.")


class SurveyCommentMarkRequest(BaseModel):
    read: Optional[bool] = Field(None, description="True marks it read, false unread. Leave out to keep it.")
    tags: Optional[List[int]] = Field(
        None, description="The comment's tags after the call (tag ids from this survey). Leave out to keep them.",
    )


class SurveyAnswerResponse(BaseModel):
    question_id: int
    scope: str = Field(..., description="`t:<tournament id>` or `p:<position id>` for a repeating section, else empty.")
    scope_label: str = Field(..., description="The tournament or position name the answer is about.")
    value: Any = Field(None, description="The number, choice or list of choices; an agree scale's {statement: 1-5}.")
    text: Optional[str] = Field(
        None, description="A comment's text, or the write-in of an `Other` answer. Plain text: never render it as markup.",
    )


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
    after_send: Optional[str] = Field(None, max_length=500, description="Shown after someone sends their answers.")
    allow_anonymous: Optional[bool] = Field(None, description="Fixed once the survey is sent.")
    closes_at: Optional[datetime] = Field(None, description="When it stops taking answers (UTC). Moves only while it's open.")


class SectionRequest(BaseModel):
    title: str = Field(..., max_length=120)
    description: Optional[str] = Field(None, max_length=240, description=_DESCRIPTION_DOC)
    audience: SurveyAudience = SurveyAudience.EVERYONE
    conditions: Optional[List[SurveyConditionModel]] = Field(None, description=_CONDITIONS_DOC)


class SectionUpdateRequest(BaseModel):
    title: Optional[str] = Field(None, max_length=120)
    description: Optional[str] = Field(None, max_length=240, description=_DESCRIPTION_DOC)
    audience: Optional[SurveyAudience] = None
    conditions: Optional[List[SurveyConditionModel]] = Field(None, description="Send `[]` to clear them.")


class MoveRequest(BaseModel):
    step: int = Field(..., description="-1 moves it one place up, 1 one place down.")


class QuestionRequest(BaseModel):
    prompt: str = Field(..., max_length=280)
    kind: SurveyQuestionKind
    options: Optional[List[str]] = Field(None, description="2 to 10 choices, for `choice_one` and `choice_many`; 2 to 10 statements for `agree_scale`.")
    required: bool = False
    other_option: bool = Field(False, description=_OTHER_DOC)
    none_option: Optional[str] = Field(None, max_length=60, description=_NONE_DOC)
    min_picks: Optional[int] = Field(None, ge=1, description=_PICKS_DOC)
    max_picks: Optional[int] = Field(None, ge=1, description=_PICKS_DOC)
    long_answer: bool = Field(False, description=_LONG_DOC)
    conditions: Optional[List[SurveyConditionModel]] = Field(None, description=_CONDITIONS_DOC)

    def extras(self) -> Dict[str, Any]:
        return self.model_dump(include={'other_option', 'none_option', 'min_picks', 'max_picks', 'long_answer'})


class QuestionUpdateRequest(BaseModel):
    prompt: Optional[str] = Field(None, max_length=280)
    kind: Optional[SurveyQuestionKind] = None
    options: Optional[List[str]] = None
    required: Optional[bool] = None
    other_option: Optional[bool] = Field(None, description=_OTHER_DOC)
    none_option: Optional[str] = Field(None, max_length=60, description=_NONE_DOC + " Send `\"\"` to remove it.")
    min_picks: Optional[int] = Field(None, ge=0, description=_PICKS_DOC + " Send 0 to remove it.")
    max_picks: Optional[int] = Field(None, ge=0, description=_PICKS_DOC + " Send 0 to remove it.")
    long_answer: Optional[bool] = Field(None, description=_LONG_DOC)
    conditions: Optional[List[SurveyConditionModel]] = Field(None, description="Send `[]` to always show it.")


# Built from service results by both the REST router and the MCP tools, so the
# two surfaces return the same shapes.

def conditions_out(entries: Optional[List[Dict[str, Any]]]) -> List[SurveyConditionModel]:
    return [SurveyConditionModel(**e) for e in entries or []]


def conditions_in(entries: Optional[List[SurveyConditionModel]]) -> Optional[List[Dict[str, Any]]]:
    return None if entries is None else [e.model_dump(exclude_none=True) for e in entries]


def question_out(q: SurveyQuestion) -> SurveyQuestionResponse:
    return SurveyQuestionResponse(
        id=q.id, prompt=q.prompt, kind=q.kind.value, options=q.options, required=q.required,
        other_option=q.other_option, none_option=q.none_option, min_picks=q.min_picks, max_picks=q.max_picks,
        long_answer=q.long_answer, position=q.position, conditions=conditions_out(q.conditions),
        original_prompt=q.original_prompt, reworded_at=q.reworded_at, reworded_after=q.reworded_after,
    )


def section_out(s: SurveySection) -> SurveySectionResponse:
    return SurveySectionResponse(
        id=s.id, title=s.title, description=s.description, audience=s.audience.value, position=s.position,
        conditions=conditions_out(s.conditions), questions=[question_out(q) for q in s.questions],
    )


def _summary(s: Survey, sent: int = 0, answered: int = 0) -> Dict[str, Any]:
    return dict(
        id=s.id, title=s.title, status=s.status.value, allow_anonymous=s.allow_anonymous,
        sent_at=s.sent_at, closes_at=s.closes_at, recipients=sent, answered=answered,
    )


def summary_out(s: Survey, sent: int = 0, answered: int = 0) -> SurveySummaryResponse:
    return SurveySummaryResponse(**_summary(s, sent, answered))


def survey_out(s: Survey) -> SurveyResponseModel:
    return SurveyResponseModel(**_summary(s), intro=s.intro or '', after_send=s.after_send or '', sections=[section_out(x) for x in s.sections])


def _breakdown(b: Breakdown, kind: SurveyQuestionKind) -> BreakdownResponse:
    return BreakdownResponse(count=b.count, average=b.average, score=b.score, counts=b.counts, hidden=b.hidden,
                             statements=b.statements,
                             top_share=b.top_share if kind is SurveyQuestionKind.RATING and not b.hidden else None)


def results_out(survey_id: int, results: SurveyResults) -> SurveyResultsResponse:
    return SurveyResultsResponse(
        survey_id=survey_id, recipients=results.recipients.get('sent', 0),
        opened=results.recipients.get('opened', 0), responses=results.responses, anonymous=results.anonymous,
        questions=[
            QuestionResultResponse(
                question_id=r.question_id, section_id=r.section_id, section_title=r.section_title,
                prompt=r.prompt, kind=r.kind.value, overall=_breakdown(r.overall, r.kind),
                groups={k: _breakdown(v, r.kind) for k, v in r.groups.items()},
                folded=r.folded, condition=r.condition,
                comments=[CommentResponse(text=c.text, who=c.who, group=c.group) for c in r.comments],
            )
            for r in results.questions
        ],
        dropoff=_dropoff(results.dropoff) if results.dropoff else None,
        group=results.group.key if results.group else None,
        groups=[SurveyGroupResponse(key=g.key, label=g.label, sent=g.sent, responded=g.responded)
                for g in results.groups],
        audiences=[SurveyGroupResponse(key='', label=a.label, sent=a.sent, responded=a.responded)
                   for a in results.audiences],
    )


def _dropoff(d: Dropoff) -> DropoffResponse:
    return DropoffResponse(
        started=d.started, declined=d.declined, median_seconds=d.median_seconds,
        estimate_seconds=d.estimate_seconds,
        parts=[DropoffPartResponse(title=p.title, opened=p.opened, answered=p.answered, stopped=p.stopped)
               for p in d.parts],
    )


def response_out(r: SurveyResponse) -> SubmittedResponse:
    return SubmittedResponse(
        id=r.id, user_id=r.user_id, user_name=r.user.preferred_name if r.user else None,
        anonymous=r.anonymous, groups=[g.get('label', '') for g in r.groups or []],
        submitted_on=r.submitted_on.isoformat() if r.submitted_on else None,
        answers=[SurveyAnswerResponse(**a) for a in response_answers(r)],
    )


def comments_out(inbox: CommentInbox) -> SurveyCommentsResponse:
    return SurveyCommentsResponse(
        survey_id=inbox.survey.id, unread=inbox.unread,
        tags=[SurveyCommentTagResponse(id=t.id, name=t.name, count=t.count) for t in inbox.tags],
        comments=[comment_out(c) for c in inbox.comments],
    )


def comment_out(c: InboxComment) -> SurveyCommentResponse:
    return SurveyCommentResponse(
        answer_id=c.answer_id, question_id=c.question_id, prompt=c.prompt, section_title=c.section_title,
        scope_label=c.scope_label, who=c.who, group=c.group, text=c.text, write_in=c.write_in,
        read=c.read, read_at=c.read_at, read_by=c.read_by, tags=c.tags,
    )
