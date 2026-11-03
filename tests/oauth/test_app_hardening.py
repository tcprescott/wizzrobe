"""Limits around third-party apps: one app's ceiling across all its users, and
which browser origins may call the API.

A popular app shares the single worker with everyone else, so its grants add
up to one bucket. CORS opens only to the pages public apps registered, never
with credentials, so a cross-origin page can't ride the web session.
"""

import functools
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from api import oauth_server
from application.services import OAuthAppService
from application.services.oauth_app_service import browser_origin
from application.utils import parked_requests
from middleware.app_cors import AppCorsMiddleware, reset_origin_cache
from models import OAuthClientType, Role, UserRole
from tests.api_helpers import create_community_member
from tests.conftest import DEFAULT_TEST_TENANT_ID
from tests.oauth.conftest import build_oauth_test_app, client, grant_tokens, make_app

pytestmark = pytest.mark.usefixtures('db')

TENANT = DEFAULT_TEST_TENANT_ID
SPA = 'https://spa.example.com'


@pytest.fixture(autouse=True)
def _fresh_origins():
    reset_origin_cache()
    yield
    reset_origin_cache()


@functools.cache
def _cors_app() -> AppCorsMiddleware:
    return AppCorsMiddleware(build_oauth_test_app())


def _browser(origin: str) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=_cors_app()), base_url='http://test', headers={'Origin': origin},
    )


async def _public_spa():
    owner = await create_community_member(username='dev')
    app, _ = await make_app(
        owner, client_type=OAuthClientType.PUBLIC,
        redirect_uris=(f'{SPA}/callback', 'http://127.0.0.1:5173/cb', 'com.example.app:/cb'),
    )
    return owner, app


async def test_an_apps_users_share_one_ceiling(monkeypatch):
    monkeypatch.setenv('API_APP_RATE_LIMIT_PER_MIN', '3')
    owner = await create_community_member(username='dev')
    alice = await create_community_member(username='alice')
    bob = await create_community_member(username='bob')
    app, secret = await make_app(owner)
    async with client() as http:
        a = (await grant_tokens(http, app, secret, alice, TENANT, ['profile']))['access_token']
        b = (await grant_tokens(http, app, secret, bob, TENANT, ['profile']))['access_token']
    async with client(a) as as_alice, client(b) as as_bob:
        assert (await as_alice.get('/api/users/me')).status_code == 200
        assert (await as_alice.get('/api/users/me')).status_code == 200
        assert (await as_bob.get('/api/users/me')).status_code == 200
        refused = await as_bob.get('/api/users/me')
    assert refused.status_code == 429
    assert 'This app is making too many requests' in refused.json()['detail']
    assert int(refused.headers['Retry-After']) >= 1


async def test_another_app_and_personal_tokens_keep_their_own_room(monkeypatch):
    monkeypatch.setenv('API_APP_RATE_LIMIT_PER_MIN', '1')
    owner = await create_community_member(username='dev')
    member = await create_community_member(username='player')
    busy, busy_secret = await make_app(owner)
    quiet, quiet_secret = await make_app(owner)
    async with client() as http:
        busy_token = (await grant_tokens(http, busy, busy_secret, member, TENANT, ['profile']))['access_token']
        quiet_token = (await grant_tokens(http, quiet, quiet_secret, member, TENANT, ['profile']))['access_token']
    async with client(busy_token) as as_busy:
        await as_busy.get('/api/users/me')
        assert (await as_busy.get('/api/users/me')).status_code == 429
    async with client(quiet_token) as as_quiet:
        assert (await as_quiet.get('/api/users/me')).status_code == 200


@pytest.mark.parametrize('uri, origin', [
    ('https://spa.example.com/callback', 'https://spa.example.com'),
    ('https://SPA.example.com:443/cb', 'https://spa.example.com'),
    ('https://spa.example.com:8443/cb', 'https://spa.example.com:8443'),
    ('http://127.0.0.1:5173/cb', 'http://127.0.0.1:5173'),
    ('http://[::1]:5173/cb', 'http://[::1]:5173'),
    ('http://example.com/cb', None),
    ('com.example.app:/cb', None),
])
def test_browser_origin(uri, origin):
    assert browser_origin(uri) == origin


async def test_only_live_public_apps_open_an_origin():
    owner, spa = await _public_spa()
    await make_app(owner, redirect_uris=('https://server.example.com/cb',))
    gone, _ = await make_app(
        owner, client_type=OAuthClientType.PUBLIC, redirect_uris=('https://gone.example.com/cb',),
    )
    admin = await create_community_member(username='root')
    await UserRole.create(user=admin, role=Role.SUPER_ADMIN, tenant=None)
    await OAuthAppService().disable_app(admin, gone.id, 'spam')

    assert await OAuthAppService().browser_origins() == {SPA, 'http://127.0.0.1:5173'}


async def test_a_registered_spa_can_preflight_and_read_the_api():
    _, spa = await _public_spa()
    async with _browser(SPA) as http:
        pre = await http.options('/api/users/me', headers={
            'Access-Control-Request-Method': 'GET', 'Access-Control-Request-Headers': 'authorization',
        })
        token_pre = await http.options('/oauth/token', headers={'Access-Control-Request-Method': 'POST'})
        anonymous = await http.get('/api/users/me')
    assert pre.status_code == 204
    assert pre.headers['access-control-allow-origin'] == SPA
    assert 'Authorization' in pre.headers['access-control-allow-headers']
    assert token_pre.status_code == 204
    assert anonymous.status_code == 401
    assert anonymous.headers['access-control-allow-origin'] == SPA
    for resp in (pre, token_pre, anonymous):
        assert 'access-control-allow-credentials' not in resp.headers


async def test_an_unregistered_origin_gets_no_cors():
    await _public_spa()
    async with _browser('https://evil.example.com') as http:
        pre = await http.options('/api/users/me', headers={'Access-Control-Request-Method': 'GET'})
        plain = await http.get('/api/users/me')
    assert pre.status_code == 400
    assert 'access-control-allow-origin' not in pre.headers
    assert 'access-control-allow-origin' not in plain.headers


async def test_consent_and_authorize_stay_same_origin():
    _, spa = await _public_spa()
    async with _browser(SPA) as http:
        pre = await http.options('/oauth/authorize', headers={'Access-Control-Request-Method': 'GET'})
    assert 'access-control-allow-origin' not in pre.headers


async def test_issuer_metadata_is_readable_from_anywhere():
    async with _browser('https://anyone.example.com') as http:
        resp = await http.get(oauth_server.METADATA_PATH)
    assert resp.status_code == 200
    assert resp.headers['access-control-allow-origin'] == 'https://anyone.example.com'


def test_parked_requests_have_a_ceiling(monkeypatch):
    monkeypatch.setattr(parked_requests, 'MAX_PARKED', 3)
    start = datetime.now(timezone.utc)
    store = {f'r{i}': SimpleNamespace(created_at=start + timedelta(seconds=i)) for i in range(3)}
    store['stale'] = SimpleNamespace(created_at=start - timedelta(hours=1))

    parked_requests.prune_parked(store, timedelta(minutes=15), room_for=1)

    assert set(store) == {'r1', 'r2'}
