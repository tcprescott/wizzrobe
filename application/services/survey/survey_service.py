"""
Survey Service - Business Logic Layer

Post-event surveys from the staff side: build one (it starts from a template),
preview it as different kinds of people, send it by DM to everyone who took
part, remind, close, and read the results.

Staff only, and gated by :attr:`~models.FeatureFlag.SURVEYS`. The questions are
written by staff; nothing here generates them. Who gets a survey, and which
sections each person sees, is decided once at send time from what they did at
the event (:mod:`application.services.survey.participation`), and the
structure is frozen from then on: a sent survey's questions can't change
under the answers already given to them.

The respondent's side is :mod:`~application.services.survey.survey_response_service`.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence

from application.errors import require_found
from application.events import EventType
from application.feature_flags import requires_feature
from application.repositories.survey_participation_repository import SurveyParticipationRepository
from application.repositories.survey_repository import SurveyRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.survey import _notifications, plan
from application.services.survey._validation import (
    INTRO_MAX,
    PROMPT_MAX,
    TITLE_MAX,
    check_condition,
    clean_options,
    clean_text,
)
from application.services.survey.participation import Participation
from application.services.survey.results import QuestionResult, summarize
from application.services.survey.template import STARTER
from models import (
    FeatureFlag,
    Survey,
    SurveyAudience,
    SurveyQuestion,
    SurveyQuestionKind,
    SurveyResponse,
    SurveySection,
    SurveyStatus,
    User,
)

_UNSET: Any = object()
MAX_SECTIONS = 20
MAX_QUESTIONS_PER_SECTION = 20
REMIND_GAP_HOURS = 24


def is_open(survey: Survey, now: Optional[datetime] = None) -> bool:
    """Taking answers: sent, not closed, and before its close time.

    A survey past ``closes_at`` reads as closed without anything having to run
    at that moment; :meth:`SurveyService.close` makes it official.
    """
    if survey.status is not SurveyStatus.OPEN:
        return False
    return survey.closes_at is None or (now or datetime.now(timezone.utc)) < survey.closes_at


@dataclass
class BuilderSummary:
    """The builder's side panel: time budget and who gets it."""

    longest_seconds: int
    longest_questions: int
    longest_who: str
    counts: Dict[str, int]
    tournaments: List[dict] = field(default_factory=list)
    positions: List[dict] = field(default_factory=list)

    @property
    def over_budget(self) -> bool:
        return self.longest_seconds > plan.BUDGET_SECONDS


@dataclass
class SurveyResults:
    survey: Survey
    recipients: Dict[str, int]
    responses: int
    anonymous: int
    questions: List[QuestionResult]

    @property
    def response_rate(self) -> Optional[int]:
        sent = self.recipients.get('sent') or 0
        return round(self.responses * 100 / sent) if sent else None

    def headline(self, kind: SurveyQuestionKind) -> Optional[QuestionResult]:
        """The first question of ``kind`` in a section everyone sees (the overall rating, the NPS)."""
        for result in self.questions:
            if result.kind is kind and not result.groups:
                return result
        return None


