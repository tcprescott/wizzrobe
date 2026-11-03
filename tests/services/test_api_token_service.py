"""Tests for ApiTokenService (unit, no DB)."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from application.services.api_token_service import TOKEN_PREFIX, ApiTokenService, _hash_token
from tests.factories import make_audit_double

UTC = timezone.utc


@pytest.fixture
def service():
    svc = object.__new__(ApiTokenService)
    svc.repository = MagicMock()
    svc.audit_service = make_audit_double()
    return svc


def make_token(**overrides):
    defaults = dict(
        id=1,
        user_id=42,
        name='My Token',
        token_hash='abc123',
        token_prefix='wizzrobe_pat_12345',
        read_only=False,
        revoked_at=None,
        expires_at=None,
        user=SimpleNamespace(id=42, preferred_name='Alice'),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestHashToken:
    def test_deterministic(self):
        assert _hash_token('abc') == _hash_token('abc')

    def test_different_inputs_different_hashes(self):
        assert _hash_token('a') != _hash_token('b')

    def test_returns_hex_string(self):
        h = _hash_token('test')
        int(h, 16)

    def test_length_is_64_chars(self):
        assert len(_hash_token('anything')) == 64


class TestCreateToken:
    async def test_raises_when_name_empty(self, service):
        actor = SimpleNamespace(id=1)
        with pytest.raises(ValueError, match='required'):
            await service.create_token(actor, name='')

    async def test_raises_when_name_whitespace(self, service):
        actor = SimpleNamespace(id=1)
        with pytest.raises(ValueError, match='required'):
            await service.create_token(actor, name='   ')

    async def test_raises_when_expiry_in_past(self, service):
        actor = SimpleNamespace(id=1)
        past = datetime.now(UTC) - timedelta(hours=1)
        with pytest.raises(ValueError, match='future'):
            await service.create_token(actor, name='My Token', expires_at=past)

    async def test_returns_token_and_raw_string(self, service):
        actor = SimpleNamespace(id=1)
        token = make_token()
        service.repository.create = AsyncMock(return_value=token)
        result_token, raw = await service.create_token(actor, name='My Token')
        assert result_token is token
        assert raw.startswith(TOKEN_PREFIX)

    async def test_raw_token_has_prefix(self, service):
        actor = SimpleNamespace(id=1)
        service.repository.create = AsyncMock(return_value=make_token())
        _, raw = await service.create_token(actor, name='Test')
        assert raw.startswith('wizzrobe_pat_')

    async def test_audits_on_creation(self, service):
        actor = SimpleNamespace(id=1)
        service.repository.create = AsyncMock(return_value=make_token())
        await service.create_token(actor, name='Test')
        service.audit_service.write_log.assert_awaited_once()


class TestListTokens:
    async def test_delegates_to_repository(self, service):
        actor = SimpleNamespace(id=1)
        service.repository.list_for_user = AsyncMock(return_value=[make_token()])
        result = await service.list_tokens(actor)
        assert len(result) == 1


class TestRevokeToken:
    async def test_raises_when_token_not_found(self, service):
        service.repository.get_by_id = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match='not found'):
            await service.revoke_token(actor=SimpleNamespace(id=1), token_id=99)

    async def test_raises_when_already_revoked(self, service):
        token = make_token(revoked_at=datetime.now(UTC))
        service.repository.get_by_id = AsyncMock(return_value=token)
        with pytest.raises(ValueError, match='not found'):
            await service.revoke_token(actor=SimpleNamespace(id=42), token_id=1)

    async def test_raises_when_not_owner(self, service):
        token = make_token(user_id=99)
        service.repository.get_by_id = AsyncMock(return_value=token)
        with pytest.raises(PermissionError, match='your own'):
            await service.revoke_token(actor=SimpleNamespace(id=1), token_id=1)

    async def test_revokes_and_audits(self, service):
        token = make_token(user_id=42)
        service.repository.get_by_id = AsyncMock(return_value=token)
        service.repository.revoke = AsyncMock()
        await service.revoke_token(actor=SimpleNamespace(id=42), token_id=1)
        service.repository.revoke.assert_awaited_once()
        service.audit_service.write_log.assert_awaited_once()


class TestAuthenticate:
    async def test_returns_none_for_empty_token(self, service):
        result = await service.authenticate('')
        assert result is None

    async def test_returns_none_when_hash_not_found(self, service):
        service.repository.get_by_hash = AsyncMock(return_value=None)
        result = await service.authenticate('wizzrobe_pat_sometoken')
        assert result is None

    async def test_returns_none_when_revoked(self, service):
        token = make_token(revoked_at=datetime.now(UTC))
        service.repository.get_by_hash = AsyncMock(return_value=token)
        result = await service.authenticate('wizzrobe_pat_sometoken')
        assert result is None

    async def test_returns_none_when_expired(self, service):
        token = make_token(expires_at=datetime.now(UTC) - timedelta(hours=1))
        service.repository.get_by_hash = AsyncMock(return_value=token)
        result = await service.authenticate('wizzrobe_pat_sometoken')
        assert result is None

    async def test_returns_user_and_token_on_success(self, service):
        user = SimpleNamespace(id=42, preferred_name='Alice')
        token = make_token(revoked_at=None, expires_at=None, user=user)
        service.repository.get_by_hash = AsyncMock(return_value=token)
        service.repository.touch_last_used = AsyncMock()
        result = await service.authenticate('wizzrobe_pat_sometoken')
        assert result is not None
        got_user, got_token = result
        assert got_user is user
        assert got_token is token
        service.repository.touch_last_used.assert_awaited_once()

    async def test_touches_last_used_on_success(self, service):
        user = SimpleNamespace(id=42)
        token = make_token(revoked_at=None, expires_at=None, user=user)
        service.repository.get_by_hash = AsyncMock(return_value=token)
        service.repository.touch_last_used = AsyncMock()
        await service.authenticate('wizzrobe_pat_valid')
        service.repository.touch_last_used.assert_awaited_once()
