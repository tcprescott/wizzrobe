"""Keep the anonymous, publicly-cacheable responses free of per-visitor state.

``pages/static_brackets.py`` serves the spectator bracket views with
``Cache-Control: public`` so a browser — or a CDN in front of the app — can hand
the same bytes to everyone. NiceGUI's session middleware, however, mints a
session cookie on *every* request that reaches the app, this one included. A
``public`` response carrying a ``Set-Cookie`` is a cache-poisoning hazard: a
shared cache that stored it would hand one visitor's session cookie to every
subsequent reader.

So this middleware strips ``Set-Cookie`` from exactly those responses. It has to
run **outside** the session middleware to do it — NiceGUI adds that one itself,
outermost of the app's own stack, and appends the cookie on the way out — which
is why it is installed on the wrapping FastAPI app in ``frontend.init`` rather
than alongside the tenant/auth middlewares.

Nothing is lost by dropping it: these pages never read the session (they render
identically for everyone, signed in or not), and any other page the visitor
opens mints the cookie as usual.

Pure ASGI, matching on the path only, so it costs a regex per request and
touches nothing else.
"""

import re

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_PUBLIC_CACHEABLE = re.compile(
    r'^(?:/t/[a-z0-9][a-z0-9-]*)?(?:/e/[a-z0-9][a-z0-9-]*)?/live'
    r'(?:/brackets/\d+|/tournament/\d+/brackets|/static)?/?$'
)


def is_public_cacheable_path(path: str) -> bool:
    return bool(_PUBLIC_CACHEABLE.match(path))


def _drop_cookie_vary(value: bytes) -> bytes:
    """``Vary`` without ``Cookie``, and each remaining field once.

    The session middleware adds ``Vary: Cookie``. These bodies do not depend on
    the cookie, and a CDN honouring it would key every visitor separately, which
    turns the shared cache this surface exists for into one entry per reader.
    """
    fields: list[str] = []
    for part in value.decode('latin-1').split(','):
        name = part.strip()
        if name and name.lower() != 'cookie' and name.lower() not in (f.lower() for f in fields):
            fields.append(name)
    return ', '.join(fields).encode('latin-1')


def _is_public(headers) -> bool:
    return any(
        name.lower() == b'cache-control' and b'public' in value.lower()
        for name, value in headers
    )


class PublicCacheMiddleware:
    """Strip ``Set-Cookie`` from the responses served as publicly cacheable."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http' or not is_public_cacheable_path(scope.get('path', '')):
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            # /live is also the Watch page, which needs its session cookie: share only responses marked public.
            if message['type'] == 'http.response.start' and _is_public(message.get('headers', [])):
                headers = []
                for name, value in message.get('headers', []):
                    if name.lower() == b'set-cookie':
                        continue
                    if name.lower() == b'vary':
                        value = _drop_cookie_vary(value)
                        if not value:
                            continue
                    headers.append((name, value))
                message['headers'] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)
