"""Tests for the /api/webhooks endpoints (Staff only).

Uses the function-scoped in-memory ``db`` fixture from conftest.
"""


from models import Role
from tests.api_helpers import client_for, create_user_token


class TestWebhookApiAuth:
    async def test_unauthenticated_is_rejected(self, app, db):
        async with client_for(app) as client:
            resp = await client.get('/api/webhooks')
        assert resp.status_code == 401

    async def test_non_staff_forbidden(self, app, db):
        _, token = await create_user_token(roles=[])
        async with client_for(app, token) as client:
            resp = await client.get('/api/webhooks')
        assert resp.status_code == 403

    async def test_read_only_staff_token_cannot_create(self, app, db):
        _, token = await create_user_token(roles=[Role.STAFF], read_only=True)
        async with client_for(app, token) as client:
            resp = await client.post('/api/webhooks', json={
                'name': 'x', 'url': 'https://example.com', 'event_types': ['*'],
            })
        assert resp.status_code == 403


class TestWebhookApiCrud:
    async def test_create_returns_secret_once_then_hidden(self, app, db):
        _, token = await create_user_token(roles=[Role.STAFF])
        async with client_for(app, token) as client:
            create = await client.post('/api/webhooks', json={
                'name': 'overlay',
                'url': 'https://example.com/hook',
                'event_types': ['match.created'],
            })
            assert create.status_code == 201
            body = create.json()
            assert body['secret']  # returned once on create
            webhook_id = body['id']

            listing = await client.get('/api/webhooks')
            assert listing.status_code == 200
            row = listing.json()[0]
            assert 'secret' not in row  # never echoed by list/GET
            assert row['id'] == webhook_id

    async def test_create_validation_error_is_400(self, app, db):
        _, token = await create_user_token(roles=[Role.STAFF])
        async with client_for(app, token) as client:
            resp = await client.post('/api/webhooks', json={
                'name': 'x', 'url': 'https://example.com', 'event_types': ['nope'],
            })
        assert resp.status_code == 400

    async def test_update_and_delete(self, app, db):
        _, token = await create_user_token(roles=[Role.STAFF])
        async with client_for(app, token) as client:
            created = (await client.post('/api/webhooks', json={
                'name': 'x', 'url': 'https://example.com', 'event_types': ['*'],
            })).json()
            wid = created['id']

            upd = await client.put(f'/api/webhooks/{wid}', json={'is_active': False})
            assert upd.status_code == 200
            assert upd.json()['is_active'] is False

            deleted = await client.delete(f'/api/webhooks/{wid}')
            assert deleted.status_code == 204

            missing = await client.get(f'/api/webhooks/{wid}')
            assert missing.status_code == 404

    async def test_regenerate_secret_returns_new_secret(self, app, db):
        _, token = await create_user_token(roles=[Role.STAFF])
        async with client_for(app, token) as client:
            created = (await client.post('/api/webhooks', json={
                'name': 'x', 'url': 'https://example.com', 'event_types': ['*'],
            })).json()
            resp = await client.post(f"/api/webhooks/{created['id']}/regenerate-secret")
            assert resp.status_code == 200
            assert resp.json()['secret'] != created['secret']
