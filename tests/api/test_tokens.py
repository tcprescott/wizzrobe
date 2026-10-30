"""Tests for personal API tokens: the service, auth dependency, and the
self-management endpoints.

Uses the function-scoped in-memory ``db`` fixture from conftest.
"""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from application.services.api_token_service import ApiTokenService, _hash_token
from models import ApiToken, User
from tests.api_helpers import create_community_member


async def _user(discord_id: int = 1, username: str = 'u', is_active: bool = True) -> User:
    return await create_community_member(discord_id=discord_id, username=username, is_active=is_active)


def _client(app, raw_token: str | None = None) -> AsyncClient:
    headers = {'Authorization': f'Bearer {raw_token}'} if raw_token else {}
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test', headers=headers)


class TestApiTokenService:
    async def test_create_returns_plaintext_and_stores_only_hash(self, db):
        user = await _user()
        token, raw = await ApiTokenService().create_token(user, name='cli')
        assert raw.startswith('wizzrobe_pat_')
        # Only the hash is persisted; the plaintext never is.
        stored = await ApiToken.get(id=token.id)
        assert stored.token_hash == _hash_token(raw)
        assert raw not in stored.token_hash
        assert stored.token_prefix == raw[:17]

    async def test_authenticate_resolves_user_and_bumps_last_used(self, db):
        user = await _user()
        _, raw = await ApiTokenService().create_token(user, name='cli')
        result = await ApiTokenService().authenticate(raw)
        assert result is not None
        resolved_user, token = result
        assert resolved_user.id == user.id
        assert token.last_used_at is not None

    async def test_authenticate_rejects_unknown_token(self, db):
        assert await ApiTokenService().authenticate('wizzrobe_pat_nope') is None

    async def test_authenticate_rejects_revoked_token(self, db):
        user = await _user()
        token, raw = await ApiTokenService().create_token(user, name='cli')
        await ApiTokenService().revoke_token(user, token.id)
        assert await ApiTokenService().authenticate(raw) is None

    async def test_authenticate_rejects_expired_token(self, db):
        user = await _user()
        past = datetime.now(timezone.utc) - timedelta(days=1)
        # Create directly with a past expiry (service forbids past expiry on create).
        raw = 'wizzrobe_pat_expired'
        await ApiToken.create(
            user=user, name='old', token_hash=_hash_token(raw),
            token_prefix=raw[:17], expires_at=past,
        )
        assert await ApiTokenService().authenticate(raw) is None

    async def test_revoke_requires_ownership(self, db):
        owner = await _user(discord_id=1, username='owner')
        other = await _user(discord_id=2, username='other')
        token, _ = await ApiTokenService().create_token(owner, name='cli')
        with pytest.raises(PermissionError):
            await ApiTokenService().revoke_token(other, token.id)

    async def test_create_rejects_past_expiry(self, db):
        user = await _user()
        with pytest.raises(ValueError):
            await ApiTokenService().create_token(
                user, name='x', expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
            )


class TestTokenEndpoints:
    async def test_list_and_create(self, db, app):
        user = await _user()
        _, raw = await ApiTokenService().create_token(user, name='bootstrap')
        async with _client(app, raw) as c:
            created = await c.post('/api/tokens', json={'name': 'second'})
            assert created.status_code == 201
            body = created.json()
            assert body['token'].startswith('wizzrobe_pat_')
            assert body['name'] == 'second'

            listed = await c.get('/api/tokens')
            assert listed.status_code == 200
            names = {t['name'] for t in listed.json()}
            assert {'bootstrap', 'second'} <= names
            assert all('token' not in t for t in listed.json())

    async def test_a_token_cannot_mint_one_that_outlives_it(self, db, app):
        """A leaked short-lived token must not buy a permanent one."""
        user = await _user()
        soon = datetime.now(timezone.utc) + timedelta(hours=1)
        _, raw = await ApiTokenService().create_token(user, name='short', expires_at=soon)
        async with _client(app, raw) as c:
            forever = await c.post('/api/tokens', json={'name': 'forever'})
            later = await c.post('/api/tokens', json={
                'name': 'later',
                'expires_at': (soon + timedelta(days=30)).isoformat(),
            })
        assert forever.status_code == later.status_code == 201
        for resp in (forever, later):
            minted = datetime.fromisoformat(resp.json()['expires_at'])
            assert abs((minted - soon).total_seconds()) < 1

    async def test_revoke_endpoint(self, db, app):
        user = await _user()
        token, raw = await ApiTokenService().create_token(user, name='temp')
        async with _client(app, raw) as c:
            resp = await c.delete(f'/api/tokens/{token.id}')
            assert resp.status_code == 204
        assert await ApiTokenService().authenticate(raw) is None

    async def test_missing_token_is_401(self, db, app):
        async with _client(app) as c:
            assert (await c.get('/api/tokens')).status_code == 401

    async def test_inactive_user_is_403(self, db, app):
        user = await _user(is_active=False)
        _, raw = await ApiTokenService().create_token(user, name='cli')
        async with _client(app, raw) as c:
            assert (await c.get('/api/tokens')).status_code == 403

    async def test_read_only_token_cannot_write(self, db, app):
        user = await _user()
        _, raw = await ApiTokenService().create_token(user, name='ro', read_only=True)
        async with _client(app, raw) as c:
            assert (await c.get('/api/tokens')).status_code == 200
            blocked = await c.post('/api/tokens', json={'name': 'escalate'})
            assert blocked.status_code == 403