class SurveyService:
    """Staff-side survey operations."""

    def __init__(self) -> None:
        self.repository = SurveyRepository()
        self.audit_service = AuditService()

    @staticmethod
    async def _ensure_staff(actor: User) -> None:
        await AuthService.ensure(await AuthService.is_staff(actor), 'Only Staff can manage surveys.')

    async def _load(self, survey_id: int) -> Survey:
        return require_found(await self.repository.get_full(survey_id), 'Survey')

    @staticmethod
    def _ensure_draft(survey: Survey) -> None:
        if survey.status is not SurveyStatus.DRAFT:
            raise ValueError("This survey has been sent, so its questions can't change. Duplicate it to start a new one.")

    async def _audit(self, actor: User, action: str, survey: Survey, **details: Any) -> None:
        await self.audit_service.write_log(actor, action, {'survey_id': survey.id, 'title': survey.title, **details})

    # --- reads ----------------------------------------------------------

    @requires_feature(FeatureFlag.SURVEYS)
    async def list_surveys(self, actor: User) -> List[Survey]:
        """The current event's surveys, newest first."""
        await self._ensure_staff(actor)
        return await self.repository.list_for_event()

    @requires_feature(FeatureFlag.SURVEYS)
    async def get_survey(self, actor: User, survey_id: int) -> Survey:
        """One survey with its sections and questions, in order."""
        await self._ensure_staff(actor)
        return await self._load(survey_id)

    @requires_feature(FeatureFlag.SURVEYS)
    async def builder_summary(self, actor: User, survey_id: int) -> BuilderSummary:
        """How long the survey takes the person with the most to answer, and who gets it.

        Worked out over the people who actually took part in the survey's event,
        so the time box speaks about the real worst case, not a guess.
        """
        await self._ensure_staff(actor)
        survey = await self._load(survey_id)
        participation = await Participation.load(survey.event_id)
        longest, who, count = 0, '', 0
        for facts in (await participation.facts_by_user()).values():
            parts = plan.parts_for(survey.sections, facts)
            seconds = plan.seconds(parts)
            if seconds > longest:
                longest, count = seconds, plan.question_count(parts)
                who = _describe(facts)
        return BuilderSummary(
            longest_seconds=longest, longest_questions=count, longest_who=who,
            counts=participation.counts(),
            tournaments=participation.tournament_names(), positions=participation.position_names(),
        )

    @requires_feature(FeatureFlag.SURVEYS)
    async def preview(self, actor: User, survey_id: int, persona: str = 'everything') -> tuple[Survey, List[plan.Part]]:
        """The parts one kind of person would see. Nothing is recorded."""
        await self._ensure_staff(actor)
        survey = await self._load(survey_id)
        participation = await Participation.load(survey.event_id)
        persona = persona if persona in plan.PREVIEW_PERSONAS else 'everything'
        facts = plan.preview_facts(persona, participation.tournament_names(), participation.position_names())
        return survey, plan.parts_for(survey.sections, facts, ignore_conditions=persona == 'everything')

    @requires_feature(FeatureFlag.SURVEYS)
    async def results(self, actor: User, survey_id: int) -> SurveyResults:
        """Counts per question, split by tournament or position where a section repeats."""
        await self._ensure_staff(actor)
        survey = await self._load(survey_id)
        responses = await self.repository.submitted_responses(survey.id)
        answers = await self.repository.submitted_answers(survey.id)
        questions = [q for s in survey.sections for q in s.questions]
        sections = {s.id: s for s in survey.sections}
        return SurveyResults(
            survey=survey,
            recipients=await self.repository.recipient_counts(survey.id),
            responses=len(responses),
            anonymous=sum(1 for r in responses if r.anonymous),
            questions=summarize(questions, sections, answers, responses),
        )

    @requires_feature(FeatureFlag.SURVEYS)
    async def list_responses(self, actor: User, survey_id: int, *, limit: int = 100,
                             offset: int = 0) -> List[SurveyResponse]:
        """Submitted responses, newest first, each with its answers. Anonymous ones carry no user."""
        await self._ensure_staff(actor)
        survey = await self._load(survey_id)
        return await self.repository.submitted_responses(survey.id, limit=limit, offset=offset)

    # --- building -------------------------------------------------------

    @requires_feature(FeatureFlag.SURVEYS)
    async def create_survey(self, actor: User, title: str, *, from_template: bool = True,
                            intro: str = '', allow_anonymous: bool = True) -> Survey:
        """A new draft in the current event, filled from the starter template unless asked not to."""
        await self._ensure_staff(actor)
        survey = await self.repository.create(
            title=clean_text(title, limit=TITLE_MAX, what='A title'),
            intro=clean_text(intro, limit=INTRO_MAX, what='The intro', required=False),
            allow_anonymous=allow_anonymous, created_by=actor,
        )
        if from_template:
            for s_pos, (s_title, audience, questions) in enumerate(STARTER):
                section = await self.repository.create_section(
                    survey, title=s_title, audience=audience, position=s_pos,
                )
                for q_pos, (prompt, kind, options, condition) in enumerate(questions):
                    await self.repository.create_question(
                        section, prompt=prompt, kind=kind, options=options, condition=condition, position=q_pos,
                    )
        await self._audit(actor, AuditActions.SURVEY_CREATED, survey, from_template=from_template)
        return await self._load(survey.id)

    @requires_feature(FeatureFlag.SURVEYS)
    async def duplicate(self, actor: User, survey_id: int) -> Survey:
        """A new draft in the current event with the same sections and questions."""
        await self._ensure_staff(actor)
        source = await self._load(survey_id)
        copy = await self.repository.create(
            title=clean_text(f'{source.title} (copy)'[:TITLE_MAX], limit=TITLE_MAX, what='A title'),
            intro=source.intro, allow_anonymous=source.allow_anonymous, created_by=actor,
        )
        ids: Dict[int, int] = {}
        for section in source.sections:
            new_section = await self.repository.create_section(
                copy, title=section.title, audience=section.audience, position=section.position,
            )
            for q in section.questions:
                args = dict(q.condition_args or {}) or None
                if args and args.get('question_id') in ids:
                    args['question_id'] = ids[args['question_id']]
                new_q = await self.repository.create_question(
                    new_section, prompt=q.prompt, kind=q.kind, options=q.options, required=q.required,
                    position=q.position, condition=q.condition, condition_args=args,
                )
                ids[q.id] = new_q.id
        await self._audit(actor, AuditActions.SURVEY_CREATED, copy, copied_from=source.id)
        return await self._load(copy.id)

    @requires_feature(FeatureFlag.SURVEYS)
    async def update_survey(self, actor: User, survey_id: int, *, title: Any = _UNSET, intro: Any = _UNSET,
                            allow_anonymous: Any = _UNSET, closes_at: Any = _UNSET) -> Survey:
        """Change the survey's title, intro, anonymity switch or close time.

        The close time can move while it's open; the rest is fixed once sent.
        """
        await self._ensure_staff(actor)
        survey = await self._load(survey_id)
        fields: Dict[str, Any] = {}
        if title is not _UNSET or intro is not _UNSET or allow_anonymous is not _UNSET:
            self._ensure_draft(survey)
        if title is not _UNSET:
            fields['title'] = clean_text(title, limit=TITLE_MAX, what='A title')
        if intro is not _UNSET:
            fields['intro'] = clean_text(intro, limit=INTRO_MAX, what='The intro', required=False)
        if allow_anonymous is not _UNSET:
            fields['allow_anonymous'] = bool(allow_anonymous)
        if closes_at is not _UNSET:
            if survey.status is SurveyStatus.CLOSED:
                raise ValueError('This survey is closed.')
            if closes_at is not None and closes_at <= datetime.now(timezone.utc):
                raise ValueError('Pick a close time in the future.')
            fields['closes_at'] = closes_at
        if fields:
            await self.repository.update(survey, **fields)
            await self._audit(actor, AuditActions.SURVEY_UPDATED, survey, fields=sorted(fields))
        return await self._load(survey.id)

    @requires_feature(FeatureFlag.SURVEYS)
    async def delete_survey(self, actor: User, survey_id: int) -> None:
        """Delete a survey and every answer it collected."""
        await self._ensure_staff(actor)
        survey = await self._load(survey_id)
        await self._audit(actor, AuditActions.SURVEY_DELETED, survey, status=survey.status.value)
        await self.repository.delete(survey)

    async def _draft_section(self, section_id: int) -> tuple[Survey, SurveySection]:
        section = require_found(await self.repository.get_section(section_id), 'Section')
        survey = await self._load(section.survey_id)
        self._ensure_draft(survey)
        return survey, section

    @requires_feature(FeatureFlag.SURVEYS)
    async def add_section(self, actor: User, survey_id: int, title: str,
                          audience: SurveyAudience = SurveyAudience.EVERYONE) -> SurveySection:
        await self._ensure_staff(actor)
        survey = await self._load(survey_id)
        self._ensure_draft(survey)
        if len(survey.sections) >= MAX_SECTIONS:
            raise ValueError(f'A survey holds at most {MAX_SECTIONS} sections.')
        section = await self.repository.create_section(
            survey, title=clean_text(title, limit=TITLE_MAX, what='A section title'),
            audience=SurveyAudience(audience), position=await self.repository.next_section_position(survey.id),
        )
        await self._audit(actor, AuditActions.SURVEY_UPDATED, survey, section_added=section.id)
        return section

    @requires_feature(FeatureFlag.SURVEYS)
    async def update_section(self, actor: User, section_id: int, *, title: Any = _UNSET,
                             audience: Any = _UNSET) -> SurveySection:
        await self._ensure_staff(actor)
        survey, section = await self._draft_section(section_id)
        fields: Dict[str, Any] = {}
        if title is not _UNSET:
            fields['title'] = clean_text(title, limit=TITLE_MAX, what='A section title')
        if audience is not _UNSET:
            fields['audience'] = SurveyAudience(audience)
        if fields:
            await self.repository.update(section, **fields)
            await self._audit(actor, AuditActions.SURVEY_UPDATED, survey, section_updated=section.id)
        return section

    @requires_feature(FeatureFlag.SURVEYS)
    async def delete_section(self, actor: User, section_id: int) -> None:
        await self._ensure_staff(actor)
        survey, section = await self._draft_section(section_id)
        await self.repository.delete(section)
        await self._audit(actor, AuditActions.SURVEY_UPDATED, survey, section_deleted=section_id)

    @requires_feature(FeatureFlag.SURVEYS)
    async def move_section(self, actor: User, section_id: int, step: int) -> None:
        """Move a section one place up (``-1``) or down (``1``)."""
        await self._ensure_staff(actor)
        survey, section = await self._draft_section(section_id)
        ids = await self.repository.section_ids(survey.id)
        await self.repository.set_section_positions(_moved(ids, section.id, step))

    @staticmethod
    def _earlier(survey: Survey, section_id: int, before_question_id: Optional[int]) -> Dict[int, SurveyQuestion]:
        """The questions before a point in the survey, which an answer-based condition may follow.

        ``before_question_id`` is the question being edited; ``None`` means a new
        question at the end of ``section_id``.
        """
        earlier: Dict[int, SurveyQuestion] = {}
        for section in survey.sections:
            for q in section.questions:
                if q.id == before_question_id:
                    return earlier
                earlier[q.id] = q
            if section.id == section_id:
                return earlier
        return earlier

    @requires_feature(FeatureFlag.SURVEYS)
    async def add_question(self, actor: User, section_id: int, prompt: str, kind: SurveyQuestionKind, *,
                           options: Optional[Sequence[str]] = None, required: bool = False,
                           condition: Optional[str] = None,
                           condition_args: Optional[Mapping[str, Any]] = None) -> SurveyQuestion:
        await self._ensure_staff(actor)
        survey, section = await self._draft_section(section_id)
        current = next((s for s in survey.sections if s.id == section.id), None)
        if current is not None and len(current.questions) >= MAX_QUESTIONS_PER_SECTION:
            raise ValueError(f'A section holds at most {MAX_QUESTIONS_PER_SECTION} questions.')
        kind = SurveyQuestionKind(kind)
        question = await self.repository.create_question(
            section,
            prompt=clean_text(prompt, limit=PROMPT_MAX, what='The question'),
            kind=kind, options=clean_options(kind, options), required=bool(required),
            condition=condition or None,
            condition_args=check_condition(condition, condition_args, self._earlier(survey, section.id, None)),
            position=await self.repository.next_question_position(section.id),
        )
        await self._audit(actor, AuditActions.SURVEY_UPDATED, survey, question_added=question.id)
        return question

    @requires_feature(FeatureFlag.SURVEYS)
    async def update_question(self, actor: User, question_id: int, *, prompt: Any = _UNSET, kind: Any = _UNSET,
                              options: Any = _UNSET, required: Any = _UNSET, condition: Any = _UNSET,
                              condition_args: Any = _UNSET) -> SurveyQuestion:
        await self._ensure_staff(actor)
        question = require_found(await self.repository.get_question(question_id), 'Question')
        survey, _ = await self._draft_section(question.section_id)
        fields: Dict[str, Any] = {}
        if prompt is not _UNSET:
            fields['prompt'] = clean_text(prompt, limit=PROMPT_MAX, what='The question')
        new_kind = SurveyQuestionKind(kind) if kind is not _UNSET else question.kind
        if kind is not _UNSET:
            fields['kind'] = new_kind
        if options is not _UNSET or kind is not _UNSET:
            fields['options'] = clean_options(new_kind, options if options is not _UNSET else question.options)
        if required is not _UNSET:
            fields['required'] = bool(required)
        if condition is not _UNSET or condition_args is not _UNSET:
            key = condition if condition is not _UNSET else question.condition
            args = condition_args if condition_args is not _UNSET else question.condition_args
            fields['condition'] = key or None
            fields['condition_args'] = check_condition(
                key, args, self._earlier(survey, question.section_id, question.id),
            )
        if fields:
            await self.repository.update(question, **fields)
            await self._audit(actor, AuditActions.SURVEY_UPDATED, survey, question_updated=question.id)
        return question

    @requires_feature(FeatureFlag.SURVEYS)
    async def delete_question(self, actor: User, question_id: int) -> None:
        await self._ensure_staff(actor)
        question = require_found(await self.repository.get_question(question_id), 'Question')
        survey, _ = await self._draft_section(question.section_id)
        followers = [q for s in survey.sections for q in s.questions
                     if (q.condition_args or {}).get('question_id') == question.id]
        if followers:
            raise ValueError(f'“{followers[0].prompt}” follows this question. Change or delete that one first.')
        await self.repository.delete(question)
        await self._audit(actor, AuditActions.SURVEY_UPDATED, survey, question_deleted=question_id)

    @requires_feature(FeatureFlag.SURVEYS)
    async def move_question(self, actor: User, question_id: int, step: int) -> None:
        """Move a question one place up (``-1``) or down (``1``) inside its section."""
        await self._ensure_staff(actor)
        question = require_found(await self.repository.get_question(question_id), 'Question')
        await self._draft_section(question.section_id)
        ids = await self.repository.question_ids(question.section_id)
        await self.repository.set_question_positions(_moved(ids, question.id, step))

    # --- lifecycle ------------------------------------------------------

    @requires_feature(FeatureFlag.SURVEYS)
    async def send(self, actor: User, survey_id: int, *, closes_at: Optional[datetime] = None) -> int:
        """Open the survey and DM everyone who took part in its event.

        Works out each person's facts now and freezes them on their recipient
        row. Returns how many people it went to. Only from the app: REST and MCP
        build surveys but don't send them (Thomas's call), so this is never
        exposed there.
        """
        await self._ensure_staff(actor)
        survey = await self._load(survey_id)
        self._ensure_draft(survey)
        if not any(s.questions for s in survey.sections):
            raise ValueError('Add at least one question before sending.')
        if closes_at is not None and closes_at <= datetime.now(timezone.utc):
            raise ValueError('Pick a close time in the future.')
        participation = await Participation.load(survey.event_id)
        users = await _reachable(participation.everyone())
        if not users:
            raise ValueError('Nobody took part in this event yet, so there is nobody to send it to.')
        facts = await participation.facts_by_user(set(users))
        await self.repository.add_recipients(survey, facts)
        now = datetime.now(timezone.utc)
        await self.repository.update(survey, status=SurveyStatus.OPEN, sent_at=now, sent_by=actor, closes_at=closes_at)
        recipients = await self.repository.recipients(survey.id)
        queued = await _notifications.send_invites(
            survey, [(r, users[r.user_id].discord_id if r.user_id in users else None) for r in recipients],
        )
        await self.repository.mark_recipients(queued, dm_sent_at=now)
        await self.audit_service.write_and_publish(
            actor, AuditActions.SURVEY_SENT,
            {'survey_id': survey.id, 'title': survey.title, 'recipients': len(recipients), 'dms': len(queued)},
            EventType.SURVEY_SENT,
        )
        return len(recipients)

    @requires_feature(FeatureFlag.SURVEYS)
    async def remind(self, actor: User, survey_id: int) -> int:
        """DM everyone who hasn't answered yet. Once a day at most."""
        await self._ensure_staff(actor)
        survey = await self._load(survey_id)
        if not is_open(survey):
            raise ValueError('This survey is closed.')
        now = datetime.now(timezone.utc)
        if survey.reminded_at and (now - survey.reminded_at).total_seconds() < REMIND_GAP_HOURS * 3600:
            raise ValueError('A reminder already went out in the last day.')
        pending = await self.repository.recipients(survey.id, unanswered=True)
        queued = await _notifications.send_invites(
            survey, [(r, r.user.discord_id) for r in pending], reminder=True,
        )
        await self.repository.update(survey, reminded_at=now)
        await self._audit(actor, AuditActions.SURVEY_REMINDED, survey, dms=len(queued))
        return len(queued)

    @requires_feature(FeatureFlag.SURVEYS)
    async def close(self, actor: User, survey_id: int) -> Survey:
        """Stop taking answers. Drafts nobody submitted stay unsubmitted."""
        await self._ensure_staff(actor)
        survey = await self._load(survey_id)
        if survey.status is not SurveyStatus.OPEN:
            raise ValueError('Only an open survey can be closed.')
        await self.repository.update(survey, status=SurveyStatus.CLOSED, closed_at=datetime.now(timezone.utc))
        await self.audit_service.write_and_publish(
            actor, AuditActions.SURVEY_CLOSED, {'survey_id': survey.id, 'title': survey.title},
            EventType.SURVEY_CLOSED,
        )
        return survey


def _moved(ids: List[int], item: int, step: int) -> Dict[int, int]:
    order = list(ids)
    if item not in order:
        return {}
    i = order.index(item)
    j = max(0, min(len(order) - 1, i + (1 if step > 0 else -1)))
    order[i], order[j] = order[j], order[i]
    return {row_id: position for position, row_id in enumerate(order)}


def _describe(facts: Mapping[str, Any]) -> str:
    bits = []
    n = len(facts.get('tournaments') or [])
    if n:
        bits.append(f'played {n} tournament' + ('' if n == 1 else 's'))
    m = len(facts.get('positions') or [])
    if m:
        bits.append(f'worked {m} position' + ('' if m == 1 else 's'))
    if facts.get('crew'):
        bits.append('crewed')
    return 'Someone who ' + ' and '.join(bits) if bits else 'Someone who checked in'


async def _reachable(user_ids: set) -> Dict[int, User]:
    return await SurveyParticipationRepository.reachable_users(user_ids)
