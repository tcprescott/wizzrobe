"""REST API tests for async qualifier live-race endpoints.

Covers happy-path reads/writes with a QUALIFIER_ADMIN token, auth (401/403 for
missing/read-only/role-less tokens), 404 vs 400 boundaries, and cross-tenant
isolation. Fixtures build the qualifier + pool through the service layer under a
tenant scope, mirroring the repository conventions.
"""

import pytest

from application.services import AsyncQualifierLiveRaceService, AsyncQualifierService
from application.tenant_context import tenant_scope
from models import (
    AsyncQualifierLiveRace,
    AsyncQualifierLiveRaceStatus,
    Role,
    Tenant,
    User,
)
from tests.api_helpers import client_for, create_user_token, enable_all_features


async def _make_qualifier_and_pool(actor: User, *, name='Q'):
    svc = AsyncQualifierService()
    qualifier = await svc.create_qualifier(actor, name=name)
    pool = await svc.create_pool(actor, qualifier.id, name=f'{name}-pool')
    return qualifier, pool


async def _admin_token(username='qadmin'):
    return await create_user_token(username=username, roles=[Role.QUALIFIER_ADMIN])


class TestReads:
    async def test_list_live_races_happy_path(self, db, app):
        actor, raw = await _admin_token()
        qualifier, pool = await _make_qualifier_and_pool(actor)
        lr = await AsyncQualifierLiveRaceService().create_live_race(
            actor, pool.id, match_title='Race One'
        )
        async with client_for(app, raw) as c:
            resp = await c.get('/api/async-qualifiers/live-races', params={'qualifier_id': qualifier.id})
            assert resp.status_code == 200
            rows = resp.json()
            assert [r['id'] for r in rows] == [lr.id]
            assert rows[0]['match_title'] == 'Race One'
            assert rows[0]['status'] == AsyncQualifierLiveRaceStatus.SCHEDULED.value

    async def test_list_requires_qualifier_id(self, db, app):
        _, raw = await _admin_token()
        async with client_for(app, raw) as c:
            resp = await c.get('/api/async-qualifiers/live-races')
            assert resp.status_code == 422

    async def test_get_live_race_happy_path(self, db, app):
        actor, raw = await _admin_token()
        _, pool = await _make_qualifier_and_pool(actor)
        lr = await AsyncQualifierLiveRaceService().create_live_race(
            actor, pool.id, match_title='Detail'
        )
        async with client_for(app, raw) as c:
            resp = await c.get(f'/api/async-qualifiers/live-races/{lr.id}')
            assert resp.status_code == 200
            assert resp.json()['match_title'] == 'Detail'

    async def test_get_missing_live_race_is_404(self, db, app):
        _, raw = await _admin_token()
        async with client_for(app, raw) as c:
            assert (await c.get('/api/async-qualifiers/live-races/9999')).status_code == 404

    async def test_list_runs_empty(self, db, app):
        actor, raw = await _admin_token()
        _, pool = await _make_qualifier_and_pool(actor)
        lr = await AsyncQualifierLiveRaceService().create_live_race(
            actor, pool.id, match_title='NoRuns'
        )
        async with client_for(app, raw) as c:
            resp = await c.get(f'/api/async-qualifiers/live-races/{lr.id}/runs')
            assert resp.status_code == 200
            assert resp.json() == []

    async def test_list_runs_missing_is_404(self, db, app):
        _, raw = await _admin_token()
        async with client_for(app, raw) as c:
            assert (await c.get('/api/async-qualifiers/live-races/9999/runs')).status_code == 404


class TestAuth:
    async def test_unauthenticated_is_401(self, db, app):
        async with client_for(app) as c:
            resp = await c.get('/api/async-qualifiers/live-races', params={'qualifier_id': 1})
            assert resp.status_code == 401

    async def test_read_only_token_forbidden_on_write(self, db, app):
        actor, _ = await _admin_token(username='setup')
        _, pool = await _make_qualifier_and_pool(actor)
        _, ro_raw = await create_user_token(
            username='ro', roles=[Role.QUALIFIER_ADMIN], read_only=True
        )
        async with client_for(app, ro_raw) as c:
            resp = await c.post(
                '/api/async-qualifiers/live-races',
                json={'pool_id': pool.id, 'match_title': 'Nope'},
            )
            assert resp.status_code == 403

    async def test_roleless_token_forbidden_on_read(self, db, app):
        """A plain user cannot administer qualifiers -> service PermissionError -> 403."""
        admin, _ = await _admin_token(username='owner')
        qualifier, _ = await _make_qualifier_and_pool(admin)
        _, raw = await create_user_token(username='plain')
        async with client_for(app, raw) as c:
            resp = await c.get(
                '/api/async-qualifiers/live-races', params={'qualifier_id': qualifier.id}
            )
            assert resp.status_code == 403

    async def test_roleless_token_forbidden_on_write(self, db, app):
        admin, _ = await _admin_token(username='owner2')
        _, pool = await _make_qualifier_and_pool(admin)
        _, raw = await create_user_token(username='plain2')
        async with client_for(app, raw) as c:
            resp = await c.post(
                '/api/async-qualifiers/live-races',
                json={'pool_id': pool.id, 'match_title': 'X'},
            )
            assert resp.status_code == 403


