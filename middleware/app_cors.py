"""CORS for third-party browser apps, on ``/api`` and the ``/oauth`` token routes.

A public app that runs in a browser (an SPA) can only call Wizzrobe if the
browser lets it, so origins come from live public apps' registered redirect
URIs (``OAuthAppService.browser_origins``), cached for a minute. Anything else
gets no CORS headers, which is what it got before.

Credentials stay off: the API takes a bearer token, never the session cookie,
so a cross-origin page can't ride a signed-in visitor's web session. The issuer
metadata is public, so any origin may read it.

Pure ASGI rather than ``BaseHTTPMiddleware`` so a preflight is answered before
routing (FastAPI would 405 an ``OPTIONS``) without buffering the response.
"""

import logging
import time
from typing import FrozenSet, List, Optional, Tuple

from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from api.oauth_server import METADATA_PATH
from application.services.oauth_app_service import OAuthAppService

logger = logging.getLogger(__name__)

_CACHE_SECONDS = 60.0
_TOKEN_PATHS = frozenset({'/oauth/token', '/oauth/revoke'})
_ALLOW_METHODS = 'GET, POST, PUT, PATCH, DELETE'
_ALLOW_HEADERS = 'Authorization, Content-Type, X-Wizzrobe-Community'
_EXPOSE_HEADERS = 'WWW-Authenticate, Retry-After'
_MAX_AGE = '600'

_origins: FrozenSet[str] = frozenset()
_loaded_at: Optional[float] = None


def reset_origin_cache() -> None:
    global _origins, _loaded_at
    _origins, _loaded_at = frozenset(), None


async def _allowed_origins() -> FrozenSet[str]:
    global _origins, _loaded_at
    now = time.monotonic()
    if _loaded_at is None or now - _loaded_at >= _CACHE_SECONDS:
        try:
            _origins = await OAuthAppService().browser_origins()
        except Exception:
            # Keep serving the last list; a DB blip shouldn't break every SPA.
            logger.warning('Could not refresh the app CORS origins', exc_info=True)
        _loaded_at = now
    return _origins


def _covered(path: str) -> bool:
    return path.startswith('/api/') or path in _TOKEN_PATHS or path == METADATA_PATH


async def _allows(path: str, origin: str) -> bool:
    return path == METADATA_PATH or origin in await _allowed_origins()


def _cors_headers(origin: str) -> List[Tuple[bytes, bytes]]:
    return [
        (b'access-control-allow-origin', origin.encode('latin-1')),
        (b'access-control-expose-headers', _EXPOSE_HEADERS.encode()),
    ]


class AppCorsMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http' or not _covered(scope['path']):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        origin = headers.get('origin')
        if not origin:
            await self.app(scope, receive, send)
            return
        allowed = await _allows(scope['path'], origin)
        if scope['method'] == 'OPTIONS' and 'access-control-request-method' in headers:
            await self._preflight(origin, allowed)(scope, receive, send)
            return
        if not allowed:
            await self.app(scope, receive, send)
            return

        async def send_with_cors(message: Message) -> None:
            if message['type'] == 'http.response.start':
                raw = [
                    (k, v) for k, v in message.get('headers', [])
                    if not k.lower().startswith(b'access-control-')
                ]
                raw.extend(_cors_headers(origin))
                raw.append((b'vary', b'Origin'))
                message = {**message, 'headers': raw}
            await send(message)

        await self.app(scope, receive, send_with_cors)

    @staticmethod
    def _preflight(origin: str, allowed: bool) -> Response:
        if not allowed:
            return PlainTextResponse('Origin not allowed', status_code=400, headers={'Vary': 'Origin'})
        response = Response(status_code=204, headers={
            'Access-Control-Allow-Methods': _ALLOW_METHODS,
            'Access-Control-Allow-Headers': _ALLOW_HEADERS,
            'Access-Control-Max-Age': _MAX_AGE,
            'Vary': 'Origin',
        })
        response.headers['Access-Control-Allow-Origin'] = origin
        return response
