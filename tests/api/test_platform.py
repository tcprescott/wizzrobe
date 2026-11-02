"""REST API tests for the platform administration endpoints (api/routers/platform.py).

What matters: only a super admin reaches any of it (a community's Staff is an
outsider here), a read-only token can't write, a super admin reaches switched-off
communities, and each write lands through the owning service (validated and
audited) rather than around it.
"""

import logging

import pytest

from application.services.audit_service import AuditActions
from models import (
    AuditLog,
    FeatureFlag,
    FeatureFlagGroup,
    RacetimeBot,
    Role,
    Tenant,
    TenantFeatureFlag,
    TenantMembership,
    User,
    UserRole,
)
from tests.api_helpers import client_for, create_user_token

_LOGGER = 'wizzrobe.test.platform_api'


async def _super(read_only: bool = False):
    return await create_user_token(username='root', roles=[Role.SUPER_ADMIN], read_only=read_only)


@pytest.fixture(autouse=True)
def _restore_logger():
    target = logging.getLogger(_LOGGER)
    saved = target.level
    yield
    target.setLevel(saved)


class TestAccess:
    @pytest.mark.parametrize('path', [
        '/api/platform/communities',
        '/api/platform/users',
        '/api/platform/feature-groups',
        '/api/platform/log-levels',
        '/api/platform/apps',
        '/api/platform/browser-monitoring',
        '/api/platform/browser-monitoring/rules',
    ])
    async def test_staff_of_a_community_is_refused(self, db, app, path):
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            assert (await c.get(path)).status_code == 403

    async def test_unauthenticated_is_401(self, db, app):
        async with client_for(app) as c:
            assert (await c.get('/api/platform/communities')).status_code == 401

    async def test_read_only_super_admin_reads_but_cannot_write(self, db, app):
        _, raw = await _super(read_only=True)
        async with client_for(app, raw) as c:
            assert (await c.get('/api/platform/communities')).status_code == 200
            resp = await c.post('/api/platform/communities', json={'name': 'X', 'slug': 'x'})
            assert resp.status_code == 403
        assert not await Tenant.filter(slug='x').exists()


class TestCommunities:
    async def test_list_includes_switched_off_communities(self, db, app):
        await Tenant.create(name='Dormant', slug='dormant', is_active=False)
        _, raw = await _super()
        async with client_for(app, raw) as c:
            resp = await c.get('/api/platform/communities')
        assert resp.status_code == 200
        rows = {r['slug']: r for r in resp.json()}
        assert rows['dormant']['is_active'] is False
        assert 'setup_missing' in rows['dormant']

    async def test_create_then_grant_first_staff(self, db, app):
        _, raw = await _super()
        target = await User.create(discord_id=4242, username='organizer')
        async with client_for(app, raw) as c:
            created = await c.post('/api/platform/communities', json={'name': 'New One', 'slug': 'new-one'})
            assert created.status_code == 201, created.text
            body = created.json()
            assert body['staff'] == []
            assert any(not s['done'] and s['key'] == 'staff' for s in body['setup'])

            granted = await c.post(
                f"/api/platform/communities/{body['id']}/staff", json={'user_id': target.id},
            )
            assert granted.status_code == 201, granted.text
            assert [u['id'] for u in granted.json()] == [target.id]
        assert await TenantMembership.filter(user=target, tenant_id=body['id']).exists()
        assert await UserRole.filter(user=target, tenant_id=body['id'], role=Role.STAFF).exists()

    async def test_create_rejects_a_taken_slug(self, db, app):
        await Tenant.create(name='Taken', slug='taken')
        _, raw = await _super()
        async with client_for(app, raw) as c:
            resp = await c.post('/api/platform/communities', json={'name': 'Again', 'slug': 'taken'})
        assert resp.status_code == 400

    async def test_patch_switches_off_and_null_clears_domain(self, db, app):
        tenant = await Tenant.create(name='Edit Me', slug='edit-me', domain='edit.example.com')
        _, raw = await _super()
        async with client_for(app, raw) as c:
            resp = await c.patch(
                f'/api/platform/communities/{tenant.id}', json={'domain': None, 'is_active': False},
            )
        assert resp.status_code == 200, resp.text
        await tenant.refresh_from_db()
        assert tenant.domain is None
        assert tenant.is_active is False
        assert tenant.name == 'Edit Me'
        assert await AuditLog.filter(action=AuditActions.TENANT_UPDATED).exists()

    async def test_patch_refuses_a_null_name(self, db, app):
        tenant = await Tenant.create(name='Keep', slug='keep')
        _, raw = await _super()
        async with client_for(app, raw) as c:
            resp = await c.patch(f'/api/platform/communities/{tenant.id}', json={'name': None})
        assert resp.status_code == 400

    async def test_unknown_community_is_404(self, db, app):
        _, raw = await _super()
        async with client_for(app, raw) as c:
            assert (await c.get('/api/platform/communities/99999')).status_code == 404


