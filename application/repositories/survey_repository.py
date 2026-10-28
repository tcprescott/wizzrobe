"""
Survey Repository - Data Access Layer

Post-event surveys: the survey and its sections and questions, the people it
was sent to, and their responses and answers.
"""

from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, TypeVar

from tortoise.expressions import F
from tortoise.functions import Count
from tortoise.models import Model

from application.event_writes import ensure_event_writable, guarded_delete, guarded_update
from application.repositories._base import EventScopedRepository
from application.repositories._event import event_scoped
from application.repositories._save import save_fields
from application.repositories._tenant import current_tenant_id, scoped
from models import (
    Survey,
    SurveyAnswer,
    SurveyCommentMark,
    SurveyCommentTag,
    SurveyPartTally,
    SurveyQuestion,
    SurveyRecipient,
    SurveyResponse,
    SurveyResponseStatus,
    SurveySection,
    SurveyStatus,
    SurveySuggestionBatch,
)

M = TypeVar('M', bound=Model)


class SurveyRepository(EventScopedRepository[Survey]):
    """Surveys belong to one event; sections and questions hang off them."""

    model = Survey

    @classmethod
    async def update(cls, obj: M, **fields: Any) -> M:
        """The base update, for a survey or any of its rows (section, question, recipient)."""
        return await super().update(obj, **fields)  # type: ignore[arg-type, return-value]

    @classmethod
    async def delete(cls, obj: Model) -> None:
        await obj.delete()

    @staticmethod
    async def get_full(survey_id: int) -> Optional[Survey]:
        """The survey with its sections and their questions loaded, in order."""
        return await Survey.get_or_none(id=survey_id, tenant_id=current_tenant_id()).prefetch_related(
            'sections__questions',
        )

    @staticmethod
    async def list_for_event(event_id: Optional[int] = None) -> List[Survey]:
        """The event's surveys, newest first (the ambient event unless named)."""
        return await event_scoped(Survey.all(), event_id).order_by('-created_at', '-id').prefetch_related('sections__questions')

    @staticmethod
    async def list_open() -> List[Survey]:
        """Every open survey in the community, across events (cross-event: closing sweeps them)."""
        return await scoped(Survey.filter(status=SurveyStatus.OPEN))

    # --- sections -------------------------------------------------------

    @staticmethod
    async def get_section(section_id: int) -> Optional[SurveySection]:
        return await SurveySection.get_or_none(id=section_id, tenant_id=current_tenant_id())

    @staticmethod
    async def create_section(survey: Survey, **fields: Any) -> SurveySection:
        return await SurveySection.create(tenant_id=current_tenant_id(), survey=survey, **fields)

    @staticmethod
    async def next_section_position(survey_id: int) -> int:
        positions = await scoped(SurveySection.filter(survey_id=survey_id)).values_list('position', flat=True)
        return max(positions, default=-1) + 1  # type: ignore[operator]

    @staticmethod
    async def section_ids(survey_id: int) -> List[int]:
        return list(await scoped(SurveySection.filter(survey_id=survey_id)).order_by('position', 'id')  # type: ignore[arg-type]
                    .values_list('id', flat=True))

    @staticmethod
    async def set_section_positions(order: Dict[int, int]) -> None:
        for section_id, position in order.items():
            await guarded_update(scoped(SurveySection.filter(id=section_id)), position=position)

    # --- questions ------------------------------------------------------

    @staticmethod
    async def get_question(question_id: int) -> Optional[SurveyQuestion]:
        return await SurveyQuestion.get_or_none(
            id=question_id, tenant_id=current_tenant_id(),
        ).prefetch_related('section')

    @staticmethod
    async def create_question(section: SurveySection, **fields: Any) -> SurveyQuestion:
        return await SurveyQuestion.create(tenant_id=current_tenant_id(), section=section, **fields)

    @staticmethod
    async def next_question_position(section_id: int) -> int:
        positions = await scoped(SurveyQuestion.filter(section_id=section_id)).values_list('position', flat=True)
        return max(positions, default=-1) + 1  # type: ignore[operator]

    @staticmethod
    async def question_ids(section_id: int) -> List[int]:
        return list(await scoped(SurveyQuestion.filter(section_id=section_id)).order_by('position', 'id')  # type: ignore[arg-type]
                    .values_list('id', flat=True))

    @staticmethod
    async def set_question_positions(order: Dict[int, int]) -> None:
        for question_id, position in order.items():
            await guarded_update(scoped(SurveyQuestion.filter(id=question_id)), position=position)

    @staticmethod
    async def place_question(question: SurveyQuestion, section_id: int, order: Dict[int, int]) -> None:
        """Move ``question`` into ``section_id`` and renumber that section's questions to ``order``."""
        if question.section_id != section_id:
            await guarded_update(scoped(SurveyQuestion.filter(id=question.id)), section_id=section_id)
        for question_id, position in order.items():
            await guarded_update(scoped(SurveyQuestion.filter(id=question_id)), position=position)

    @staticmethod
    async def questions_for_survey(survey_id: int) -> List[SurveyQuestion]:
        return await scoped(SurveyQuestion.filter(section__survey_id=survey_id)).order_by(
            'section__position', 'section_id', 'position', 'id',
        )

    # --- recipients -----------------------------------------------------

    @staticmethod
    async def add_recipients(survey: Survey, facts_by_user: Dict[int, dict]) -> int:
        """Record who the survey goes to; people already on it are left alone."""
        existing = set(await scoped(SurveyRecipient.filter(survey_id=survey.id)).values_list('user_id', flat=True))
        rows = [
            SurveyRecipient(tenant_id=current_tenant_id(), survey_id=survey.id, user_id=user_id, facts=facts)
            for user_id, facts in facts_by_user.items() if user_id not in existing
        ]
        if rows:
            await ensure_event_writable(survey.event_id)
            await SurveyRecipient.bulk_create(rows)
        return len(rows)

    @staticmethod
    async def get_recipient(survey_id: int, user_id: int) -> Optional[SurveyRecipient]:
        return await SurveyRecipient.get_or_none(
            survey_id=survey_id, user_id=user_id, tenant_id=current_tenant_id(),
        )

    @staticmethod
    async def recipients(survey_id: int, *, unanswered: bool = False,
                         remindable: bool = False) -> List[SurveyRecipient]:
        """``remindable``: not answered and not marked "Not interested"."""
        query = scoped(SurveyRecipient.filter(survey_id=survey_id))
        if unanswered or remindable:
            query = query.filter(submitted_at__isnull=True)
        if remindable:
            query = query.filter(declined_at__isnull=True)
        return await query.prefetch_related('user').order_by('id')

    @staticmethod
    async def recipient_counts(survey_id: int) -> Dict[str, int]:
        query = scoped(SurveyRecipient.filter(survey_id=survey_id))
        return {
            'sent': await query.count(),
            'opened': await query.filter(opened_at__isnull=False).count(),
            'submitted': await query.filter(submitted_at__isnull=False).count(),
        }

    @staticmethod
    async def progress_rows(survey_id: int) -> List[Dict[str, Any]]:
        """Each recipient's progress columns, for the drop-off card (no names)."""
        return await scoped(SurveyRecipient.filter(survey_id=survey_id)).values(
            'opened_at', 'submitted_at', 'declined_at', 'progress', 'last_part', 'finish_seconds', 'facts',
        )

    @staticmethod
    async def bump_part_tallies(survey: Survey, parts: Iterable[tuple[int, str, str, bool, bool]]) -> None:
        """Add one person's ``(section id, scope, label, opened, answered)`` parts to the running counts."""
        for section_id, scope, label, opened, answered in parts:
            tally, _ = await SurveyPartTally.get_or_create(
                survey_id=survey.id, section_id=section_id, scope=scope,
                defaults={'scope_label': label, 'tenant_id': current_tenant_id()},
            )
            await guarded_update(scoped(SurveyPartTally.filter(id=tally.id)),
                                 opened=F('opened') + int(opened), answered=F('answered') + int(answered))

    @staticmethod
    async def part_tallies(survey_id: int) -> List[SurveyPartTally]:
        return await scoped(SurveyPartTally.filter(survey_id=survey_id))

    @staticmethod
    async def recipient_totals(survey_ids: List[int]) -> tuple[Dict[int, int], Dict[int, int]]:
        """``({survey id: sent}, {survey id: answered})`` for a list of surveys, two queries."""
        if not survey_ids:
            return {}, {}
        query = scoped(SurveyRecipient.filter(survey_id__in=survey_ids))
        sent = await query.annotate(n=Count('id')).group_by('survey_id').values('survey_id', 'n')
        answered = await query.filter(submitted_at__isnull=False).annotate(n=Count('id')) \
            .group_by('survey_id').values('survey_id', 'n')
        return ({r['survey_id']: r['n'] for r in sent}, {r['survey_id']: r['n'] for r in answered})

    @staticmethod
    async def mark_recipients(recipient_ids: Iterable[int], **fields: Any) -> None:
        ids = list(recipient_ids)
        if ids:
            await guarded_update(scoped(SurveyRecipient.filter(id__in=ids)), **fields)

    @staticmethod
    async def open_surveys_for_user(user_id: int) -> List[SurveyRecipient]:
        """The open surveys ``user_id`` was sent and hasn't answered (cross-event: their own list)."""
        return await scoped(SurveyRecipient.filter(
            user_id=user_id, submitted_at__isnull=True, survey__status=SurveyStatus.OPEN,
        )).prefetch_related('survey')

    # --- responses ------------------------------------------------------

    @staticmethod
    async def draft_for(survey_id: int, user_id: int) -> Optional[SurveyResponse]:
        return await SurveyResponse.get_or_none(
            survey_id=survey_id, user_id=user_id, status=SurveyResponseStatus.DRAFT,
            tenant_id=current_tenant_id(),
        )

    @staticmethod
    async def create_response(survey: Survey, user_id: int) -> SurveyResponse:
        return await SurveyResponse.create(tenant_id=current_tenant_id(), survey=survey, user_id=user_id)

    @staticmethod
    async def answers_for(response_id: int) -> List[SurveyAnswer]:
        return await scoped(SurveyAnswer.filter(response_id=response_id))

    @staticmethod
    async def upsert_answer(
        response: SurveyResponse, question_id: int, scope: str, scope_label: str,
        value: Any, text: Optional[str],
    ) -> None:
        data = None if value is None else {'v': value}
        existing = await SurveyAnswer.get_or_none(
            response_id=response.id, question_id=question_id, scope=scope, tenant_id=current_tenant_id(),
        )
        if existing is None:
            await SurveyAnswer.create(
                tenant_id=current_tenant_id(), response=response, question_id=question_id,
                scope=scope, scope_label=scope_label, data=data, text=text,
            )
            return
        existing.scope_label, existing.data = scope_label, data
        existing.text = text  # type: ignore[assignment]
        await save_fields(existing, 'scope_label', 'data', 'text')

    @staticmethod
    async def clear_answer(response: SurveyResponse, question_id: int, scope: str) -> None:
        await guarded_delete(scoped(SurveyAnswer.filter(
            response_id=response.id, question_id=question_id, scope=scope,
        )))

    @staticmethod
    async def submit_response(
        response: SurveyResponse, *, anonymous: bool, groups: List[dict], submitted_on: date,
    ) -> None:
        response.status = SurveyResponseStatus.SUBMITTED
        response.anonymous = anonymous
        response.groups = groups
        response.submitted_on = submitted_on
        fields = ['status', 'anonymous', 'groups', 'submitted_on']
        if anonymous:
            response.user_id = None
            fields.append('user_id')
        await save_fields(response, *fields)

    @staticmethod
    async def submitted_responses(survey_id: int, *, limit: Optional[int] = None,
                                  offset: int = 0) -> List[SurveyResponse]:
        query = scoped(SurveyResponse.filter(
            survey_id=survey_id, status=SurveyResponseStatus.SUBMITTED,
        )).order_by('-id').offset(offset).prefetch_related('user', 'answers')
        if limit is not None:
            query = query.limit(limit)
        return await query

    @staticmethod
    async def submitted_count(survey_id: int) -> int:
        return await scoped(SurveyResponse.filter(
            survey_id=survey_id, status=SurveyResponseStatus.SUBMITTED,
        )).count()

    @staticmethod
    async def submitted_answers(survey_id: int) -> List[SurveyAnswer]:
        """Every answer on a submitted response, with its response's groups loaded."""
        return await scoped(SurveyAnswer.filter(
            response__survey_id=survey_id, response__status=SurveyResponseStatus.SUBMITTED,
        )).prefetch_related('response')


    # --- comments inbox ---------------------------------------------------

    @staticmethod
    async def comment_tags(survey_id: int) -> List[SurveyCommentTag]:
        return await scoped(SurveyCommentTag.filter(survey_id=survey_id)).order_by('position', 'id')

    @staticmethod
    async def get_comment_tag(tag_id: int) -> Optional[SurveyCommentTag]:
        return await SurveyCommentTag.get_or_none(id=tag_id, tenant_id=current_tenant_id())

    @staticmethod
    async def create_comment_tag(survey: Survey, name: str, position: int) -> SurveyCommentTag:
        return await SurveyCommentTag.create(tenant_id=current_tenant_id(), survey=survey, name=name, position=position)

    @staticmethod
    async def delete_comment_tag(tag: SurveyCommentTag) -> None:
        await guarded_delete(scoped(SurveyCommentTag.filter(id=tag.id)))

    @staticmethod
    async def comment_marks(survey_id: int) -> Dict[int, SurveyCommentMark]:
        """Each marked comment's read state and tags, by answer id."""
        marks = await scoped(SurveyCommentMark.filter(survey_id=survey_id)).prefetch_related('read_by')
        return {m.answer_id: m for m in marks}

    @staticmethod
    async def submitted_answer(answer_id: int) -> Optional[SurveyAnswer]:
        """One answer on a submitted response, with its response and question loaded."""
        return await scoped(SurveyAnswer.filter(
            id=answer_id, response__status=SurveyResponseStatus.SUBMITTED,
        )).prefetch_related('response__user', 'question__section').first()

    @classmethod
    async def save_comment_mark(cls, survey_id: int, answer_id: int, **fields: Any) -> SurveyCommentMark:
        mark, created = await SurveyCommentMark.get_or_create(
            answer_id=answer_id, tenant_id=current_tenant_id(),
            defaults={'survey_id': survey_id, **fields},
        )
        return mark if created or not fields else await cls.update(mark, **fields)


    # --- AI suggestions ---------------------------------------------------

    @staticmethod
    async def create_suggestion_batch(survey: Survey, section: SurveySection, **fields: Any) -> SurveySuggestionBatch:
        return await SurveySuggestionBatch.create(
            tenant_id=current_tenant_id(), survey=survey, section=section, **fields,
        )

    @staticmethod
    async def get_suggestion_batch(batch_id: int) -> Optional[SurveySuggestionBatch]:
        return await scoped(SurveySuggestionBatch.filter(id=batch_id)).first()

    @staticmethod
    async def open_suggestion_batches(survey_id: int) -> List[SurveySuggestionBatch]:
        """Batches on ``survey_id`` nobody has dismissed, newest first."""
        return await scoped(SurveySuggestionBatch.filter(
            survey_id=survey_id, dismissed_at__isnull=True,
        )).order_by('-id')

    @staticmethod
    async def suggestion_batches_since(since: datetime) -> int:
        return await scoped(SurveySuggestionBatch.filter(created_at__gte=since)).count()

    @staticmethod
    async def update_suggestion_batch(batch: SurveySuggestionBatch, **fields: Any) -> SurveySuggestionBatch:
        batch.update_from_dict(fields)
        await save_fields(batch, *fields)
        return batch

    @staticmethod
    async def latest_closed_survey(before_event_id: int) -> Optional[Survey]:
        """The community's most recent sent survey from another event, with its questions (cross-event by design)."""
        return await scoped(Survey.filter(
            status__in=[SurveyStatus.OPEN, SurveyStatus.CLOSED],
        ).exclude(event_id=before_event_id)).order_by('-sent_at', '-id') \
            .prefetch_related('sections__questions').first()