class TestWrites:
    async def test_create_live_race_success(self, db, app):
        actor, raw = await _admin_token()
        _, pool = await _make_qualifier_and_pool(actor)
        async with client_for(app, raw) as c:
            resp = await c.post(
                '/api/async-qualifiers/live-races',
                json={'pool_id': pool.id, 'match_title': 'Fresh Race'},
            )
            assert resp.status_code == 201
            body = resp.json()
            assert body['pool_id'] == pool.id
            assert body['match_title'] == 'Fresh Race'
            assert body['status'] == AsyncQualifierLiveRaceStatus.SCHEDULED.value
            assert body['racetime_slug'] is None

    async def test_create_blank_title_bad_request(self, db, app):
        actor, raw = await _admin_token()
        _, pool = await _make_qualifier_and_pool(actor)
        async with client_for(app, raw) as c:
            resp = await c.post(
                '/api/async-qualifiers/live-races',
                json={'pool_id': pool.id, 'match_title': '   '},
            )
            assert resp.status_code == 400

    async def test_create_missing_pool_not_found(self, db, app):
        _, raw = await _admin_token()
        async with client_for(app, raw) as c:
            resp = await c.post(
                '/api/async-qualifiers/live-races',
                json={'pool_id': 9999, 'match_title': 'Race'},
            )
            assert resp.status_code == 404

    async def test_open_room_missing_is_404(self, db, app):
        _, raw = await _admin_token()
        async with client_for(app, raw) as c:
            resp = await c.post('/api/async-qualifiers/live-races/9999/open-room')
            assert resp.status_code == 404

    async def test_open_room_without_bot_bad_request(self, db, app):
        """No authorized racetime bot -> service ValueError -> 400."""
        actor, raw = await _admin_token()
        _, pool = await _make_qualifier_and_pool(actor)
        lr = await AsyncQualifierLiveRaceService().create_live_race(
            actor, pool.id, match_title='RoomRace'
        )
        async with client_for(app, raw) as c:
            resp = await c.post(f'/api/async-qualifiers/live-races/{lr.id}/open-room')
            assert resp.status_code == 400

    async def test_cancel_live_race_success(self, db, app):
        actor, raw = await _admin_token()
        _, pool = await _make_qualifier_and_pool(actor)
        lr = await AsyncQualifierLiveRaceService().create_live_race(
            actor, pool.id, match_title='ToCancel'
        )
        async with client_for(app, raw) as c:
            resp = await c.delete(f'/api/async-qualifiers/live-races/{lr.id}')
            assert resp.status_code == 204
        assert await AsyncQualifierLiveRace.get_or_none(id=lr.id) is None

    async def test_cancel_missing_is_404(self, db, app):
        _, raw = await _admin_token()
        async with client_for(app, raw) as c:
            assert (await c.delete('/api/async-qualifiers/live-races/9999')).status_code == 404


class TestTenantIsolation:
    @pytest.fixture
    async def two_tenants(self, db, app):
        a = await Tenant.get(id=1)
        b = await Tenant.create(name='Beta', slug='beta')
        with tenant_scope(a.id):
            actor_a, _ = await _admin_token(username='a-admin')
            _, pool_a = await _make_qualifier_and_pool(actor_a, name='QA')
            lr_a = await AsyncQualifierLiveRaceService().create_live_race(
                actor_a, pool_a.id, match_title='A Race'
            )
        with tenant_scope(b.id):
            await enable_all_features(b.id)
            _, token_b = await _admin_token(username='b-admin')
            qualifier_b, _ = await _make_qualifier_and_pool(
                await User.get(username='b-admin'), name='QB'
            )
        return {'app': app, 'token_b': token_b, 'lr_a': lr_a, 'qualifier_b': qualifier_b}

    async def test_cannot_read_other_tenant_live_race(self, two_tenants):
        ctx = two_tenants
        async with client_for(ctx['app'], ctx['token_b']) as c:
            resp = await c.get(f"/api/async-qualifiers/live-races/{ctx['lr_a'].id}")
            assert resp.status_code == 404

    async def test_list_omits_other_tenant_rows(self, two_tenants):
        ctx = two_tenants
        async with client_for(ctx['app'], ctx['token_b']) as c:
            resp = await c.get(
                '/api/async-qualifiers/live-races',
                params={'qualifier_id': ctx['qualifier_b'].id},
            )
            assert resp.status_code == 200
            assert resp.json() == []
