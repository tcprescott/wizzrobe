"""Survey AI suggestions, the AI provider setting, and the sealed key behind it."""

from datetime import datetime, timedelta, timezone

import pytest

from application.errors import FeatureDisabledError
from application.services import AiProviderService, SurveyService, SurveySuggestionService
from application.services.feature_flag_service import reset_flag_cache
from application.services.survey.suggestions import _clean_drafts, pending_drafts
from application.tenant_context import tenant_scope
from application.utils import secret_box
from models import (
    AuditLog,
    FeatureFlag,
    Match,
    MatchPlayers,
    MatchRescheduleRequest,
    Role,
    SurveyQuestion,
    SurveySuggestionBatch,
    TenantFeatureFlag,
    Tournament,
    UserRole,
)
from tests.factories import make_member

NOW = datetime.now(timezone.utc)


@pytest.fixture
async def world(db, monkeypatch):
    monkeypatch.delenv('MOCK_AI', raising=False)
    staff = await make_member(100, 'staff')
    await UserRole.create(user=staff, role=Role.STAFF)
    alice = await make_member(1, 'alice_the_player')
    tournament = await Tournament.create(name='ALTTPR')
    match = await Match.create(tournament=tournament, scheduled_at=NOW, started_at=NOW + timedelta(minutes=12))
    await MatchPlayers.create(match=match, user=alice)
    await MatchRescheduleRequest.create(match=match, requested_by=alice, reason='my secret dentist appointment')
    survey = await SurveyService().create_survey(staff, 'SGL26 survey')
    return {'staff': staff, 'alice': alice, 'survey': survey, 'section': survey.sections[0]}


async def _set_key(staff, *, limit: int = 20) -> None:
    await AiProviderService().save(staff, provider='anthropic', model='claude-opus-5-5', base_url=None,
                                   api_key='mock-key', daily_limit=limit)


def test_secret_box_round_trips_only_in_its_own_context():
    token = secret_box.seal('sk-real', purpose='ai', context='tenant:1')
    assert token.startswith('v1:') and 'sk-real' not in token
    assert secret_box.open_(token, purpose='ai', context='tenant:1') == 'sk-real'
    assert secret_box.open_(token, purpose='ai', context='tenant:2') is None
    assert secret_box.open_(token, purpose='other', context='tenant:1') is None
    assert secret_box.open_('v1:garbage', purpose='ai', context='tenant:1') is None
    assert secret_box.open_('', purpose='ai', context='tenant:1') is None


async def test_provider_save_keeps_the_key_out_of_status_and_audit(world):
    service = AiProviderService()
    with pytest.raises(ValueError, match='Paste the API key'):
        await service.save(world['staff'], provider='anthropic', model='m', base_url=None, api_key='',
                           daily_limit=20)
    await service.save(world['staff'], provider='anthropic', model='m', base_url=None, api_key='sk-ant-secret',
                       daily_limit=20)
    status = await service.status(world['staff'])
    assert status['configured'] and status['key_readable']
    assert 'sk-ant-secret' not in repr(status)
    # A blank key keeps the stored one.
    await service.save(world['staff'], provider='anthropic', model='m2', base_url=None, api_key=None, daily_limit=5)
    resolved = await service.resolve()
    assert resolved is not None and resolved.api_key == 'sk-ant-secret' and resolved.daily_limit == 5
    logs = await AuditLog.filter(action='ai_provider.set')
    assert len(logs) == 2 and all('sk-ant' not in repr(log.details) for log in logs)


async def test_provider_rules(world):
    service = AiProviderService()
    staff = world['staff']
    with pytest.raises(ValueError, match='provider address'):
        await service.save(staff, provider='openai_compatible', model='m', base_url='', api_key='k', daily_limit=20)
    with pytest.raises(ValueError, match='between'):
        await service.save(staff, provider='anthropic', model='m', base_url=None, api_key='k', daily_limit=0)
    with pytest.raises(ValueError, match='provider from the list'):
        await service.save(staff, provider='nope', model='m', base_url=None, api_key='k', daily_limit=20)
    with pytest.raises(PermissionError):
        await service.save(world['alice'], provider='anthropic', model='m', base_url=None, api_key='k',
                           daily_limit=20)
    with pytest.raises(PermissionError):
        await service.status(world['alice'])
    await service.save(staff, provider='openai_compatible', model='gpt', base_url='https://api.example.com/v1',
                       api_key='k', daily_limit=20)
    assert (await service.status(staff))['base_url'] == 'https://api.example.com/v1'
    await service.clear(staff)
    assert await service.resolve() is None
    assert await AuditLog.filter(action='ai_provider.cleared').count() == 1


