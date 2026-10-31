"""Transport-level behaviour of the /mcp endpoint.

These guard the mechanics the whole design rests on — statelessness, the
bare-path route, the OAuth-only rule, the Bearer challenge. Each of them fails
silently and expensively if it regresses: a stateful server would cross-wire
identities, a Mount would 307 every client into a wall, and a missing challenge
would leave clients unable to start the flow they need.
"""

from application.services.api_token_service import MCP_TOKEN_PREFIX
from application.utils.build_info import build_info
from models import Role
from tests.api_helpers import create_user_token
from tests.mcp.conftest import auth, call_tool, create_oauth_token, mcp_session, rpc


class TestAuthentication:
    async def test_no_token_is_401_with_challenge(self, db):
        async with mcp_session() as client:
            resp = await rpc(client, None, 'tools/list')
        assert resp.status_code == 401
        assert 'resource_metadata=' in resp.headers['www-authenticate']

    async def test_garbage_token_is_401(self, db):
        async with mcp_session() as client:
            resp = await rpc(client, MCP_TOKEN_PREFIX + 'nope' * 8, 'tools/list')
        assert resp.status_code == 401
        assert 'resource_metadata=' in resp.headers['www-authenticate']

    async def test_personal_access_token_is_refused(self, db):
        """A REST PAT must not work here — and must be told where to go.

        401 rather than 403 on purpose: 403 would be accurate but leaves the
        client with nowhere to go, whereas 401 + challenge starts the flow.
        """
        _, pat = await create_user_token(roles=[Role.STAFF])
        async with mcp_session() as client:
            resp = await rpc(client, pat, 'tools/list')
        assert resp.status_code == 401
        assert 'resource_metadata=' in resp.headers['www-authenticate']

    async def test_expired_oauth_token_is_401(self, db):
        _, raw = await create_oauth_token(expired=True)
        async with mcp_session() as client:
            resp = await rpc(client, raw, 'tools/list')
        assert resp.status_code == 401

    async def test_deactivated_user_is_403(self, db):
        _, raw = await create_oauth_token(is_active=False)
        async with mcp_session() as client:
            resp = await rpc(client, raw, 'tools/list')
        assert resp.status_code == 403

    async def test_valid_oauth_token_is_accepted(self, db):
        _, raw = await create_oauth_token()
        async with mcp_session() as client:
            resp = await rpc(client, raw, 'tools/list')
        assert resp.status_code == 200


class TestTransport:
    async def test_tools_list_works_without_initialize(self, db):
        """Proves the server is stateless.

        A stateful server rejects anything before `initialize`. Statelessness is
        what makes per-request actor binding safe (mcpserver/asgi.py), so this
        doubles as a regression guard on that setting.
        """
        _, raw = await create_oauth_token()
        async with mcp_session() as client:
            resp = await rpc(client, raw, 'tools/list')
        assert resp.status_code == 200
        names = {t['name'] for t in resp.json()['result']['tools']}
        assert 'whoami' in names

    async def test_initialize_reports_server_identity(self, db):
        _, raw = await create_oauth_token()
        async with mcp_session() as client:
            resp = await rpc(client, raw, 'initialize', {
                'protocolVersion': '2025-06-18',
                'capabilities': {},
                'clientInfo': {'name': 'test', 'version': '0'},
            })
        assert resp.status_code == 200
        result = resp.json()['result']
        assert result['serverInfo']['name'] == 'Wizzrobe'
        assert 'whoami' in result['instructions']

    async def test_wrong_method_is_rejected(self, db):
        """PUT is not an MCP verb and must never reach the transport.

        The exact code differs by deployment and that is fine: this harness
        mounts only the MCP routes, so Starlette answers 405, while the full app
        has NiceGUI mounted at '/' which matches everything and answers 404.
        What matters is that it is refused, so assert that rather than a code
        the real server does not return.
        """
        _, raw = await create_oauth_token()
        async with mcp_session() as client:
            resp = await client.put('/mcp', headers=auth(raw), json={})
        assert resp.status_code in (404, 405)

    async def test_unacceptable_accept_header_is_406(self, db):
        """A client that cannot take JSON is refused.

        Not a *missing* header: httpx then sends ``Accept: */*``, which the
        transport rightly treats as accepting JSON.
        """
        _, raw = await create_oauth_token()
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'text/html',
            'Authorization': f'Bearer {raw}',
        }
        async with mcp_session() as client:
            resp = await client.post(
                '/mcp', headers=headers,
                json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'},
            )
        assert resp.status_code == 406

    async def test_both_bare_and_trailing_slash_paths_work(self, db):
        """The endpoint is a Route, not a Mount, and both spellings are served.

        Under a Mount, `/mcp` itself would 307 to `/mcp/` — and clients do not
        follow redirects by default, so every call would fail. Registering both
        paths also survives the full app, where NiceGUI's root mount swallows
        anything Starlette would otherwise have redirected.
        """
        _, raw = await create_oauth_token()
        body = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}
        async with mcp_session() as client:
            bare = await client.post('/mcp', headers=auth(raw), json=body)
            slashed = await client.post('/mcp/', headers=auth(raw), json=body)
        assert bare.status_code == 200
        assert slashed.status_code == 200

    async def test_unusual_host_header_is_accepted(self, db):
        """Regression: DNS-rebinding protection would 421 every production call.

        It auto-enables when the configured host is loopback, which is the
        default — so this fails closed in exactly the environment that running
        locally cannot reveal.
        """
        _, raw = await create_oauth_token()
        headers = dict(auth(raw))
        headers['Host'] = 'wizzrobe.example.com'
        async with mcp_session() as client:
            resp = await client.post(
                '/mcp', headers=headers,
                json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'},
            )
        assert resp.status_code == 200


