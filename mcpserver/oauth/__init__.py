"""OAuth 2.1 authorization server for the MCP surface.

Same origin as the resource server, which is the simple case: a client that
finds ``/.well-known/oauth-protected-resource`` is pointed back here, and
discovers ``/.well-known/oauth-authorization-server`` on the same host.

The routes come from the SDK's ``create_auth_routes`` — authorize, token,
register (RFC 7591), revoke, and the RFC 8414 metadata — so PKCE and the grant
mechanics are the SDK's well-tested implementation rather than ours. They are
appended to the *outer* app so they land at the origin root; mounted under the
MCPServer sub-app they would sit beneath ``/mcp`` where no client looks.
"""

import logging

from fastapi import FastAPI, HTTPException
from mcp.server.auth.routes import create_auth_routes
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from pydantic import AnyHttpUrl
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from api.rate_limit import rate_limit
from application.utils.environment import get_base_url
from mcpserver.oauth.provider import WizzrobeOAuthProvider
from mcpserver.wellknown import SCOPES_SUPPORTED

logger = logging.getLogger(__name__)

_provider: WizzrobeOAuthProvider | None = None


def _rate_limited(app: ASGIApp) -> ASGIApp:
    """Put a route's ASGI app behind the shared per-minute limiter.

    Wraps at the ASGI layer, not the handler layer, because these routes are not
    all the same shape: the SDK hands ``/authorize`` a plain request handler and
    wraps the other three in ``CORSMiddleware``, which is an ASGI app. ``Route``
    normalizes both into ``Route.app``, so that is the one place a single wrapper
    fits.

    These routes hang off the outer app, not the ``/api`` router or the ``/mcp``
    endpoint, so neither of those limiters covers them — and they are the only
    unauthenticated writes the app exposes. ``/register`` in particular inserts a
    row per call and is open by design (RFC 7591 dynamic registration is what
    lets a client configure itself from a URL), so without a limit anyone can
    grow ``mcp_oauth_clients`` as fast as they can POST. ``/token`` and
    ``/revoke`` get the same bucket, which also caps guessing at a code or a
    refresh token.

    The same limiter as the REST API and ``/mcp``, so a client is throttled on
    one budget across all three rather than three independent ones.

    ``OPTIONS`` passes through: the SDK wraps most of these in CORS middleware,
    and a 429 on a preflight would break a browser client (MCP Inspector) for
    reasons it cannot report.
    """

    async def guarded(scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get('method') != 'OPTIONS':
            try:
                await rate_limit(Request(scope, receive))
            except HTTPException as exc:
                response = JSONResponse(
                    {
                        'error': 'rate_limited',
                        'error_description': str(exc.detail),
                    },
                    status_code=exc.status_code,
                    headers=dict(exc.headers or {}),
                )
                await response(scope, receive, send)
                return
        await app(scope, receive, send)

    return guarded


def get_provider() -> WizzrobeOAuthProvider:
    global _provider
    if _provider is None:
        _provider = WizzrobeOAuthProvider()
    return _provider


def register_oauth_routes(app: FastAPI) -> None:
    """Append the authorization-server routes to the outer app."""
    base = get_base_url()
    routes = create_auth_routes(
        provider=get_provider(),
        issuer_url=AnyHttpUrl(base),
        service_documentation_url=AnyHttpUrl(f'{base}/api/docs'),
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=SCOPES_SUPPORTED,
            default_scopes=SCOPES_SUPPORTED,
        ),
        revocation_options=RevocationOptions(enabled=True),
    )
    for route in routes:
        route.app = _rate_limited(route.app)
    app.router.routes.extend(routes)
    logger.info('MCP OAuth routes registered (issuer=%s)', base)
