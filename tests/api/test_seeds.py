"""REST API tests for the seed-generation endpoints (api/routers/seeds.py).

Covers the randomizer catalogue read, the roll-a-seed write (mocked so no real
randomizer backend is contacted), auth gating, and cross-tenant preset scoping.
"""

import pytest

from application.services import SeedGenerationService
from application.tenant_context import tenant_scope
from models import (
    GeneratedSeeds,
    Match,
    Preset,
    ProviderTask,
    ProviderTaskStatus,
    RandomizerCredential,
    Role,
    Tenant,
    Tournament,
)
from tests.api_helpers import client_for, create_user_token


@pytest.fixture(autouse=True)
def mock_seedgen(monkeypatch):
    monkeypatch.setenv('MOCK_SEEDGEN', '1')


async def test_list_randomizers(db, app):
    _, raw = await create_user_token(username='reader', read_only=True)
    async with client_for(app, raw) as c:
        resp = await c.get('/api/seeds/randomizers')
        assert resp.status_code == 200
        body = resp.json()
        keyed = {'ootr', 'smmap', 'dk64r'}
        assert {r['randomizer'] for r in body} == (
            set(SeedGenerationService.AVAILABLE_RANDOMIZERS) - keyed
        )
        by_name = {r['randomizer']: r['supports_triforce_texts'] for r in body}
        assert by_name['alttpr'] is True
        assert by_name['ff1r'] is False


async def test_configuring_a_credential_adds_its_randomizer(db, app):
    await RandomizerCredential.create(randomizer='ootr', key='api_key', value='k')
    _, raw = await create_user_token(username='reader2', read_only=True)
    async with client_for(app, raw) as c:
        resp = await c.get('/api/seeds/randomizers')
        names = {r['randomizer'] for r in resp.json()}
        assert 'ootr' in names
        assert 'dk64r' not in names


async def test_randomizers_requires_auth(db, app):
    async with client_for(app) as c:
        resp = await c.get('/api/seeds/randomizers')
        assert resp.status_code == 401


async def test_uncredentialed_randomizer_dropped_from_catalogue(db, app):
    b = await Tenant.create(name='NoDK', slug='no-dk')
    with tenant_scope(b.id):
        _, token_b = await create_user_token(username='b-reader', roles=[Role.STAFF])
    async with client_for(app, token_b) as c:
        resp = await c.get('/api/seeds/randomizers')
        assert resp.status_code == 200
        names = {r['randomizer'] for r in resp.json()}
        assert 'dk64r' not in names
        assert 'alttpr' in names


async def test_generate_seed_happy_path(db, app):
    _, raw = await create_user_token(username='roller', roles=[Role.PRESET_MANAGER])
    async with client_for(app, raw) as c:
        resp = await c.post('/api/seeds', json={'randomizer': 'alttpr'})
        assert resp.status_code == 200
        assert resp.json()['url'].startswith('https://mock.seedgen.local/alttpr/')


async def test_generate_seed_with_preset(db, app):
    _, raw = await create_user_token(username='roller', roles=[Role.PRESET_MANAGER])
    preset = await Preset.create(name='Custom', randomizer='alttpr', settings={'foo': 'bar'})
    async with client_for(app, raw) as c:
        resp = await c.post('/api/seeds', json={'randomizer': 'alttpr', 'preset_id': preset.id})
        assert resp.status_code == 200
        assert resp.json()['url'].startswith('https://mock.seedgen.local/alttpr/')


async def test_generate_seed_queued_randomizer_202(db, app):
    _, raw = await create_user_token(username='roller', roles=[Role.PRESET_MANAGER])
    async with client_for(app, raw) as c:
        resp = await c.post('/api/seeds', json={'randomizer': 'dk64r'})
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body['randomizer'] == 'dk64r'
        assert set(body) == {'task_id', 'randomizer', 'status', 'url', 'error'}
        polled = await c.get(f"/api/seeds/tasks/{body['task_id']}")
        assert polled.status_code == 200
        assert polled.json()['task_id'] == body['task_id']