class TestFeatures:
    async def test_override_forces_a_feature_off(self, db, app):
        tenant = await Tenant.create(name='Flags', slug='flags')
        _, raw = await _super()
        async with client_for(app, raw) as c:
            resp = await c.put(
                f'/api/platform/communities/{tenant.id}/features/{FeatureFlag.VOLUNTEERS.value}',
                json={'available': False},
            )
        assert resp.status_code == 200, resp.text
        row = next(f for f in resp.json() if f['flag'] == FeatureFlag.VOLUNTEERS.value)
        assert row['override'] is False and row['live'] is False
        stored = await TenantFeatureFlag.get(tenant_id=tenant.id, flag=FeatureFlag.VOLUNTEERS.value)
        assert stored.available is False

    async def test_unknown_feature_key_is_400(self, db, app):
        tenant = await Tenant.create(name='Flags', slug='flags')
        _, raw = await _super()
        async with client_for(app, raw) as c:
            resp = await c.put(
                f'/api/platform/communities/{tenant.id}/features/no-such-thing', json={'available': True},
            )
        assert resp.status_code == 400

    async def test_group_crud_and_assignment(self, db, app):
        tenant = await Tenant.create(name='Tiered', slug='tiered')
        _, raw = await _super()
        async with client_for(app, raw) as c:
            created = await c.post('/api/platform/feature-groups', json={
                'name': 'Pro', 'flags': [FeatureFlag.VOLUNTEERS.value, 'bogus'],
            })
            assert created.status_code == 201, created.text
            group = created.json()
            assert group['flags'] == [FeatureFlag.VOLUNTEERS.value]

            assigned = await c.put(
                f'/api/platform/communities/{tenant.id}/feature-group', json={'group_id': group['id']},
            )
            assert assigned.status_code == 200
            row = next(f for f in assigned.json() if f['flag'] == FeatureFlag.VOLUNTEERS.value)
            assert row['group_available'] is True

            renamed = await c.patch(f"/api/platform/feature-groups/{group['id']}", json={'name': 'Pro+'})
            assert renamed.json()['name'] == 'Pro+'
            assert renamed.json()['community_count'] == 1

            assert (await c.delete(f"/api/platform/feature-groups/{group['id']}")).status_code == 204
        await tenant.refresh_from_db()
        assert tenant.feature_group_id is None  # type: ignore[attr-defined]
        assert not await FeatureFlagGroup.filter(name='Pro+').exists()


class TestUsers:
    async def test_list_filters_and_reaches_every_community(self, db, app):
        other = await Tenant.create(name='Elsewhere', slug='elsewhere')
        outsider = await User.create(discord_id=777, username='faraway', display_name='Far Away')
        await TenantMembership.create(user=outsider, tenant=other)
        _, raw = await _super()
        async with client_for(app, raw) as c:
            resp = await c.get('/api/platform/users', params={'q': 'far'})
        assert resp.status_code == 200
        assert [u['id'] for u in resp.json()] == [outsider.id]
        assert resp.json()[0]['communities'] == ['Elsewhere']

    async def test_deactivate_and_reactivate(self, db, app):
        target = await User.create(discord_id=888, username='target')
        _, raw = await _super()
        async with client_for(app, raw) as c:
            off = await c.put(f'/api/platform/users/{target.id}/active', json={'is_active': False})
            assert off.status_code == 200 and off.json()['is_active'] is False
            on = await c.put(f'/api/platform/users/{target.id}/active', json={'is_active': True})
            assert on.json()['is_active'] is True
        assert await AuditLog.filter(action=AuditActions.USER_ACTIVATION_CHANGED).count() == 2

    async def test_cannot_deactivate_yourself(self, db, app):
        me, raw = await _super()
        async with client_for(app, raw) as c:
            resp = await c.put(f'/api/platform/users/{me.id}/active', json={'is_active': False})
        assert resp.status_code == 400


class TestLogLevels:
    async def test_set_then_reset(self, db, app):
        _, raw = await _super()
        async with client_for(app, raw) as c:
            set_resp = await c.put('/api/platform/log-levels', json={'logger': _LOGGER, 'level': 'debug'})
            assert set_resp.status_code == 200, set_resp.text
            row = next(r for r in set_resp.json() if r['logger'] == _LOGGER)
            assert row['override'] == 'DEBUG'
            assert logging.getLogger(_LOGGER).level == logging.DEBUG

            reset = await c.delete('/api/platform/log-levels', params={'logger': _LOGGER})
            assert reset.status_code == 200
            assert all(r['logger'] != _LOGGER for r in reset.json())

    async def test_unknown_level_is_400(self, db, app):
        _, raw = await _super()
        async with client_for(app, raw) as c:
            resp = await c.put('/api/platform/log-levels', json={'logger': _LOGGER, 'level': 'LOUD'})
        assert resp.status_code == 400


