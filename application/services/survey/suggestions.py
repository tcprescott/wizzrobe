"""AI-drafted survey questions: staff press "Suggest questions" on a section.

The AI never writes a question into a survey. It returns drafts, kept on a
:class:`~models.SurveySuggestionBatch`; a draft joins the survey only when a
staff member keeps it, through :meth:`SurveyService.add_question` like any
question they type. The provider and key are the community's own
(:class:`~application.services.AiProviderService`); there is no platform key.

What the AI is told is built by :mod:`.ai_context` from the event's structure
and totals, never people or what they wrote, and stored on the batch so
staff can read it back. Each community has a daily cap on requests.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from application.errors import require_found
from application.feature_flags import requires_feature
from application.repositories.survey_repository import SurveyRepository
from application.services.ai_provider_service import AiProviderService
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.survey import ai_context
from application.services.survey._validation import PROMPT_MAX, clean_options, clean_text
from application.services.survey.survey_service import SurveyService
from application.utils.clients.ai_client import PROVIDER_LABELS, AiProviderError, complete_json
from models import (
    AiProvider,
    FeatureFlag,
    SurveyQuestion,
    SurveyQuestionKind,
    SurveyStatus,
    SurveySuggestionBatch,
    User,
)

MAX_DRAFTS = 5
PENDING, KEPT = 'pending', 'kept'


def pending_drafts(batch: SurveySuggestionBatch) -> List[tuple[int, dict]]:
    """``(index, draft)`` for the drafts in ``batch`` nobody has kept yet."""
    if batch.dismissed_at is not None:
        return []
    return [(i, d) for i, d in enumerate(batch.drafts or []) if d.get('state') == PENDING]


def provider_label(batch: SurveySuggestionBatch) -> str:
    try:
        return PROVIDER_LABELS[AiProvider(batch.provider)]
    except ValueError:
        return batch.provider


def _clean_drafts(raw: Any) -> List[dict]:
    """The AI's answer, held to what a staff-written question may be; anything off is dropped."""
    drafts: List[dict] = []
    items = raw.get('questions') if isinstance(raw, dict) else None
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            kind = SurveyQuestionKind(str(item.get('kind')))
            prompt = clean_text(str(item.get('prompt') or ''), limit=PROMPT_MAX, what='The question')
            options = clean_options(kind, [str(o) for o in item.get('options') or []])
        except ValueError:
            continue
        if any(d['prompt'].lower() == prompt.lower() for d in drafts):
            continue
        drafts.append({'prompt': prompt, 'kind': kind.value, 'options': options or [], 'state': PENDING})
        if len(drafts) >= MAX_DRAFTS:
            break
    return drafts


