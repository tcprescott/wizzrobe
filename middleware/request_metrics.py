"""Count and time every HTTP request for Sentry Metrics.

Traces sample a tenth of requests; these metrics see all of them, labelled by
the route *template* (``/t/{slug}/admin``, never the filled-in path) so the
label set stays bounded. NiceGUI's own assets and socket.io transport under
``/_nicegui`` are skipped: they are most of the request volume, and a long-poll
that is open for 25s by design would swamp the duration figures.

Also keeps the in-flight request count that ``runtime_stats`` reports.
Pure ASGI rather than ``BaseHTTPMiddleware`` so the timing includes streaming
bodies and the route is read after the router has matched it.
"""

import time
from typing import Any, Dict

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from application.utils import runtime_stats, sentry_metrics

_SKIPPED_PREFIX = '/_nicegui'


def _route_label(scope: Scope) -> str:
    route = scope.get('route')
    path = getattr(route, 'path', None)
    return path or 'unmatched'


class RequestMetricsMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http' or scope.get('path', '').startswith(_SKIPPED_PREFIX):
            await self.app(scope, receive, send)
            return

        status: Dict[str, Any] = {'code': 500}

        async def send_wrapper(message: Message) -> None:
            if message['type'] == 'http.response.start':
                status['code'] = message['status']
            await send(message)

        started = time.perf_counter()
        runtime_stats.http_request_started()
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            runtime_stats.http_request_finished()
            if sentry_metrics.enabled():
                attrs = {
                    'route': _route_label(scope),
                    'method': scope.get('method', ''),
                    'status': f"{status['code'] // 100}xx",
                }
                sentry_metrics.count('http.requests', attributes=attrs)
                sentry_metrics.distribution(
                    'http.request_duration', (time.perf_counter() - started) * 1000,
                    unit='millisecond', attributes=attrs)
