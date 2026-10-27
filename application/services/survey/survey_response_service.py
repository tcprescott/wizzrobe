"""
Survey Response Service - Business Logic Layer

The respondent's side of a post-event survey: open it, answer it one part at a
time (answers save as they go, layout C), and send it, by name or anonymously.

Only the people a survey was sent to can open it, and each only ever reads and
writes their own response. What a respondent sends is untrusted: every answer
is checked against its question (:mod:`~application.services.survey._validation`)
and only questions on one of *their* parts are accepted, so a crafted request
can't answer a section they were never shown.

Anonymity is decided at submit: the draft is theirs while they work on it, and
an anonymous submit cuts the link for good (see
:class:`~models.SurveyResponse`).
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

from application.errors import NotFoundError, require_found
from application.feature_flags import requires_feature
from application.repositories.survey_repository import SurveyRepository
from application.services.survey import plan
from application.services.survey._validation import coerce_answer
from application.services.survey.survey_service import is_open
from models import FeatureFlag, Survey, SurveyRecipient, SurveyResponse, User


@dataclass
class RespondentView:
    """Everything the survey page needs for one person."""

    survey: Survey
    recipient: Optional[SurveyRecipient]  # None in the staff preview
    parts: List[plan.Part]
    answers: Dict[plan.AnswerKey, Any] = field(default_factory=dict)
    texts: Dict[plan.AnswerKey, str] = field(default_factory=dict)
    submitted: bool = False

    @property
    def open(self) -> bool:
        return is_open(self.survey) and not self.submitted

    @property
    def shown_parts(self) -> List[plan.Part]:
        """The parts showing now: a section that waits on an answer comes and goes with it."""
        return [p for p in self.parts if plan.part_shown(p, self.answers)]

    def stale_keys(self) -> List[plan.AnswerKey]:
        """Saved answers to questions or parts an answer changed later has hidden."""
        live = {(q.id, p.scope) for p in self.shown_parts for q in self.showing(p)}
        return [key for key in {*self.answers, *self.texts} if key not in live]

    def part(self, key: str) -> Optional[plan.Part]:
        return next((p for p in self.parts if p.key == key), None)

    def answered(self, part: plan.Part) -> int:
        """How many of the part's showing questions have an answer."""
        return sum(
            1 for q in part.questions
            if plan.visible(q, part.scope, self.answers)
            and ((q.id, part.scope) in self.answers or (q.id, part.scope) in self.texts)
        )

    def showing(self, part: plan.Part) -> List[Any]:
        return [q for q in part.questions if plan.visible(q, part.scope, self.answers)]

    def answers_in(self, part: plan.Part) -> Dict[int, Any]:
        """Question id → what's saved for it in ``part`` (the comment text for a comment)."""
        found: Dict[int, Any] = {}
        for q in part.questions:
            key = (q.id, part.scope)
            if key in self.answers:
                found[q.id] = self.answers[key]
            elif key in self.texts:
                found[q.id] = self.texts[key]
        return found

    def done(self, part: plan.Part) -> bool:
        return bool(self.showing(part)) and self.answered(part) > 0

    def missing_required(self) -> List[str]:
        missing = []
        for part in self.shown_parts:
            for q in self.showing(part):
                key = (q.id, part.scope)
                if q.required and key not in self.answers and key not in self.texts:
                    missing.append(f'{part.title}: {q.prompt}')
        return missing


def _day(now: Optional[datetime] = None) -> datetime:
    """Midnight UTC of today. A recipient row keeps only the day it was opened
    and answered, so its times can't be lined up with an anonymous response."""
    now = now or datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _groups(facts: Mapping[str, Any]) -> List[dict]:
    groups = [{'key': f"t:{t['id']}", 'label': f"{t['name']} player"} for t in facts.get('tournaments') or []]
    groups += [{'key': f"p:{p['id']}", 'label': p['name']} for p in facts.get('positions') or []]
    if facts.get('crew'):
        groups.append({'key': 'crew', 'label': 'Crew'})
    if facts.get('checked_in'):
        groups.append({'key': 'checked_in', 'label': 'Checked in'})
    return groups


