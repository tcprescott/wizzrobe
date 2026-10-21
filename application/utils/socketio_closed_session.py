"""Answering a socket.io request for a session engine.io has just closed.

engine.io checks ``sid in self.sockets`` and only then calls ``_get_socket``,
which raises ``KeyError('Session is disconnected')`` when the socket is still
in the table but already marked closed. That window opens whenever a session
closes while the browser still has a long-poll in flight: a restart shutting
every socket down, a ping timeout on a phone that went to sleep. The client
should get the 400 engine.io gives any other stale session (it reconnects on
its own), but the ``KeyError`` escapes ``handle_request`` instead, so the
request ends as a 500 and an unhandled error in Sentry (SGLMAN-1P).

``_get_socket`` drops the closed socket before raising and nothing has been
sent yet, so answering the 400 here leaves engine.io in the state its own
"Invalid session" path would have.

Pure stdlib and engine.io-agnostic (it wraps whatever server it is handed), so
it can be tested without starting NiceGUI.
"""

import functools
import json
import logging
from typing import Any

_DISCONNECTED = 'Session is disconnected'

logger = logging.getLogger('wizzrobe.socketio')


def _is_closed_session(exc: KeyError) -> bool:
    return exc.args == (_DISCONNECTED,)


async def _bad_request(scope: dict, send: Any) -> None:
    if scope.get('type') == 'websocket':
        await send({'type': 'websocket.close', 'reason': _DISCONNECTED})
        return
    body = json.dumps(_DISCONNECTED).encode('utf-8')
    await send({
        'type': 'http.response.start',
        'status': 400,
        'headers': [(b'content-type', b'text/plain'), (b'content-length', str(len(body)).encode())],
    })
    await send({'type': 'http.response.body', 'body': body})


def install(eio_server: Any) -> None:
    """Wrap ``eio_server.handle_request`` so a closed session answers 400. Idempotent."""
    original = eio_server.handle_request
    if getattr(original, '_wizzrobe_closed_session', False):
        return

    @functools.wraps(original)
    async def handle_request(scope: dict, receive: Any, send: Any) -> Any:
        try:
            return await original(scope, receive, send)
        except KeyError as exc:
            if not _is_closed_session(exc):
                raise
            logger.info('socket.io %s for a closed session; answered 400', scope.get('method') or scope.get('type'))
            await _bad_request(scope, send)
            return None

    handle_request._wizzrobe_closed_session = True  # type: ignore[attr-defined]
    eio_server.handle_request = handle_request
