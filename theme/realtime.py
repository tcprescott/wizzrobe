"""Presentation-side bridge for live updates pushed from the service layer.

Views call :func:`register_view` at build time with an async ``on_change`` handler.
Each registration captures the current NiceGUI ``Client`` and subscribes a callback
to a live channel — :mod:`application.events.match_live` by default, or
:mod:`application.events.check_in_live` for the check-in desk. When the channel
publishes, the callback schedules the handler inside the captured client's
context so UI mutations land in the right browser, with whatever arguments the
channel published. Subscriptions are cleaned up automatically when the client
disconnects.
"""

from types import ModuleType
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from nicegui import app, background_tasks, context
from nicegui.storage import request_contextvar

from application.events import match_live

OnChange = Callable[..., Awaitable[None]]

# Released on disconnect.
_client_tokens: Dict[str, List[Tuple[ModuleType, int]]] = {}
_disconnect_installed = False


def register_view(on_change: OnChange, channel: Optional[ModuleType] = None) -> None:
    """Subscribe ``on_change`` to ``channel`` for the current client.

    ``channel`` is a live-channel module with ``subscribe``/``unsubscribe``;
    it defaults to ``match_live``, whose handlers take
    ``(match_id, change_type)``. Must be called during page/view construction
    (a NiceGUI client context).
    """
    channel = channel if channel is not None else match_live
    client = context.client
    client_id = client.id

    async def _runner(*args: Any) -> None:
        # app.storage.user follows the request contextvar, which this task inherits from the publisher:
        # rebind it or every open board reads the publisher as its viewer.
        request = client.request
        session_id = (getattr(request, 'session', None) or {}).get('id')
        if session_id not in app.storage._users:  # pylint: disable=protected-access
            # No user storage (rotated at login, or the client outlived it): app.storage.user would raise.
            return
        token = request_contextvar.set(request)
        try:
            with client:
                await on_change(*args)
        finally:
            request_contextvar.reset(token)

    def _callback(*args: Any) -> None:
        background_tasks.create(_runner(*args))

    token = channel.subscribe(_callback)
    _client_tokens.setdefault(client_id, []).append((channel, token))
    _install_disconnect_cleanup()


def _install_disconnect_cleanup() -> None:
    global _disconnect_installed
    if _disconnect_installed:
        return
    _disconnect_installed = True
    app.on_disconnect(_on_disconnect)
    # A view registered after its socket dropped sees no disconnect, and its subscription would hold the page alive.
    app.on_delete(_on_disconnect)


def _on_disconnect(client) -> None:
    for channel, token in _client_tokens.pop(client.id, []):
        channel.unsubscribe(token)


def refresh_on_reconnect(refresh: Callable[[], Awaitable[Any]]) -> None:
    """Re-read the view when the socket comes back, rather than resume.

    This is only the *short* blip. An outage longer than ``reconnect_timeout``
    (3.0 s by default) means the server has already dropped the client, and the
    framework then does a full page reload (``try_reconnect`` →
    ``window.location.reload()``) which re-reads everything on its own. This
    handler covers the case where the same client survives and would otherwise
    keep showing pre-blip state — including a status the operator's own eaten
    click never changed, or a push from another device that arrived while the
    socket was down.
    """
    client = context.client
    # on_connect also fires on the initial handshake; refreshing there doubles every first render.
    seen_first = {'value': False}

    async def reread() -> None:
        with client:
            await refresh()

    def handle_connect() -> None:
        if not seen_first['value']:
            seen_first['value'] = True
            return
        background_tasks.create(reread())

    client.on_connect(handle_connect)
