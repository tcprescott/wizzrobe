"""Harness for the MCP server suite.

Mirrors ``tests/api_helpers.py`` but cannot share its shape, for two reasons
that both come from the transport's task-group:

* ``StreamableHTTPSessionManager.run()`` may be entered exactly once per
  instance, so the *app* cannot be ``functools.cache``d like ``build_api_app``.
  The tool catalogue behind it can, and is — see :func:`_catalogue`.
* It is an anyio cancel scope, which must be exited in the task that entered
  it. pytest-asyncio drives async-generator fixture setup and teardown on
  *different* tasks, so a ``yield``-style fixture raises "Attempted to exit
  cancel scope in a different task".

Hence :func:`mcp_session` — an async context manager used inside the test body,
so enter and exit share one task. It calls the real ``mcpserver.mount()`` rather
than re-deriving the routing, so the suite exercises production wiring.
"""

import functools
import hashlib
import random
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional, Tuple

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from mcp.server.mcpserver import MCPServer

import mcpserver
from application.services.api_token_service import MCP_TOKEN_PREFIX
from application.services.mcp_auth_service import granted_scope
from models import ApiToken, ApiTokenOrigin, McpOAuthClient, Role, User, UserRole

# The transport validates both: a POST without JSON content-type is rejected,
# and json_response=True still requires application/json in Accept.
MCP_HEADERS = {
    'Content-Type': 'application/json',
    'Accept': 'application/json, text/event-stream',
}


@functools.cache
def _catalogue() -> MCPServer:
    """The tool catalogue, built once per process.

    ``build_server()`` costs ~230ms, effectively all of it in the 77
    ``add_tool()`` calls: each derives a JSON schema from the tool's signature
    via Pydantic. The catalogue is immutable and identical every time, so
    rebuilding it for each of the ~106 sessions below cost ~24s per run — more
    than any query the MCP suite makes. Same reasoning, and same shape, as
    ``functools.cache`` on ``build_api_app()`` in ``tests/api_helpers.py``.
    """
    return mcpserver.server.build_server()


def _cached_build_server() -> MCPServer:
    """What ``mcpserver.mount()`` calls during the suite.

    The *server* is reusable; its transport is not — ``session_manager.run()``
    may be entered exactly once per instance. So the cached server hands back a
    freshly minted session manager each time, which is the only part of
    ``build_server()`` that was ever per-session to begin with.
    """
    mcp = _catalogue()
    mcpserver.server.start_transport(mcp)
    return mcp


# Patched at the module the production ``mount()`` resolves it from, so mount()
# itself still runs for real: routes, discovery and the OAuth endpoints are all
# registered by the code that registers them in production.
mcpserver.build_server = _cached_build_server


@asynccontextmanager
async def mcp_session():
    """Yield a client against a freshly mounted MCP server.

    A new transport per use, because its session manager is single-use. Use
    inside the test body, never as a fixture — see the module docstring.
    """
    app = FastAPI()
    mcpserver.mount(app)
    async with mcpserver.session_lifespan():
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url='http://test') as client:
            yield client


async def create_oauth_token(
    *,
    username: str = 'mcp-user',
    discord_id: Optional[int] = None,
    roles: Optional[Iterable[Role]] = None,
    is_active: bool = True,
    expired: bool = False,
    tenant_id: Optional[int] = None,
    write: bool = False,
    scope: Optional[str] = None,
) -> Tuple[User, str]:
    """Create a user and a platform-wide OAuth token for them.

    Mints the token directly rather than driving the authorization flow: these
    tests are about the tool surface, and ``test_mcp_oauth.py`` owns the flow.

    ``roles`` are granted in ``tenant_id`` (default: the ambient test tenant),
    because roles are per-tenant — granting them "globally" would silently test
    something the product cannot express.

    ``write`` mirrors the consent screen's box and defaults off, so a test that
    does not mention writing gets the connection most real users have.

    ``scope`` stores that scope string instead of the one ``write`` implies, for
    the tests about which areas a connection reaches.
    """
    if discord_id is None:
        discord_id = random.randint(1, 10 ** 12)
    user = await User.create(discord_id=discord_id, username=username, is_active=is_active)
    for role in roles or []:
        if role == Role.SUPER_ADMIN:
            await UserRole.create(user=user, role=role, tenant=None)
        elif tenant_id is not None:
            await UserRole.create(user=user, role=role, tenant_id=tenant_id)
        else:
            await UserRole.create(user=user, role=role)

    client, _ = await McpOAuthClient.get_or_create(
        client_id=f'test-client-{secrets.token_hex(4)}',
        defaults={
            'client_name': 'Test MCP Client',
            'redirect_uris': ['http://localhost/callback'],
            'grant_types': ['authorization_code', 'refresh_token'],
            'response_types': ['code'],
        },
    )
    raw = MCP_TOKEN_PREFIX + secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    await ApiToken.create(
        user=user, name='test-mcp', tenant_id=None, oauth_client=client,
        origin=ApiTokenOrigin.OAUTH.value, read_only=not write,
        scope=scope if scope is not None else granted_scope(allow_write=write),
        token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        token_prefix=raw[:17],
        expires_at=now - timedelta(minutes=1) if expired else now + timedelta(hours=1),
    )
    return user, raw


def auth(raw_token: Optional[str]) -> dict:
    headers = dict(MCP_HEADERS)
    if raw_token:
        headers['Authorization'] = f'Bearer {raw_token}'
    return headers


async def rpc(client: AsyncClient, raw_token: Optional[str], method: str, params: Any = None):
    """Send one JSON-RPC message and return the raw httpx response."""
    payload: dict = {'jsonrpc': '2.0', 'id': 1, 'method': method}
    if params is not None:
        payload['params'] = params
    return await client.post('/mcp', headers=auth(raw_token), json=payload)


async def list_tools(client: AsyncClient, raw_token: str) -> list:
    resp = await rpc(client, raw_token, 'tools/list')
    assert resp.status_code == 200, resp.text
    return resp.json()['result']['tools']


async def call_tool(
    client: AsyncClient, raw_token: str, name: str, /, **arguments
) -> Tuple[bool, Any]:
    """Call a tool. Returns ``(is_error, payload)``.

    On success the payload is ``structuredContent`` when the tool returned a
    model, else the text block. On error it is the error text, so a test can
    assert on the ``forbidden:`` / ``not_found:`` prefix.
    """
    resp = await rpc(
        client, raw_token, 'tools/call', {'name': name, 'arguments': arguments}
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()['result']
    text = result['content'][0]['text'] if result.get('content') else ''
    if result.get('isError'):
        return True, text
    return False, result.get('structuredContent', text)
