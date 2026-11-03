"""The ASGI endpoint that fronts the MCP transport.

Rate limit → authenticate → bind the actor → hand off to the session manager →
release the binding. It is a raw ASGI callable rather than a FastAPI route
because the streamable-HTTP transport needs the untouched ``scope``/``receive``/
``send`` triple; a FastAPI route would consume the body.

**Why the binding reaches the tool.** ``handle_request`` starts the MCP server
task from *this* task, and ``asyncio`` copies the current context into a new
task — so anything set here is visible inside the tool. It works only because
the server runs stateless: a stateful session would spawn one long-lived task
per session that snapshots the *first* request's context and then serves every
later request under it, so a second token on the same session would execute with
the first token's identity. ``stateless_http=True`` in ``server.py`` is a
correctness requirement, not a scaling knob.
"""

import logging
from typing import Optional

from starlette.requests import Request

from application.services.api_token_service import ApiTokenService
from mcpserver.auth import McpActor, reset_actor, set_actor
from mcpserver.rate_limit import check_rate_limit
from mcpserver.wellknown import www_authenticate
from models import ApiTokenOrigin

logger = logging.getLogger(__name__)

_BEARER = 'bearer '


def _bearer_token(header: Optional[str]) -> Optional[str]:
    if not header or not header.lower().startswith(_BEARER):
        return None
    return header[len(_BEARER):].strip() or None


async def _json_error(
    send,
    status: int,
    error: str,
    description: str,
    extra_headers: Optional[dict] = None,
) -> None:
    """Emit an OAuth-shaped error body directly on the ASGI send channel.

    The shape matches the SDK's own transport errors so a client that parses
    them is not surprised by ours.
    """
    import json

    body = json.dumps({'error': error, 'error_description': description}).encode()
    headers = [(b'content-type', b'application/json')]
    for key, value in (extra_headers or {}).items():
        headers.append((key.lower().encode(), str(value).encode()))
    await send({'type': 'http.response.start', 'status': status, 'headers': headers})
    await send({'type': 'http.response.body', 'body': body})


class McpEndpoint:
    """Authenticating ASGI wrapper around the streamable-HTTP session manager."""

    def __init__(self, session_manager) -> None:
        self._session_manager = session_manager

    async def __call__(self, scope, receive, send) -> None:
        if scope['type'] != 'http':
            await self._session_manager.handle_request(scope, receive, send)
            return

        # Never read the body here, so the transport still receives the request intact.
        request = Request(scope, receive)

        retry_after = await check_rate_limit(request)
        if retry_after is not None:
            await _json_error(
                send, 429, 'rate_limited',
                'Too many requests. Please retry shortly.',
                {'Retry-After': retry_after},
            )
            return

        raw = _bearer_token(request.headers.get('authorization'))
        if raw is None:
            await _json_error(
                send, 401, 'invalid_token', 'Authorization required.',
                {'WWW-Authenticate': www_authenticate()},
            )
            return

        try:
            resolved = await ApiTokenService().resolve_actor(raw)
        except PermissionError as exc:
            await _json_error(send, 403, 'forbidden', str(exc))
            return
        if resolved is None:
            await _json_error(
                send, 401, 'invalid_token', 'Invalid or expired token.',
                {'WWW-Authenticate': www_authenticate()},
            )
            return

        user, token = resolved
        # Refuse PATs with the challenge, so a client holding a stale credential can rediscover the auth server.
        if token.origin != ApiTokenOrigin.OAUTH.value:
            logger.info('MCP rejected a non-OAuth token (id=%s)', token.id)
            await _json_error(
                send, 401, 'invalid_token',
                'This endpoint requires an OAuth token. Connect through your MCP client.',
                {'WWW-Authenticate': www_authenticate()},
            )
            return

        actor_token = set_actor(McpActor(user=user, token=token))
        try:
            await self._session_manager.handle_request(scope, receive, send)
        finally:
            reset_actor(actor_token)