class TestBotRestart:
    async def test_restart_is_refused_while_the_runtime_is_off(self, db, app, monkeypatch):
        monkeypatch.delenv('RACETIME_BOT_ENABLED', raising=False)
        bot = await RacetimeBot.create(category='alttp', client_id='c', client_secret='s', name='Bot')
        _, raw = await _super()
        async with client_for(app, raw) as c:
            resp = await c.post(f'/api/racetime-bots/{bot.id}/restart')
        assert resp.status_code == 400
        assert 'RACETIME_BOT_ENABLED' in resp.text

    async def test_restart_reaches_the_manager(self, db, app, monkeypatch):
        calls = []

        class _Manager:
            async def restart(self, actor, bot_id):
                calls.append((actor.id, bot_id))

        monkeypatch.setattr('racetimebot.get_racetime_manager', lambda: _Manager())
        bot = await RacetimeBot.create(category='alttp', client_id='c', client_secret='s', name='Bot')
        me, raw = await _super()
        async with client_for(app, raw) as c:
            resp = await c.post(f'/api/racetime-bots/{bot.id}/restart')
        assert resp.status_code == 200, resp.text
        assert 'client_secret' not in resp.text
        assert calls == [(me.id, bot.id)]

    async def test_staff_cannot_restart(self, db, app):
        bot = await RacetimeBot.create(category='alttp', client_id='c', client_secret='s', name='Bot')
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            assert (await c.post(f'/api/racetime-bots/{bot.id}/restart')).status_code == 403


class TestApps:
    async def test_list_then_disable_and_enable(self, db, app):
        from models import OAuthApp, OAuthClientType
        user, raw = await _super()
        registered = await OAuthApp.create(
            owner=user, name='Seed Helper', client_type=OAuthClientType.PUBLIC, client_id='seed-helper',
        )
        async with client_for(app, raw) as c:
            rows = (await c.get('/api/platform/apps')).json()
            assert [r['name'] for r in rows] == ['Seed Helper']
            assert rows[0]['kind'] == 'device' and rows[0]['disabled'] is False
            assert (await c.post(f'/api/platform/apps/{registered.id}/disable', json={'reason': ''})).status_code == 400
            rows = (await c.post(f'/api/platform/apps/{registered.id}/disable', json={'reason': 'Phishing'})).json()
            assert rows[0]['disabled'] is True and rows[0]['disabled_reason'] == 'Phishing'
            rows = (await c.post(f'/api/platform/apps/{registered.id}/enable')).json()
            assert rows[0]['disabled'] is False and rows[0]['disabled_reason'] is None
        assert await AuditLog.filter(action=AuditActions.OAUTH_APP_DISABLED).exists()

    async def test_read_only_token_cannot_disable(self, db, app):
        from models import OAuthApp, OAuthClientType
        user, raw = await _super(read_only=True)
        registered = await OAuthApp.create(
            owner=user, name='X', client_type=OAuthClientType.PUBLIC, client_id='x-app',
        )
        async with client_for(app, raw) as c:
            resp = await c.post(f'/api/platform/apps/{registered.id}/disable', json={'reason': 'r'})
        assert resp.status_code == 403
        await registered.refresh_from_db()
        assert registered.disabled_at is None


class TestBrowserMonitoring:
    async def test_settings_round_trip(self, db, app):
        _, raw = await _super()
        async with client_for(app, raw) as c:
            body = (await c.get('/api/platform/browser-monitoring')).json()
            assert body['enabled'] is True and 0.5 in body['sample_rates']
            payload = {'enabled': True, 'replay_on_error': False, 'sample_rate': 0.5}
            body = (await c.put('/api/platform/browser-monitoring', json=payload)).json()
            assert body['replay_on_error'] is False and body['sample_rate'] == 0.5
            assert body['updated_by'] == 'root'
            bad = {**payload, 'sample_rate': 0.3}
            assert (await c.put('/api/platform/browser-monitoring', json=bad)).status_code == 400

    async def test_add_and_remove_rule(self, db, app):
        _, raw = await _super()
        async with client_for(app, raw) as c:
            resp = await c.post('/api/platform/browser-monitoring/rules', json={'pattern': 'ResizeObserver loop xyz'})
            assert resp.status_code == 201
            added = [r for r in resp.json() if not r['builtin']]
            assert [r['pattern'] for r in added] == ['ResizeObserver loop xyz']
            assert any(r['builtin'] for r in resp.json())
            short = await c.post('/api/platform/browser-monitoring/rules', json={'pattern': 'ab'})
            assert short.status_code == 400
            rows = (await c.delete(f"/api/platform/browser-monitoring/rules/{added[0]['id']}")).json()
            assert not [r for r in rows if not r['builtin']]
