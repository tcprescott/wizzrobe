"""Never let a browser keep an error answer for NiceGUI's framework scripts.

NiceGUI stamps ``Cache-Control: public, max-age=31536000, immutable`` on every
response under ``/_nicegui/<version>/``, whatever its status. The path carries
the version, so a *good* answer is safe to keep for a year. A bad one is not:
a 404, a 5xx or a redirect caught during a restart is then kept for a year too,
and a reload reuses it instead of asking again. When the bad answer is Vue's
module, every page that browser opens stays blank (SGLMAN-1R, SGLMAN-1S).

So any ``/_nicegui/`` answer that isn't a success or a 304 goes out with
``no-store`` instead. Installed on the wrapping FastAPI app in ``frontend.init``,
outside NiceGUI's own cache middleware, so it has the last word.
"""

import re

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_FRAMEWORK_PATH = re.compile(r'^(?:/t/[a-z0-9][a-z0-9-]*)?/_nicegui/')

NO_STORE = b'no-store'


def is_framework_path(path: str) -> bool:
    return bool(_FRAMEWORK_PATH.match(path))


def is_keepable(status: int) -> bool:
    return 200 <= status < 300 or status == 304


class FrameworkCacheMiddleware:
    """Swap the year-long cache header on failed ``/_nicegui/`` answers for ``no-store``."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http' or not is_framework_path(scope.get('path', '')):
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message['type'] == 'http.response.start' and not is_keepable(message['status']):
                headers = [
                    (name, value)
                    for name, value in message.get('headers', [])
                    if name.lower() != b'cache-control'
                ]
                headers.append((b'cache-control', NO_STORE))
                message['headers'] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)
