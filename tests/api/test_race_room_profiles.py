"""REST API tests for race room profile endpoints (api/routers/race_room_profiles.py).

Race room profiles are tenant-scoped and gated in-service by
``AuthService.can_manage_sync`` (STAFF / SUPER_ADMIN / SYNC_ADMIN). Reads use the
any-token dep and re-gate in the service; writes additionally reject read-only
tokens at the HTTP layer.
"""

import pytest

from application.tenant_context import tenant_scope
from models import RaceRoomProfile, Role, Tenant
from tests.api_helpers import client_for, create_user_token, enable_all_features


async def _sync_admin_token(username='sync'):
    return await create_user_token(username=username, roles=[Role.SYNC_ADMIN])


class TestReads:
    async def test_list_profiles_sync_admin_ok(self, db, app):
        _, raw = await _sync_admin_token()
        await RaceRoomProfile.create(name='House Rules')
        await RaceRoomProfile.create(name='Async Rules')
        async with client_for(app, raw) as c:
            resp = await c.get('/api/race-room-profiles')
            assert resp.status_code == 200
            assert {p['name'] for p in resp.json()} == {'House Rules', 'Async Rules'}

    async def test_list_profiles_staff_ok(self, db, app):
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
        await RaceRoomProfile.create(name='House Rules')
        async with client_for(app, raw) as c:
            resp = await c.get('/api/race-room-profiles')
            assert resp.status_code == 200
            assert [p['name'] for p in resp.json()] == ['House Rules']

    async def test_list_profiles_unauthenticated(self, db, app):
        async with client_for(app) as c:
            resp = await c.get('/api/race-room-profiles')
            assert resp.status_code == 401

    async def test_list_profiles_role_less_forbidden(self, db, app):
        _, raw = await create_user_token(username='plain')
        async with client_for(app, raw) as c:
            resp = await c.get('/api/race-room-profiles')
            assert resp.status_code == 403

    async def test_get_profile_success(self, db, app):
        _, raw = await _sync_admin_token()
        profile = await RaceRoomProfile.create(name='House Rules', goal='beat the game')
        async with client_for(app, raw) as c:
            resp = await c.get(f'/api/race-room-profiles/{profile.id}')
            assert resp.status_code == 200
            body = resp.json()
            assert body['name'] == 'House Rules'
            assert body['goal'] == 'beat the game'

    async def test_get_profile_not_found(self, db, app):
        _, raw = await _sync_admin_token()
        async with client_for(app, raw) as c:
            resp = await c.get('/api/race-room-profiles/9999')
            assert resp.status_code == 404

    async def test_selectable_no_management_gate(self, db, app):
        """/selectable has no in-service gate: a role-less token still gets 200."""
        _, raw = await create_user_token(username='plain')
        await RaceRoomProfile.create(name='Pickable')
        async with client_for(app, raw) as c:
            resp = await c.get('/api/race-room-profiles/selectable')
            assert resp.status_code == 200
            assert [p['name'] for p in resp.json()] == ['Pickable']