class SurveySuggestionService:
    """Ask, keep, dismiss. Staff only, draft surveys only."""

    def __init__(self) -> None:
        self.repository = SurveyRepository()
        self.surveys = SurveyService()
        self.providers = AiProviderService()
        self.audit_service = AuditService()

    @staticmethod
    async def _ensure_staff(actor: User) -> None:
        await AuthService.ensure(await AuthService.is_staff(actor), 'Only Staff can manage surveys.')

    @requires_feature(FeatureFlag.SURVEYS)
    async def provider_label(self, actor: User) -> Optional[str]:
        """The configured provider's name, or ``None`` when the community has none (no button then)."""
        await self._ensure_staff(actor)
        return await self.providers.is_configured()

    @requires_feature(FeatureFlag.SURVEYS)
    async def open_batches(self, actor: User, survey_id: int) -> Dict[int, SurveySuggestionBatch]:
        """Per section, its newest batch that still has drafts waiting."""
        await self._ensure_staff(actor)
        latest: Dict[int, SurveySuggestionBatch] = {}
        for batch in await self.repository.open_suggestion_batches(survey_id):
            if batch.section_id not in latest and pending_drafts(batch):
                latest[batch.section_id] = batch
        return latest

    @requires_feature(FeatureFlag.SURVEYS)
    async def suggest(self, actor: User, section_id: int) -> SurveySuggestionBatch:
        await self._ensure_staff(actor)
        section = require_found(await self.repository.get_section(section_id), 'Section')
        survey = require_found(await self.repository.get_full(section.survey_id), 'Survey')
        if survey.status is not SurveyStatus.DRAFT:
            raise ValueError("This survey has been sent, so its questions can't change.")
        section = next(s for s in survey.sections if s.id == section_id)
        provider = await self.providers.resolve()
        if provider is None:
            raise ValueError('Set up an AI provider under Settings → Integrations first.')
        since = datetime.now(timezone.utc) - timedelta(days=1)
        if await self.repository.suggestion_batches_since(since) >= provider.daily_limit:
            raise ValueError(f'This community has used its {provider.daily_limit} AI requests for the last 24 hours. '
                             'Staff can raise the limit under Settings → Integrations.')

        facts = await ai_context.load_facts(survey.event_id)
        previous = await self.repository.latest_closed_survey(survey.event_id)
        previous_results = (await self.surveys.results(actor, previous.id)).questions if previous else []
        prompt = ai_context.build(survey, section, facts, previous, previous_results)
        try:
            answer = await complete_json(
                provider.provider, api_key=provider.api_key, model=provider.model, base_url=provider.base_url,
                system=ai_context.SYSTEM, prompt=prompt, schema=ai_context.SCHEMA,
            )
        except AiProviderError as e:
            raise ValueError(str(e)) from None
        drafts = _clean_drafts(answer.data)
        batch = await self.repository.create_suggestion_batch(
            survey, section, requested_by=actor, provider=provider.provider.value, model=provider.model,
            context=f'{ai_context.SYSTEM}\n\n{prompt}', drafts=drafts,
            input_tokens=answer.input_tokens, output_tokens=answer.output_tokens,
        )
        await self.audit_service.write_log(actor, AuditActions.SURVEY_QUESTIONS_SUGGESTED, {
            'survey_id': survey.id, 'section_id': section.id, 'provider': provider.provider.value,
            'model': provider.model, 'drafts': len(drafts),
            'input_tokens': answer.input_tokens, 'output_tokens': answer.output_tokens,
        })
        if not drafts:
            raise ValueError("The AI didn't come back with any usable questions. Try again.")
        return batch

    async def _batch(self, batch_id: int) -> SurveySuggestionBatch:
        return require_found(await self.repository.get_suggestion_batch(batch_id), 'Suggestions')

    @requires_feature(FeatureFlag.SURVEYS)
    async def keep(self, actor: User, batch_id: int, index: int) -> SurveyQuestion:
        """Add draft ``index`` to its section as a question staff wrote, then mark it kept."""
        await self._ensure_staff(actor)
        batch = await self._batch(batch_id)
        drafts = [dict(d) for d in batch.drafts or []]
        if not 0 <= index < len(drafts) or drafts[index].get('state') != PENDING or batch.dismissed_at:
            raise ValueError('That suggestion is gone. Ask for new ones.')
        draft = drafts[index]
        question = await self.surveys.add_question(
            actor, batch.section_id, draft['prompt'], SurveyQuestionKind(draft['kind']), options=draft.get('options'),
        )
        drafts[index]['state'] = KEPT
        await self.repository.update_suggestion_batch(batch, drafts=drafts)
        return question

    @requires_feature(FeatureFlag.SURVEYS)
    async def dismiss(self, actor: User, batch_id: int) -> None:
        await self._ensure_staff(actor)
        batch = await self._batch(batch_id)
        if batch.dismissed_at is None:
            await self.repository.update_suggestion_batch(batch, dismissed_at=datetime.now(timezone.utc))

    @requires_feature(FeatureFlag.SURVEYS)
    async def sent_context(self, actor: User, batch_id: int) -> str:
        """Exactly what the provider was sent for ``batch_id``."""
        await self._ensure_staff(actor)
        return (await self._batch(batch_id)).context
