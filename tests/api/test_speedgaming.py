"""REST API tests for the SpeedGaming sync endpoints (api/routers/speedgaming.py).

Covers link CRUD, episode listing (payload omitted), on-demand sync, auth deps,
role gating (SYNC_ADMIN), and cross-tenant isolation.
"""

import pytest

from application.services.speedgaming_etl_service import SyncResult
from application.tenant_context import tenant_scope
from models import (
    Role,
    SpeedGamingEpisode,
    SpeedGamingEventLink,
    Tenant,
    Tournament,
)
from tests.api_helpers import build_api_app, client_for, create_user_token, enable_all_features


async def _staff_token(username='sg-staff'):
    return await create_user_token(username=username, roles=[Role.STAFF])


class TestListLinks:
    async def test_list_links_staff_ok(self, db, app):
        _, raw = await _staff_token()
        t = await Tournament.create(name='SG Cup')
        await SpeedGamingEventLink.create(tenant_id=1, tournament=t, event_slug='alttpr')
        async with client_for(app, raw) as c:
            resp = await c.get('/api/speedgaming/links')
            assert resp.status_code == 200
            body = resp.json()
            assert [link['event_slug'] for link in body] == ['alttpr']
            assert body[0]['tournament_id'] == t.id

    async def test_list_links_sync_admin_ok(self, db, app):
        """Resource extra: a SYNC_ADMIN token is authorized on GET /links."""
        _, raw = await create_user_token(username='sg-admin', roles=[Role.SYNC_ADMIN])
        async with client_for(app, raw) as c:
            resp = await c.get('/api/speedgaming/links')
            assert resp.status_code == 200

    async def test_list_links_unauthenticated(self, db, app):
        async with client_for(app) as c:
            resp = await c.get('/api/speedgaming/links')
            assert resp.status_code == 401

    async def test_list_links_role_less_forbidden(self, db, app):
        _, raw = await create_user_token(username='plain')
        async with client_for(app, raw) as c:
            resp = await c.get('/api/speedgaming/links')
            assert resp.status_code == 403


class TestEpisodes:
    async def test_list_episodes_omits_payload(self, db, app):
        _, raw = await _staff_token()
        t = await Tournament.create(name='SG Cup')
        link = await SpeedGamingEventLink.create(tenant_id=1, tournament=t, event_slug='alttpr')
        await SpeedGamingEpisode.create(
            tenant_id=1,
            event_link=link,
            sg_episode_id='ep-1',
            title='Round 1',
            payload={'secret': 'should-not-leak', 'a': 1},
            content_hash='deadbeef',
        )
        async with client_for(app, raw) as c:
            resp = await c.get(f'/api/speedgaming/links/{link.id}/episodes')
            assert resp.status_code == 200
            body = resp.json()
            assert len(body) == 1
            assert body[0]['sg_episode_id'] == 'ep-1'
            assert 'payload' not in body[0]
            assert body[0]['content_hash'] == 'deadbeef'

    async def test_list_episodes_missing_link_404(self, db, app):
        _, raw = await _staff_token()
        async with client_for(app, raw) as c:
            resp = await c.get('/api/speedgaming/links/999999/episodes')
            assert resp.status_code == 404


class TestCreateLink:
    async def test_create_link_success(self, db, app):
        _, raw = await _staff_token()
        t = await Tournament.create(name='SG Cup')
        async with client_for(app, raw) as c:
            resp = await c.post('/api/speedgaming/links', json={
                'tournament_id': t.id,
                'event_slug': 'alttpr',
                'sync_interval_minutes': 30,
                'lookahead_hours': 48,
            })
            assert resp.status_code == 201
            body = resp.json()
            assert body['event_slug'] == 'alttpr'
            assert body['sync_interval_minutes'] == 30
            assert body['active'] is True

    async def test_create_link_read_only_token_forbidden(self, db, app):
        _, raw = await create_user_token(username='ro', roles=[Role.STAFF], read_only=True)
        t = await Tournament.create(name='SG Cup')
        async with client_for(app, raw) as c:
            resp = await c.post('/api/speedgaming/links', json={
                'tournament_id': t.id, 'event_slug': 'alttpr',
            })
            assert resp.status_code == 403

    async def test_create_link_role_less_forbidden(self, db, app):
        _, raw = await create_user_token(username='plain')
        t = await Tournament.create(name='SG Cup')
        async with client_for(app, raw) as c:
            resp = await c.post('/api/speedgaming/links', json={
                'tournament_id': t.id, 'event_slug': 'alttpr',
            })
            assert resp.status_code == 403

    async def test_create_link_unknown_tournament_404(self, db, app):
        _, raw = await _staff_token()
        async with client_for(app, raw) as c:
            resp = await c.post('/api/speedgaming/links', json={
                'tournament_id': 999999, 'event_slug': 'alttpr',
            })
            assert resp.status_code == 404