class TestDiscovery:
    async def test_protected_resource_metadata(self, db):
        async with mcp_session() as client:
            for path in (
                '/.well-known/oauth-protected-resource/mcp',
                '/.well-known/oauth-protected-resource',
            ):
                resp = await client.get(path)
                assert resp.status_code == 200, path
                body = resp.json()
                assert body['resource'].endswith('/mcp')
                assert body['authorization_servers']
                assert body['bearer_methods_supported'] == ['header']

    async def test_metadata_needs_no_authentication(self, db):
        """Discovery must work *before* the client holds any credential."""
        async with mcp_session() as client:
            resp = await client.get('/.well-known/oauth-protected-resource')
        assert resp.status_code == 200


class TestRateLimit:
    async def test_over_limit_is_429_with_retry_after(self, db, monkeypatch):
        monkeypatch.setenv('API_RATE_LIMIT_PER_MIN', '3')
        _, raw = await create_oauth_token()
        async with mcp_session() as client:
            statuses = []
            for _ in range(5):
                resp = await rpc(client, raw, 'tools/list')
                statuses.append(resp.status_code)
            last = await rpc(client, raw, 'tools/list')
        assert 429 in statuses
        assert last.status_code == 429
        assert 'retry-after' in last.headers


class TestOrientationTools:
    async def test_whoami_reports_identity(self, db):
        user, raw = await create_oauth_token(username='alice', roles=[Role.STAFF])
        async with mcp_session() as client:
            is_error, payload = await call_tool(client, raw, 'whoami')
        assert not is_error, payload
        assert payload['user_id'] == user.id
        assert payload['username'] == 'alice'
        assert [t['slug'] for t in payload['tenants']] == ['default']
        assert Role.STAFF.value in payload['tenants'][0]['roles']

    async def test_server_version_reports_the_build(self, db, monkeypatch):
        build_info.cache_clear()
        monkeypatch.setenv('GIT_SHA', 'abcdef1234567890')
        monkeypatch.setenv('GIT_COMMIT_DATE', '2026-10-17T09:30:00-07:00')
        monkeypatch.delenv('APP_VERSION', raising=False)
        _, raw = await create_oauth_token()
        try:
            async with mcp_session() as client:
                is_error, payload = await call_tool(client, raw, 'server_version')
        finally:
            build_info.cache_clear()
        assert not is_error, payload
        assert payload['version'] == '2026.10.17'
        assert payload['commit_short'] == 'abcdef1'
        assert payload['source_url'].endswith('/tree/abcdef1234567890')

    async def test_list_tenants_hides_communities_without_a_role(self, db):
        """A role-less user must not learn which communities exist."""
        _, raw = await create_oauth_token()
        async with mcp_session() as client:
            is_error, payload = await call_tool(client, raw, 'list_tenants')
        assert not is_error, payload
        assert payload['result'] == []