class TestWrites:
    async def test_create_profile_success(self, db, app):
        _, raw = await _sync_admin_token()
        async with client_for(app, raw) as c:
            resp = await c.post(
                '/api/race-room-profiles',
                json={'name': 'New Profile', 'goal': 'any%', 'unlisted': True, 'time_limit': 12},
            )
            assert resp.status_code == 201
            body = resp.json()
            assert body['name'] == 'New Profile'
            assert body['goal'] == 'any%'
            assert body['unlisted'] is True
            assert body['time_limit'] == 12
            assert body['auto_start'] is True

    async def test_create_profile_read_only_forbidden(self, db, app):
        _, raw = await create_user_token(username='sync', roles=[Role.SYNC_ADMIN], read_only=True)
        async with client_for(app, raw) as c:
            resp = await c.post('/api/race-room-profiles', json={'name': 'Nope'})
            assert resp.status_code == 403

    async def test_create_profile_role_less_forbidden(self, db, app):
        _, raw = await create_user_token(username='plain')
        async with client_for(app, raw) as c:
            resp = await c.post('/api/race-room-profiles', json={'name': 'Nope'})
            assert resp.status_code == 403

    async def test_create_profile_duplicate_name_bad_request(self, db, app):
        _, raw = await _sync_admin_token()
        await RaceRoomProfile.create(name='Dupe')
        async with client_for(app, raw) as c:
            resp = await c.post('/api/race-room-profiles', json={'name': 'Dupe'})
            assert resp.status_code == 400

    async def test_create_profile_blank_name_bad_request(self, db, app):
        _, raw = await _sync_admin_token()
        async with client_for(app, raw) as c:
            resp = await c.post('/api/race-room-profiles', json={'name': '   '})
            assert resp.status_code == 400

    async def test_create_profile_negative_int_bad_request(self, db, app):
        _, raw = await _sync_admin_token()
        async with client_for(app, raw) as c:
            resp = await c.post('/api/race-room-profiles', json={'name': 'Bad', 'time_limit': -1})
            assert resp.status_code == 400

    async def test_update_profile_success(self, db, app):
        _, raw = await _sync_admin_token()
        profile = await RaceRoomProfile.create(name='Old', auto_start=True)
        async with client_for(app, raw) as c:
            resp = await c.patch(
                f'/api/race-room-profiles/{profile.id}',
                json={'name': 'Renamed', 'auto_start': False},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body['name'] == 'Renamed'
            assert body['auto_start'] is False

    async def test_update_profile_not_found(self, db, app):
        _, raw = await _sync_admin_token()
        async with client_for(app, raw) as c:
            resp = await c.patch('/api/race-room-profiles/9999', json={'name': 'X'})
            assert resp.status_code == 404

    async def test_update_profile_read_only_forbidden(self, db, app):
        _, raw = await create_user_token(username='ro', roles=[Role.SYNC_ADMIN], read_only=True)
        profile = await RaceRoomProfile.create(name='Locked')
        async with client_for(app, raw) as c:
            resp = await c.patch(f'/api/race-room-profiles/{profile.id}', json={'name': 'Y'})
            assert resp.status_code == 403

    async def test_delete_profile_success(self, db, app):
        _, raw = await _sync_admin_token()
        profile = await RaceRoomProfile.create(name='Temp')
        async with client_for(app, raw) as c:
            resp = await c.delete(f'/api/race-room-profiles/{profile.id}')
            assert resp.status_code == 204
        assert await RaceRoomProfile.get_or_none(id=profile.id) is None

    async def test_delete_profile_not_found(self, db, app):
        _, raw = await _sync_admin_token()
        async with client_for(app, raw) as c:
            resp = await c.delete('/api/race-room-profiles/9999')
            assert resp.status_code == 404


# --- Cross-tenant isolation -----------------------------------------------

class TestTenantIsolation:
    @pytest.fixture
    async def two_tenants(self, db, app):
        a = await Tenant.get(id=1)
        b = await Tenant.create(name='Beta', slug='beta')
        with tenant_scope(a.id):
            profile_a = await RaceRoomProfile.create(name='A Profile')
        with tenant_scope(b.id):
            await enable_all_features(b.id)
            _, token_b = await create_user_token(username='b-sync', roles=[Role.SYNC_ADMIN])
            profile_b = await RaceRoomProfile.create(name='B Profile')
        return {'app': app, 'token_b': token_b, 'profile_a': profile_a, 'profile_b': profile_b}

    async def test_list_omits_other_tenant(self, two_tenants):
        ctx = two_tenants
        async with client_for(ctx['app'], ctx['token_b']) as c:
            resp = await c.get('/api/race-room-profiles')
            assert resp.status_code == 200
            assert [p['id'] for p in resp.json()] == [ctx['profile_b'].id]

    async def test_get_other_tenant_profile_404(self, two_tenants):
        ctx = two_tenants
        async with client_for(ctx['app'], ctx['token_b']) as c:
            resp = await c.get(f"/api/race-room-profiles/{ctx['profile_a'].id}")
            assert resp.status_code == 404
            ok = await c.get(f"/api/race-room-profiles/{ctx['profile_b'].id}")
            assert ok.status_code == 200
