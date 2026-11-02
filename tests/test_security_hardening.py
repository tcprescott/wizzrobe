"""Tests for the security-hardening changes: proxy-aware rate-limit keying
and the HTTP security-header middleware."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.rate_limit import _client_key
from middleware.security_headers import SecurityHeadersMiddleware


def _request(headers: dict, host: str = '10.0.0.1') -> SimpleNamespace:
    lower = {k.lower(): v for k, v in headers.items()}
    return SimpleNamespace(
        headers=SimpleNamespace(get=lower.get),
        client=SimpleNamespace(host=host),
    )


class TestRateLimitKey:
    def test_wellformed_token_keyed_by_hash(self):
        import hashlib

        from application.services.api_token_service import TOKEN_PREFIX

        token = TOKEN_PREFIX + 'x' * 32
        req = _request({'authorization': f'Bearer {token}'}, host='10.0.0.1')
        assert _client_key(req) == f'token:{hashlib.sha256(token.encode()).hexdigest()}'

    def test_garbage_token_falls_back_to_ip(self):
        req = _request({'authorization': 'Bearer abc'}, host='10.0.0.1')
        assert _client_key(req) == 'ip:10.0.0.1'

    def test_forwarded_for_ignored_by_default(self, monkeypatch):
        monkeypatch.delenv('TRUST_PROXY_FORWARDED_FOR', raising=False)
        req = _request({'x-forwarded-for': '9.9.9.9'}, host='10.0.0.1')
        assert _client_key(req) == 'ip:10.0.0.1'

    def test_forwarded_for_used_when_trusted(self, monkeypatch):
        monkeypatch.setenv('TRUST_PROXY_FORWARDED_FOR', 'true')
        req = _request({'x-forwarded-for': '9.9.9.9, 10.0.0.1'}, host='10.0.0.1')
        assert _client_key(req) == 'ip:9.9.9.9'

    def test_last_mode_ignores_a_forged_prefix(self, monkeypatch):
        monkeypatch.setenv('TRUST_PROXY_FORWARDED_FOR', 'last')
        req = _request({'x-forwarded-for': '6.6.6.6, 9.9.9.9'}, host='10.0.0.1')
        assert _client_key(req) == 'ip:9.9.9.9'


@pytest.fixture
def headers_app():
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get('/ping')
    async def ping():
        return {'ok': True}

    return app


class TestSecurityHeaders:
    async def test_headers_present(self, headers_app):
        async with AsyncClient(
            transport=ASGITransport(app=headers_app), base_url='http://test'
        ) as c:
            resp = await c.get('/ping')
        assert resp.headers['x-content-type-options'] == 'nosniff'
        assert resp.headers['x-frame-options'] == 'DENY'
        assert resp.headers['content-security-policy'] == "frame-ancestors 'none'"
        assert resp.headers['referrer-policy'] == 'strict-origin-when-cross-origin'