async def test_task_for_a_match_reports_its_seed(db, app):
    _, raw = await create_user_token(username='roller', roles=[Role.PRESET_MANAGER])
    tournament = await Tournament.create(name='Cup', is_active=True, seed_generator='dk64r')
    pending = await Match.create(tournament=tournament)
    seed = await GeneratedSeeds.create(seed_url='https://dk64randomizer.com/x', randomizer='dk64r')
    rolled = await Match.create(tournament=tournament, generated_seed=seed)
    queued = await ProviderTask.create(provider='dk64r', operation='generate_seed', match=pending)
    done = await ProviderTask.create(
        provider='dk64r', operation='generate_seed', match=rolled,
        status=ProviderTaskStatus.SUCCEEDED,
    )
    async with client_for(app, raw) as c:
        waiting = await c.get(f'/api/seeds/tasks/{queued.id}')
        assert waiting.status_code == 200, waiting.text
        assert waiting.json()['url'] is None
        finished = await c.get(f'/api/seeds/tasks/{done.id}')
        assert finished.status_code == 200, finished.text
        assert finished.json()['url'] == 'https://dk64randomizer.com/x'


async def test_generate_seed_unknown_preset_404(db, app):
    _, raw = await create_user_token(username='roller', roles=[Role.PRESET_MANAGER])
    async with client_for(app, raw) as c:
        resp = await c.post('/api/seeds', json={'randomizer': 'alttpr', 'preset_id': 999999})
        assert resp.status_code == 404


async def test_generate_seed_unsupported_randomizer_400(db, app):
    _, raw = await create_user_token(username='roller', roles=[Role.PRESET_MANAGER])
    async with client_for(app, raw) as c:
        resp = await c.post('/api/seeds', json={'randomizer': 'nope'})
        assert resp.status_code == 400


async def test_generate_seed_uncredentialed_randomizer_400(db, app):
    # MOCK_SEEDGEN off for this case: the mock short-circuits before any credential is read.
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.delenv('MOCK_SEEDGEN', raising=False)
    b = await Tenant.create(name='NoDK2', slug='no-dk2')
    with tenant_scope(b.id):
        _, token_b = await create_user_token(username='b-roller', roles=[Role.STAFF])
    try:
        async with client_for(app, token_b) as c:
            resp = await c.post('/api/seeds', json={'randomizer': 'dk64r'})
            assert resp.status_code == 400
            assert 'DK64 Randomizer API key is not configured' in resp.json()['detail']
    finally:
        monkeypatch.undo()


async def test_generate_seed_requires_auth(db, app):
    async with client_for(app) as c:
        resp = await c.post('/api/seeds', json={'randomizer': 'alttpr'})
        assert resp.status_code == 401


async def test_generate_seed_rejects_read_only_token(db, app):
    _, raw = await create_user_token(username='ro', read_only=True)
    async with client_for(app, raw) as c:
        resp = await c.post('/api/seeds', json={'randomizer': 'alttpr'})
        assert resp.status_code == 403


async def test_generate_seed_refuses_a_plain_member(db, app):
    """A free roll spends the community's randomizer keys: players can't."""
    _, raw = await create_user_token(username='player')
    async with client_for(app, raw) as c:
        resp = await c.post('/api/seeds', json={'randomizer': 'alttpr'})
        assert resp.status_code == 403


@pytest.mark.parametrize('role', [Role.PROCTOR, Role.VOLUNTEER])
async def test_generate_seed_refuses_other_roles(db, app, role):
    _, raw = await create_user_token(username='other', roles=[role])
    async with client_for(app, raw) as c:
        resp = await c.post('/api/seeds', json={'randomizer': 'alttpr'})
        assert resp.status_code == 403


async def test_generate_seed_allows_staff(db, app):
    _, raw = await create_user_token(username='staffer', roles=[Role.STAFF])
    async with client_for(app, raw) as c:
        resp = await c.post('/api/seeds', json={'randomizer': 'alttpr'})
        assert resp.status_code == 200


# --- cross-tenant preset scoping -----------------------------------------

async def test_preset_is_tenant_scoped(db, app):
    a = await Tenant.get(id=1)
    b = await Tenant.create(name='Beta', slug='beta')
    with tenant_scope(a.id):
        preset_a = await Preset.create(name='A Preset', randomizer='alttpr', settings={})
    with tenant_scope(b.id):
        _, token_b = await create_user_token(username='b-roller', roles=[Role.STAFF])
    async with client_for(app, token_b) as c:
        resp = await c.post('/api/seeds', json={'randomizer': 'alttpr', 'preset_id': preset_a.id})
        assert resp.status_code == 404
