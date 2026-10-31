"""Design B — cross-host login handoff: token mint/claim security tests.

Exercises the single-use, TTL-bounded, host-bound signed token that hands a
platform-host Discord login to a custom domain, plus the auth-layer helpers that
build the handoff URLs and sanitize the return path (open-redirect guard).
"""

import importlib

import pytest

from application.services import oauth_handoff_service as h


@pytest.fixture(autouse=True)
def _secret_and_reset(monkeypatch):
    monkeypatch.setenv('STORAGE_SECRET', 'x' * 40)
    h.reset()
    yield
    h.reset()


def _mint(host='foo.gg', next_path='/admin'):
    return h.mint(
        discord_id=123, username='bob', avatar='http://a/av.png',
        target_host=host, next_path=next_path,
    )


def test_mint_then_claim_roundtrips():
    token = _mint()
    payload = h.claim(token, 'foo.gg')
    assert payload is not None
    assert payload['discord_id'] == 123
    assert payload['username'] == 'bob'
    assert payload['next'] == '/admin'


def test_claim_is_single_use():
    token = _mint()
    assert h.claim(token, 'foo.gg') is not None
    assert h.claim(token, 'foo.gg') is None


def test_claim_rejects_wrong_host():
    token = _mint(host='foo.gg')
    assert h.claim(token, 'bar.gg') is None
    assert h.claim(token, 'foo.gg') is None


def test_claim_rejects_tampered_token():
    token = _mint()
    assert h.claim(token + 'x', 'foo.gg') is None


def test_claim_rejects_expired_via_store(monkeypatch):
    token = _mint()
    real = h.time.time()
    monkeypatch.setattr(h.time, 'time', lambda: real + h._TTL_SECONDS + 5)
    assert h.claim(token, 'foo.gg') is None


def test_mint_rejects_unnormalizable_host():
    assert h.mint(discord_id=1, username='x', avatar=None,
                  target_host='not a host', next_path='/') is None


def test_host_normalization_is_consistent():
    token = h.mint(discord_id=5, username='y', avatar=None,
                   target_host='https://Foo.GG/', next_path='/')
    assert h.claim(token, 'foo.gg') is not None


# Imported under MOCK_DISCORD; its live client rejects a null token.

@pytest.fixture
def auth(monkeypatch):
    monkeypatch.setenv('MOCK_DISCORD', 'true')
    monkeypatch.setenv('STORAGE_SECRET', 'x' * 40)
    return importlib.import_module('pages.auth')


@pytest.mark.parametrize('raw,expected', [
    ('/admin', '/admin'),
    ('/home/profile?tab=1', '/home/profile?tab=1'),
    ('//evil.com', '/'),
    ('/\\evil.com', '/'),
    ('/a\\b', '/'),
    ('/a\r\nSet-Cookie: x', '/'),
    ('/a b', '/'),
    ('https://evil.com', '/'),
    ('/login', '/'),
    ('', '/'),
    (None, '/'),
])
def test_safe_next(auth, raw, expected):
    assert auth._safe_next(raw) == expected


def test_bind_commit_is_deterministic_sha256(auth):
    import hashlib
    secret = 'a-login-secret'
    assert auth._bind_commit(secret) == hashlib.sha256(secret.encode()).hexdigest()
    assert auth._bind_commit('other') != auth._bind_commit(secret)


def test_mint_carries_bind_commit_through_claim():
    commit = 'deadbeef' * 8
    token = h.mint(discord_id=9, username='z', avatar=None,
                   target_host='foo.gg', next_path='/', bind_commit=commit)
    payload = h.claim(token, 'foo.gg')
    assert payload is not None
    assert payload['bind_commit'] == commit


def test_handoff_start_url(auth, monkeypatch):
    monkeypatch.setenv('PLATFORM_HOST', 'main.gg')
    url = auth._handoff_start_url('foo.gg', '/admin', 'abc123')
    assert url == 'https://main.gg/oauth/start?host=foo.gg&next=%2Fadmin&b=abc123'


def test_claim_url(auth):
    assert auth._claim_url('foo.gg', 'tok en') == 'https://foo.gg/session/claim?token=tok%20en'


def test_handoff_url_uses_http_for_localhost_dev(auth, monkeypatch):
    monkeypatch.setenv('PLATFORM_HOST', 'localhost:8000')
    assert auth._handoff_start_url('second.localhost:8000', '/').startswith('http://localhost:8000/')
    assert auth._claim_url('second.localhost:8000', 't').startswith('http://second.localhost:8000/')


def test_login_authorize_url_skips_consent_for_returning_users(auth, monkeypatch):
    monkeypatch.delenv('OAUTH_URL', raising=False)
    url = auth._oauth_url('st8', 'https://main.gg/oauth/callback')
    assert 'prompt=none' in url
    assert 'response_type=code' in url
    assert 'state=st8' in url
