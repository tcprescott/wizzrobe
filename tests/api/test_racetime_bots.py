"""REST API tests for the racetime bot endpoints (api/routers/racetime_bots.py).

``RacetimeBot`` is a **global** (no-tenant) resource managed only by
``SUPER_ADMIN``. These tests cover the CRUD happy paths, secret-hiding
(``client_secret`` never serialized), the auth matrix (unauthenticated ->
401, non-super-admin -> 403, read-only token -> 403 on writes), and the
tenant grant sub-resource.
"""


from models import RacetimeBot, RacetimeBotTenant, Role, Tenant
from tests.api_helpers import client_for, create_user_token


async def _super_admin_token(username='super'):
    return await create_user_token(username=username, roles=[Role.SUPER_ADMIN])


async def _make_bot(category='alttp', name='ALttP Bot', **kwargs):
    return await RacetimeBot.create(
        category=category,
        client_id=kwargs.pop('client_id', 'cid'),
        client_secret=kwargs.pop('client_secret', 'topsecret'),
        name=name,
        **kwargs,
    )


class TestReads:
    async def test_list_bots_success(self, db, app):
        _, raw = await _super_admin_token()
        await _make_bot(category='alttp', name='ALttP')
        await _make_bot(category='smz3', name='SMZ3', is_active=False)
        async with client_for(app, raw) as c:
            resp = await c.get('/api/racetime-bots')
            assert resp.status_code == 200
            assert {b['category'] for b in resp.json()} == {'alttp', 'smz3'}

    async def test_list_bots_omits_client_secret(self, db, app):
        _, raw = await _super_admin_token()
        await _make_bot()
        async with client_for(app, raw) as c:
            resp = await c.get('/api/racetime-bots')
            assert resp.status_code == 200
            assert 'client_secret' not in resp.text
            assert 'client_secret' not in resp.json()[0]

    async def test_list_active_bots_only(self, db, app):
        _, raw = await _super_admin_token()
        await _make_bot(category='alttp', name='Active', is_active=True)
        await _make_bot(category='smz3', name='Inactive', is_active=False)
        async with client_for(app, raw) as c:
            resp = await c.get('/api/racetime-bots/active')
            assert resp.status_code == 200
            assert [b['category'] for b in resp.json()] == ['alttp']

    async def test_get_bot_success(self, db, app):
        _, raw = await _super_admin_token()
        bot = await _make_bot()
        async with client_for(app, raw) as c:
            resp = await c.get(f'/api/racetime-bots/{bot.id}')
            assert resp.status_code == 200
            body = resp.json()
            assert body['id'] == bot.id
            assert 'client_secret' not in body

    async def test_get_bot_not_found(self, db, app):
        _, raw = await _super_admin_token()
        async with client_for(app, raw) as c:
            resp = await c.get('/api/racetime-bots/9999')
            assert resp.status_code == 404


class TestAuth:
    async def test_list_unauthenticated_401(self, db, app):
        async with client_for(app) as c:
            resp = await c.get('/api/racetime-bots')
            assert resp.status_code == 401

    async def test_list_non_super_admin_403(self, db, app):
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            resp = await c.get('/api/racetime-bots')
            assert resp.status_code == 403

    async def test_list_role_less_403(self, db, app):
        _, raw = await create_user_token(username='plain')
        async with client_for(app, raw) as c:
            resp = await c.get('/api/racetime-bots')
            assert resp.status_code == 403

    async def test_create_read_only_token_403(self, db, app):
        _, raw = await create_user_token(username='super', roles=[Role.SUPER_ADMIN], read_only=True)
        async with client_for(app, raw) as c:
            resp = await c.post('/api/racetime-bots', json={
                'category': 'alttp', 'client_id': 'cid', 'client_secret': 's', 'name': 'Bot',
            })
            assert resp.status_code == 403

    async def test_create_non_super_admin_403(self, db, app):
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            resp = await c.post('/api/racetime-bots', json={
                'category': 'alttp', 'client_id': 'cid', 'client_secret': 's', 'name': 'Bot',
            })
            assert resp.status_code == 403


