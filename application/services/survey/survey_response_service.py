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
from application.services.survey.audiences import groups_for
from application.services.survey.survey_service import is_open
from models import FeatureFlag, Survey, SurveyQuestionKind, SurveyRecipient, SurveyResponse, User


@dataclass(frozen=True)
class MissingAnswer:
    """A required question still unanswered, as Send reports it."""

    part_key: str
    part_title: str
    question_id: int
    prompt: str
    #: An agree scale with some statements answered but not all.
    every_statement: bool = False

    def __str__(self) -> str:
        return f'{self.part_title}: {self.prompt}' + (' (every statement)' if self.every_statement else '')


@dataclass
class RespondentView:
    """Everything the survey page needs for one person."""

    survey: Survey
    recipient: Optional[SurveyRecipient]  # None in the staff preview
    parts: List[plan.Part]
    answers: Dict[plan.AnswerKey, Any] = field(default_factory=dict)
    texts: Dict[plan.AnswerKey, str] = field(default_factory=dict)
    submitted: bool = False
    anonymous: Optional[bool] = None  # known once submitted

    @property
    def after_send(self) -> str:
        return (getattr(self.survey, 'after_send', '') or '').strip() or plan.DEFAULT_AFTER_SEND

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
        """Question id → what's saved for it in ``part``, in the shape :meth:`SurveyResponseService.save_part` takes.

        The comment text for a comment, and ``{'value': …, 'other': write-in}``
        for a choice answered "Other".
        """
        found: Dict[int, Any] = {}
        for q in part.questions:
            key = (q.id, part.scope)
            if key in self.answers and key in self.texts:
                found[q.id] = {'value': self.answers[key], 'other': self.texts[key]}
            elif key in self.answers:
                found[q.id] = self.answers[key]
            elif key in self.texts:
                found[q.id] = self.texts[key]
        return found

    def done(self, part: plan.Part) -> bool:
        return bool(self.showing(part)) and self.answered(part) > 0

    def missing(self) -> List[MissingAnswer]:
        """Required questions showing but not (fully) answered, in survey order."""
        found = []
        for part in self.shown_parts:
            for q in self.showing(part):
                key = (q.id, part.scope)
                if not q.required:
                    continue
                if key not in self.answers and key not in self.texts:
                    found.append(MissingAnswer(part.key, part.title, q.id, q.prompt))
                elif q.kind is SurveyQuestionKind.AGREE_SCALE and \
                        len(self.answers.get(key) or {}) < len(q.options or []):
                    found.append(MissingAnswer(part.key, part.title, q.id, q.prompt, every_statement=True))
        return found

    def missing_required(self) -> List[str]:
        return [str(m) for m in self.missing()]


def _day(now: Optional[datetime] = None) -> datetime:
    """Midnight UTC of today. A recipient row keeps only the day it was opened
    and answered, so its times can't be lined up with an anonymous response."""
    now = now or datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _progress_with(progress: Optional[Mapping[str, Any]], kind: str, part_key: str) -> Dict[str, List[str]]:
    """``progress`` with ``part_key`` added to its ``opened`` or ``done`` list."""
    out = {k: list(v) for k, v in (progress or {}).items()}
    keys = out.setdefault(kind, [])
    if part_key not in keys:
        keys.append(part_key)
    return out


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
        if view.submitted:
            view.anonymous = not await self.repository.submitted_by(survey.id, user.id)
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
    async def open_part(self, actor: User, survey_id: int, part_key: str) -> None:
        """Note that ``actor`` reached a part, for the drop-off card. Quietly does nothing once they've sent it."""
        survey, recipient = await self._recipient(actor, survey_id)
        if recipient.submitted_at is not None or not is_open(survey):
            return
        if not any(p.key == part_key for p in plan.parts_for(survey.sections, recipient.facts or {})):
            raise ValueError("That part isn't in your survey.")
        await self.repository.update(
            recipient, progress=_progress_with(recipient.progress, 'opened', part_key), last_part=part_key,
        )

    @requires_feature(FeatureFlag.SURVEYS)
    async def decline(self, actor: User, survey_id: int) -> bool:
        """"Not interested": no more reminders for ``actor``. False when they'd already sent or declined it."""
        _, recipient = await self._recipient(actor, survey_id)
        if recipient.submitted_at is not None or recipient.declined_at is not None:
            return False
        await self.repository.update(recipient, declined_at=_day())
        return True

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
        progress = _progress_with(_progress_with(recipient.progress, 'opened', part.key), 'done', part.key)
        await self.repository.update(
            recipient, progress=progress, last_part=part.key,
            started_at=recipient.started_at or datetime.now(timezone.utc),
        )
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
            draft, anonymous=anonymous, groups=groups_for(recipient.facts or {}), submitted_on=now.date(),
        )
        await self._fold_progress(survey, recipient, view, now)

    async def _fold_progress(self, survey: Survey, recipient: SurveyRecipient, view: RespondentView,
                             now: datetime) -> None:
        """Add this person's parts to the survey's running counts and clear their own progress.

        Which parts someone opened or skipped would let a named recipient row be
        matched to an anonymous response, so it's kept only as a total. The
        exact start time likewise becomes a duration and goes.
        """
        opened = set((recipient.progress or {}).get('opened') or [])
        answered = {p.key for p in view.shown_parts if view.answered(p)}
        await self.repository.bump_part_tallies(survey, [
            (p.section_id, p.scope, p.scope_label, True, p.key in answered)
            for p in view.parts if p.key in opened | answered
        ])
        started = recipient.started_at
        await self.repository.update(
            recipient, submitted_at=_day(now), progress={}, last_part=None, started_at=None,
            finish_seconds=max(0, int((now - started).total_seconds())) if started else None,
        )

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