async def test_provider_key_stays_in_its_community(world, two_tenants):
    await _set_key(world['staff'])
    _, tenant_b = two_tenants
    with tenant_scope(tenant_b.id):
        assert await AiProviderService().resolve() is None


async def test_suggest_needs_a_provider(world):
    with pytest.raises(ValueError, match='Set up an AI provider'):
        await SurveySuggestionService().suggest(world['staff'], world['section'].id)


async def test_suggest_sends_no_names_or_player_text(world):
    await _set_key(world['staff'])
    batch = await SurveySuggestionService().suggest(world['staff'], world['section'].id)
    assert batch.provider == 'anthropic' and pending_drafts(batch)
    assert 'ALTTPR' in batch.context
    assert 'alice' not in batch.context.lower()
    assert 'dentist' not in batch.context
    assert await AuditLog.filter(action='survey.questions_suggested').count() == 1


async def test_suggest_is_staff_only(world):
    await _set_key(world['staff'])
    with pytest.raises(PermissionError):
        await SurveySuggestionService().suggest(world['alice'], world['section'].id)


async def test_daily_cap(world):
    await _set_key(world['staff'], limit=1)
    service = SurveySuggestionService()
    await service.suggest(world['staff'], world['section'].id)
    with pytest.raises(ValueError, match='used its 1 AI requests'):
        await service.suggest(world['staff'], world['section'].id)


async def test_keep_adds_the_question_once_and_dismiss_hides_the_rest(world):
    await _set_key(world['staff'])
    service = SurveySuggestionService()
    batch = await service.suggest(world['staff'], world['section'].id)
    index, draft = pending_drafts(batch)[0]
    question = await service.keep(world['staff'], batch.id, index)
    assert question.prompt == draft['prompt'] and question.section_id == world['section'].id
    with pytest.raises(ValueError, match='gone'):
        await service.keep(world['staff'], batch.id, index)
    await service.dismiss(world['staff'], batch.id)
    assert await service.open_batches(world['staff'], world['survey'].id) == {}
    assert world['section'].id == (await SurveyQuestion.get(id=question.id)).section_id
    assert 'ALTTPR' in await service.sent_context(world['staff'], batch.id)


async def test_sent_survey_takes_no_suggestions(world):
    await _set_key(world['staff'])
    await SurveyService().send(world['staff'], world['survey'].id, closes_at=NOW + timedelta(days=7))
    with pytest.raises(ValueError, match='been sent'):
        await SurveySuggestionService().suggest(world['staff'], world['section'].id)


async def test_suggestions_follow_the_surveys_flag(world):
    await _set_key(world['staff'])
    await TenantFeatureFlag.filter(flag=FeatureFlag.SURVEYS.value).update(enabled=False)
    reset_flag_cache()
    with pytest.raises(FeatureDisabledError):
        await SurveySuggestionService().suggest(world['staff'], world['section'].id)
    assert await SurveySuggestionBatch.all().count() == 0


def test_clean_drafts_drops_anything_a_staff_question_couldnt_be():
    drafts = _clean_drafts({'questions': [
        {'prompt': 'How was it?', 'kind': 'rating'},
        {'prompt': 'how was it?', 'kind': 'rating'},                       # duplicate
        {'prompt': 'Pick', 'kind': 'choice_one', 'options': ['Only one']},  # too few options
        {'prompt': '', 'kind': 'yes_no'},                                   # blank
        {'prompt': 'Bad kind', 'kind': 'essay'},
        'not a dict',
        {'prompt': 'Agree?', 'kind': 'agree_scale', 'options': ['Rooms ran on time', 'Signs were clear']},
    ]})
    assert [d['prompt'] for d in drafts] == ['How was it?', 'Agree?']
    assert drafts[1]['options'] == ['Rooms ran on time', 'Signs were clear']
    assert _clean_drafts('nonsense') == []
    many = _clean_drafts({'questions': [{'prompt': f'Q{i}', 'kind': 'comment'} for i in range(9)]})
    assert len(many) == 5