class SurveyResponseService:
    """One respondent answering one survey."""

    def __init__(self) -> None:
        self.repository = SurveyRepository()

    async def _recipient(self, actor: User, survey_id: int) -> tuple[Survey, SurveyRecipient]:
        survey = require_found(await self.repository.get_full(survey_id), 'Survey')
        recipient = await self.repository.get_recipient(survey.id, actor.id)
        if recipient is None:
            # Not a PermissionError: whether a survey exists is nobody's business
            # but the people it went to and staff.
            raise NotFoundError("This survey wasn't sent to you.")
        return survey, recipient

    async def _view(self, survey: Survey, recipient: SurveyRecipient, user: User) -> RespondentView:
        view = RespondentView(
            survey=survey, recipient=recipient, parts=plan.parts_for(survey.sections, recipient.facts or {}),
            submitted=recipient.submitted_at is not None,
        )
        draft = await self.repository.draft_for(survey.id, user.id)
        if draft is not None:
            for answer in await self.repository.answers_for(draft.id):
                key = (answer.question_id, answer.scope)
                if answer.value is not None:
                    view.answers[key] = answer.value
                if answer.text:
                    view.texts[key] = answer.text
        return view

    @requires_feature(FeatureFlag.SURVEYS)
    async def open_survey(self, actor: User, survey_id: int) -> RespondentView:
        """The survey as ``actor`` sees it, with what they've answered so far. Marks it opened."""
        survey, recipient = await self._recipient(actor, survey_id)
        if recipient.opened_at is None:
            await self.repository.update(recipient, opened_at=_day())
        return await self._view(survey, recipient, actor)

    @requires_feature(FeatureFlag.SURVEYS)
    async def save_part(self, actor: User, survey_id: int, part_key: str,
                        answers: Mapping[int, Any]) -> RespondentView:
        """Save the answers to one part (question id → answer). A blank answer clears it."""
        survey, recipient = await self._recipient(actor, survey_id)
        view = await self._view(survey, recipient, actor)
        if not view.open:
            raise ValueError('This survey is closed.' if not is_open(survey) else "You've already sent your answers.")
        part = view.part(part_key)
        if part is None:
            raise ValueError("That part isn't in your survey.")
        questions = {q.id: q for q in part.questions}
        unknown = [qid for qid in answers if int(qid) not in questions]
        if unknown:
            raise ValueError("Some of those answers aren't for this part.")
        response = await self._draft(survey, actor)
        for qid, raw in answers.items():
            question = questions[int(qid)]
            value, text = coerce_answer(question, raw)
            if value is None and text is None:
                await self.repository.clear_answer(response, question.id, part.scope)
            else:
                await self.repository.upsert_answer(response, question.id, part.scope, part.scope_label, value, text)
        return await self._view(survey, recipient, actor)

    async def _draft(self, survey: Survey, actor: User) -> SurveyResponse:
        draft = await self.repository.draft_for(survey.id, actor.id)
        return draft or await self.repository.create_response(survey, actor.id)

    @requires_feature(FeatureFlag.SURVEYS)
    async def submit(self, actor: User, survey_id: int, *, anonymous: bool = False) -> None:
        """Send the answers. Anonymous strips the name for good and keeps only the day."""
        survey, recipient = await self._recipient(actor, survey_id)
        view = await self._view(survey, recipient, actor)
        if not view.open:
            raise ValueError('This survey is closed.' if not is_open(survey) else "You've already sent your answers.")
        if anonymous and not survey.allow_anonymous:
            raise ValueError("This survey doesn't take anonymous answers.")
        missing = view.missing_required()
        if missing:
            raise ValueError(f'Answer “{missing[0]}” first.')
        draft = await self.repository.draft_for(survey.id, actor.id)
        stale = view.stale_keys()
        if draft is None or len(stale) == len({*view.answers, *view.texts}):
            raise ValueError('Answer at least one question first.')
        for question_id, scope in stale:
            await self.repository.clear_answer(draft, question_id, scope)
        now = datetime.now(timezone.utc)
        await self.repository.submit_response(
            draft, anonymous=anonymous, groups=_groups(recipient.facts or {}), submitted_on=now.date(),
        )
        await self.repository.update(recipient, submitted_at=_day(now))

    @requires_feature(FeatureFlag.SURVEYS)
    async def my_open_surveys(self, actor: User) -> List[Survey]:
        """Open surveys sent to ``actor`` that they haven't answered, for a reminder on their pages."""
        rows = await self.repository.open_surveys_for_user(actor.id)
        return [r.survey for r in rows if is_open(r.survey)]


def response_answers(response: SurveyResponse) -> List[dict]:
    """A submitted response's answers as plain dicts, for REST and MCP."""
    return [
        {'question_id': a.question_id, 'scope': a.scope, 'scope_label': a.scope_label,
         'value': a.value, 'text': a.text}
        for a in response.answers
    ]

