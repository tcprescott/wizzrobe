"""REST API tests for the preset endpoints (api/routers/presets.py).

Presets are tenant-scoped seed-rolling settings blobs. Reads accept any token;
the management gate (STAFF / SUPER_ADMIN / PRESET_MANAGER) lives in
``PresetService`` and is exercised via the coarse read/write HTTP deps.
"""

import pytest

from application.tenant_context import tenant_scope
from models import Preset, Role, Tenant
from tests.api_helpers import client_for, create_user_token


async def _staff_token(username='staff'):
    return await create_user_token(username=username, roles=[Role.STAFF])


async def _make_preset(name='Standard', randomizer='alttpr', settings=None, description=None):
    return await Preset.create(
        name=name,
        randomizer=randomizer,
        settings=settings if settings is not None else {'mode': 'open'},
        description=description,
    )


class TestReads:
    async def test_list_presets_happy_path(self, db, app):
        _, raw = await _staff_token()
        await _make_preset(name='Standard', randomizer='alttpr')
        await _make_preset(name='Chaos', randomizer='z1r')
        async with client_for(app, raw) as c:
            resp = await c.get('/api/presets')
            assert resp.status_code == 200
            names = {p['name'] for p in resp.json()}
            assert names == {'Standard', 'Chaos'}

    async def test_list_presets_forbidden_for_plain_user(self, db, app):
        """list_presets is gated by the service -> 403 for a role-less token."""
        _, raw = await create_user_token(username='plain')
        await _make_preset()
        async with client_for(app, raw) as c:
            resp = await c.get('/api/presets')
            assert resp.status_code == 403

    async def test_list_by_randomizer_is_ungated(self, db, app):
        """The ?randomizer filter uses the ungated read path — a plain token works."""
        _, raw = await create_user_token(username='plain')
        await _make_preset(name='Standard', randomizer='alttpr')
        await _make_preset(name='Chaos', randomizer='z1r')
        async with client_for(app, raw) as c:
            resp = await c.get('/api/presets', params={'randomizer': 'alttpr'})
            assert resp.status_code == 200
            body = resp.json()
            assert [p['name'] for p in body] == ['Standard']

    async def test_list_selectable_is_ungated_but_hides_settings(self, db, app):
        """A tournament editor needs the name; the settings can be race-day secret."""
        _, raw = await create_user_token(username='plain')
        await _make_preset(name='Standard', settings={'goal': 'ganon'})
        async with client_for(app, raw) as c:
            for resp in (
                await c.get('/api/presets/selectable'),
                await c.get('/api/presets', params={'randomizer': 'alttpr'}),
            ):
                assert resp.status_code == 200
                assert [p['name'] for p in resp.json()] == ['Standard']
                assert resp.json()[0]['settings'] == {}

    async def test_preset_managers_see_settings_in_the_picker_reads(self, db, app):
        _, raw = await create_user_token(username='pm', roles=[Role.PRESET_MANAGER])
        await _make_preset(name='Standard', settings={'goal': 'ganon'})
        async with client_for(app, raw) as c:
            for resp in (
                await c.get('/api/presets/selectable'),
                await c.get('/api/presets', params={'randomizer': 'alttpr'}),
            ):
                assert resp.json()[0]['settings'] == {'goal': 'ganon'}

    async def test_get_preset_happy_path(self, db, app):
        _, raw = await _staff_token()
        preset = await _make_preset(name='Standard', description='the default')
        async with client_for(app, raw) as c:
            resp = await c.get(f'/api/presets/{preset.id}')
            assert resp.status_code == 200
            body = resp.json()
            assert body['id'] == preset.id
            assert body['description'] == 'the default'
            assert body['settings'] == {'mode': 'open'}

    async def test_get_preset_not_found(self, db, app):
        _, raw = await _staff_token()
        async with client_for(app, raw) as c:
            resp = await c.get('/api/presets/9999')
            assert resp.status_code == 404

    async def test_unauthenticated_rejected(self, db, app):
        await _make_preset()
        async with client_for(app) as c:
            resp = await c.get('/api/presets')
            assert resp.status_code == 401

    async def test_preset_manager_can_list(self, db, app):
        """A PRESET_MANAGER token (not STAFF) gets 200 on GET '' — proving the
        coarse require_api_actor dep plus the in-service gate (not require_staff)."""
        _, raw = await create_user_token(username='pm', roles=[Role.PRESET_MANAGER])
        await _make_preset()
        async with client_for(app, raw) as c:
            resp = await c.get('/api/presets')
            assert resp.status_code == 200
            assert len(resp.json()) == 1