class TestWrites:
    async def test_create_bot_success_hides_secret(self, db, app):
        _, raw = await _super_admin_token()
        async with client_for(app, raw) as c:
            resp = await c.post('/api/racetime-bots', json={
                'category': 'alttp',
                'client_id': 'cid',
                'client_secret': 'topsecret',
                'name': 'ALttP Bot',
                'description': 'the bot',
            })
            assert resp.status_code == 201
            body = resp.json()
            assert body['category'] == 'alttp'
            assert body['description'] == 'the bot'
            assert 'client_secret' not in body
            assert 'topsecret' not in resp.text

    async def test_create_bot_duplicate_category_bad_request(self, db, app):
        _, raw = await _super_admin_token()
        await _make_bot(category='alttp')
        async with client_for(app, raw) as c:
            resp = await c.post('/api/racetime-bots', json={
                'category': 'alttp', 'client_id': 'cid', 'client_secret': 's', 'name': 'Dupe',
            })
            assert resp.status_code == 400

    async def test_update_bot_success(self, db, app):
        _, raw = await _super_admin_token()
        bot = await _make_bot(name='Old Name')
        async with client_for(app, raw) as c:
            resp = await c.patch(f'/api/racetime-bots/{bot.id}', json={'name': 'New Name'})
            assert resp.status_code == 200
            assert resp.json()['name'] == 'New Name'

    async def test_update_bot_not_found(self, db, app):
        _, raw = await _super_admin_token()
        async with client_for(app, raw) as c:
            resp = await c.patch('/api/racetime-bots/9999', json={'name': 'X'})
            assert resp.status_code == 404

    async def test_delete_bot_success(self, db, app):
        _, raw = await _super_admin_token()
        bot = await _make_bot()
        async with client_for(app, raw) as c:
            resp = await c.delete(f'/api/racetime-bots/{bot.id}')
            assert resp.status_code == 204
        assert await RacetimeBot.get_or_none(id=bot.id) is None

    async def test_delete_bot_not_found(self, db, app):
        _, raw = await _super_admin_token()
        async with client_for(app, raw) as c:
            resp = await c.delete('/api/racetime-bots/9999')
            assert resp.status_code == 404


class TestGrants:
    async def test_grant_then_list_then_revoke(self, db, app):
        _, raw = await _super_admin_token()
        bot = await _make_bot()
        tenant = await Tenant.create(name='Community', slug='community')
        async with client_for(app, raw) as c:
            grant_resp = await c.post(f'/api/racetime-bots/{bot.id}/grants', json={'tenant_id': tenant.id})
            assert grant_resp.status_code == 201
            grant_body = grant_resp.json()
            assert grant_body['bot_id'] == bot.id
            assert grant_body['tenant_id'] == tenant.id

            list_resp = await c.get(f'/api/racetime-bots/{bot.id}/grants')
            assert list_resp.status_code == 200
            assert [g['tenant_id'] for g in list_resp.json()] == [tenant.id]

            revoke_resp = await c.delete(f'/api/racetime-bots/{bot.id}/grants/{tenant.id}')
            assert revoke_resp.status_code == 204
        assert await RacetimeBotTenant.get_or_none(bot_id=bot.id, tenant_id=tenant.id) is None

    async def test_list_grants_bot_not_found(self, db, app):
        _, raw = await _super_admin_token()
        async with client_for(app, raw) as c:
            resp = await c.get('/api/racetime-bots/9999/grants')
            assert resp.status_code == 404

    async def test_grant_read_only_token_403(self, db, app):
        _, raw = await create_user_token(username='super', roles=[Role.SUPER_ADMIN], read_only=True)
        bot = await _make_bot()
        tenant = await Tenant.create(name='Community', slug='community')
        async with client_for(app, raw) as c:
            resp = await c.post(f'/api/racetime-bots/{bot.id}/grants', json={'tenant_id': tenant.id})
            assert resp.status_code == 403