class TestUpdateDeleteLink:
    async def test_update_link_success(self, db, app):
        _, raw = await _staff_token()
        t = await Tournament.create(name='SG Cup')
        link = await SpeedGamingEventLink.create(tenant_id=1, tournament=t, event_slug='alttpr')
        async with client_for(app, raw) as c:
            resp = await c.patch(f'/api/speedgaming/links/{link.id}', json={'active': False})
            assert resp.status_code == 200
            assert resp.json()['active'] is False

    async def test_update_link_missing_404(self, db, app):
        _, raw = await _staff_token()
        async with client_for(app, raw) as c:
            resp = await c.patch('/api/speedgaming/links/999999', json={'active': False})
            assert resp.status_code == 404

    async def test_delete_link_success(self, db, app):
        _, raw = await _staff_token()
        t = await Tournament.create(name='SG Cup')
        link = await SpeedGamingEventLink.create(tenant_id=1, tournament=t, event_slug='alttpr')
        async with client_for(app, raw) as c:
            resp = await c.delete(f'/api/speedgaming/links/{link.id}')
            assert resp.status_code == 204
        assert await SpeedGamingEventLink.get_or_none(id=link.id) is None

    async def test_delete_link_missing_404(self, db, app):
        _, raw = await _staff_token()
        async with client_for(app, raw) as c:
            resp = await c.delete('/api/speedgaming/links/999999')
            assert resp.status_code == 404


class TestSyncNow:
    async def test_sync_now_returns_tally(self, db, app, monkeypatch):
        _, raw = await _staff_token()
        t = await Tournament.create(name='SG Cup')
        link = await SpeedGamingEventLink.create(tenant_id=1, tournament=t, event_slug='alttpr')

        async def fake_sync(self, event_link, actor=None):
            return SyncResult(imported=2, unchanged=1, skipped=3)

        monkeypatch.setattr(
            'application.services.speedgaming_etl_service.SpeedGamingETLService.sync_event_link',
            fake_sync,
        )
        async with client_for(app, raw) as c:
            resp = await c.post(f'/api/speedgaming/links/{link.id}/sync')
            assert resp.status_code == 200
            body = resp.json()
            assert body == {
                'imported': 2, 'unchanged': 1, 'skipped': 3,
                'cancelled': 0, 'auto_finished': 0, 'errors': 0,
            }

    async def test_sync_now_missing_link_404(self, db, app):
        _, raw = await _staff_token()
        async with client_for(app, raw) as c:
            resp = await c.post('/api/speedgaming/links/999999/sync')
            assert resp.status_code == 404

    async def test_sync_now_read_only_forbidden(self, db, app):
        _, raw = await create_user_token(username='ro', roles=[Role.STAFF], read_only=True)
        t = await Tournament.create(name='SG Cup')
        link = await SpeedGamingEventLink.create(tenant_id=1, tournament=t, event_slug='alttpr')
        async with client_for(app, raw) as c:
            resp = await c.post(f'/api/speedgaming/links/{link.id}/sync')
            assert resp.status_code == 403


# --- Cross-tenant isolation ----------------------------------------------

class TestTenantIsolation:
    @pytest.fixture
    async def two_tenants(self, db):
        a = await Tenant.get(id=1)
        b = await Tenant.create(name='Beta', slug='beta')
        with tenant_scope(a.id):
            ta = await Tournament.create(name='A Cup')
            link_a = await SpeedGamingEventLink.create(tenant_id=a.id, tournament=ta, event_slug='a-event')
        with tenant_scope(b.id):
            await enable_all_features(b.id)
            _, token_b = await create_user_token(username='b-staff', roles=[Role.STAFF])
        return {'app': build_api_app(), 'token_b': token_b, 'link_a': link_a}

    async def test_list_omits_other_tenant(self, two_tenants):
        ctx = two_tenants
        async with client_for(ctx['app'], ctx['token_b']) as c:
            resp = await c.get('/api/speedgaming/links')
            assert resp.status_code == 200
            assert resp.json() == []

    async def test_episodes_cross_tenant_404(self, two_tenants):
        ctx = two_tenants
        async with client_for(ctx['app'], ctx['token_b']) as c:
            resp = await c.get(f"/api/speedgaming/links/{ctx['link_a'].id}/episodes")
            assert resp.status_code == 404

    async def test_patch_cross_tenant_404(self, two_tenants):
        ctx = two_tenants
        async with client_for(ctx['app'], ctx['token_b']) as c:
            resp = await c.patch(f"/api/speedgaming/links/{ctx['link_a'].id}", json={'active': False})
            assert resp.status_code == 404
