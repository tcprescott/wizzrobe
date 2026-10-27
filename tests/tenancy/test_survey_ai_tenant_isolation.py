"""Cross-tenant isolation (leak) tests for ``AiProviderSetting`` and ``SurveySuggestionBatch``.

A community's AI key is its own: another community never calls a provider on
it, and the sealed key doesn't even open there. Suggestion batches carry event
facts, so they stay inside their community too.
"""

from application.repositories import AiProviderRepository, SurveyRepository
from application.tenant_context import tenant_scope
from application.utils import secret_box
from models import AiProvider, AiProviderSetting, Survey, SurveySection, SurveySuggestionBatch


async def test_provider_settings_are_isolated(two_tenants):
    a, b = two_tenants
    repo = AiProviderRepository()
    with tenant_scope(a.id):
        row_a = await AiProviderSetting.create(
            provider=AiProvider.ANTHROPIC, model='m',
            key_ciphertext=secret_box.seal('key-a', purpose='ai-provider-key', context=f'tenant:{a.id}'),
        )
    with tenant_scope(b.id):
        assert await repo.get_current() is None
        row_b = await AiProviderSetting.create(provider=AiProvider.OPENAI_COMPATIBLE, model='m', key_ciphertext='x')
        assert (await repo.get_current()).id == row_b.id
        assert await repo.delete_current()
    with tenant_scope(a.id):
        assert (await repo.get_current()).id == row_a.id
    # The sealed key is bound to its community.
    assert secret_box.open_(row_a.key_ciphertext, purpose='ai-provider-key', context=f'tenant:{b.id}') is None


async def _batch(title: str) -> SurveySuggestionBatch:
    survey = await Survey.create(title=title)
    section = await SurveySection.create(survey=survey, title='Overall')
    return await SurveySuggestionBatch.create(survey=survey, section=section, provider='anthropic', model='m',
                                              context='facts', drafts=[])


async def test_suggestion_batches_are_isolated(two_tenants):
    a, b = two_tenants
    repo = SurveyRepository()
    with tenant_scope(a.id):
        batch_a = await _batch('A')
    with tenant_scope(b.id):
        batch_b = await _batch('B')
        assert await repo.get_suggestion_batch(batch_a.id) is None
        assert [x.id for x in await repo.open_suggestion_batches(batch_a.survey_id)] == []
    with tenant_scope(a.id):
        assert (await repo.get_suggestion_batch(batch_a.id)).id == batch_a.id
        assert await repo.get_suggestion_batch(batch_b.id) is None