class TestWrites:
    async def test_create_preset_success(self, db, app):
        _, raw = await _staff_token()
        async with client_for(app, raw) as c:
            resp = await c.post('/api/presets', json={
                'name': 'Open',
                'randomizer': 'alttpr',
                'settings': {'goal': 'ganon'},
                'description': 'open mode',
            })
            assert resp.status_code == 201
            body = resp.json()
            assert body['name'] == 'Open'
            assert body['randomizer'] == 'alttpr'
            assert body['settings'] == {'goal': 'ganon'}

    async def test_create_preset_unknown_randomizer_bad_request(self, db, app):
        _, raw = await _staff_token()
        async with client_for(app, raw) as c:
            resp = await c.post('/api/presets', json={
                'name': 'Bad', 'randomizer': 'nope', 'settings': {},
            })
            assert resp.status_code == 400

    async def test_create_preset_forbidden_for_plain_user(self, db, app):
        _, raw = await create_user_token(username='plain')
        async with client_for(app, raw) as c:
            resp = await c.post('/api/presets', json={
                'name': 'X', 'randomizer': 'alttpr', 'settings': {},
            })
            assert resp.status_code == 403

    async def test_create_preset_read_only_token_forbidden(self, db, app):
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF], read_only=True)
        async with client_for(app, raw) as c:
            resp = await c.post('/api/presets', json={
                'name': 'X', 'randomizer': 'alttpr', 'settings': {},
            })
            assert resp.status_code == 403

    async def test_update_preset_success(self, db, app):
        _, raw = await _staff_token()
        preset = await _make_preset(name='Old')
        async with client_for(app, raw) as c:
            resp = await c.patch(f'/api/presets/{preset.id}', json={'name': 'New'})
            assert resp.status_code == 200
            assert resp.json()['name'] == 'New'

    async def test_update_preset_not_found(self, db, app):
        _, raw = await _staff_token()
        async with client_for(app, raw) as c:
            resp = await c.patch('/api/presets/9999', json={'name': 'Nope'})
            assert resp.status_code == 404

    async def test_delete_preset_success(self, db, app):
        _, raw = await _staff_token()
        preset = await _make_preset(name='Temp')
        async with client_for(app, raw) as c:
            resp = await c.delete(f'/api/presets/{preset.id}')
            assert resp.status_code == 204
        assert await Preset.get_or_none(id=preset.id) is None

    async def test_delete_preset_not_found(self, db, app):
        _, raw = await _staff_token()
        async with client_for(app, raw) as c:
            resp = await c.delete('/api/presets/9999')
            assert resp.status_code == 404

    async def test_import_builtins_success(self, db, app):
        _, raw = await _staff_token()
        async with client_for(app, raw) as c:
            resp = await c.post('/api/presets/import-builtins')
            assert resp.status_code == 200
            assert isinstance(resp.json(), list)

    async def test_import_builtins_read_only_token_forbidden(self, db, app):
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF], read_only=True)
        async with client_for(app, raw) as c:
            resp = await c.post('/api/presets/import-builtins')
            assert resp.status_code == 403


class TestTenantIsolation:
    @pytest.fixture
    async def two_tenants(self, db):
        a = await Tenant.get(id=1)
        b = await Tenant.create(name='Beta', slug='beta')
        with tenant_scope(a.id):
            preset_a = await _make_preset(name='A-Preset')
        with tenant_scope(b.id):
            _, token_b = await create_user_token(username='b-staff', roles=[Role.STAFF])
        return {'preset_a': preset_a, 'token_b': token_b}

    async def test_cross_tenant_get_by_id_404(self, app, two_tenants):
        ctx = two_tenants
        async with client_for(app, ctx['token_b']) as c:
            resp = await c.get(f"/api/presets/{ctx['preset_a'].id}")
            assert resp.status_code == 404

    async def test_list_omits_other_tenant_preset(self, app, two_tenants):
        ctx = two_tenants
        async with client_for(app, ctx['token_b']) as c:
            resp = await c.get('/api/presets')
            assert resp.status_code == 200
            assert ctx['preset_a'].id not in {p['id'] for p in resp.json()}
