"""Browser-side usage capture: tab switches, clicks, dialogs and field use.

Page views only say which page someone loaded. On a tabbed hub (Admin, Home,
Volunteer) a tab switch rewrites the URL in place, and nothing a person clicks
inside a page reaches the server unless it ends in a saved change, so the
"which controls get used" question needs this layer. ``static/js/usage.js``
batches the browser's side and sends it here as a ``wiz_usage`` event.

Identity is read once at page build: ``app.storage.browser`` is only readable
while the page is being built, and the handler runs later on the websocket.
"""

import logging
import re
from typing import Any, Optional

from nicegui import app, background_tasks, context, ui

from application.services.telemetry_service import TelemetryService
from application.tenant_context import tenant_scope
from application.utils.environment import telemetry_enabled
from theme.assets import asset_url

logger = logging.getLogger(__name__)

_TENANT_PREFIX = re.compile(r'^/t/[^/]+')
_MAX_EVENTS_PER_PAGE = 5000


def normalize_path(path: str) -> str:
    """``/t/sgl26/admin/schedule`` → ``/admin/schedule``; numeric ids → ``{id}``.

    Mirrors the browser's normalisation in usage.js so tab rows and click rows
    for the same screen group together.
    """
    path = _TENANT_PREFIX.sub('', path or '') or '/'
    return re.sub(r'/\d+(?=/|$)', '/{id}', path)


def _identity() -> dict[str, Any]:
    try:
        session_id = app.storage.browser.get('id')
    except Exception:
        session_id = None
    try:
        discord_id = app.storage.user.get('discord_id')
        user_id = app.storage.user.get('user_id')
        username = app.storage.user.get('username')
    except Exception:
        discord_id = user_id = username = None
    return {'discord_id': discord_id, 'user_id': user_id, 'username': username, 'session_id': session_id}


async def _in_tenant(tenant_id: Optional[int], coro) -> None:
    with tenant_scope(tenant_id):
        await coro


_CLIENT_ATTR = '_wiz_usage_capture'


def install_usage_capture(tenant_id: Optional[int]) -> None:
    """Load usage.js and receive its batches for this page. Call at page build."""
    capture = UsageCapture(tenant_id)
    setattr(context.client, _CLIENT_ATTR, capture)
    capture.install()


def record_tab_view(base_path: Optional[str], slug: Optional[str]) -> None:
    """Record a tab view on a hub page; a no-op off a hub or before install."""
    capture = getattr(context.client, _CLIENT_ATTR, None)
    if capture is not None and base_path is not None and slug:
        capture.tab_viewed(base_path, slug)


class UsageCapture:
    """Per-page handle: installs the browser script and records tab switches."""

    def __init__(self, tenant_id: Optional[int]) -> None:
        self._tenant_id = tenant_id
        self._identity = _identity()
        self._received = 0
        self._enabled = telemetry_enabled()

    def install(self) -> None:
        if not self._enabled:
            return
        # In the head: markup from a lazy tab build never runs its <script>, and the listener must cover those tabs.
        ui.add_head_html(f'<script defer src="{asset_url("js/usage.js")}"></script>')
        ui.on('wiz_usage', self._on_batch)

    def _on_batch(self, e: Any) -> None:
        events = (e.args or {}).get('events') if isinstance(e.args, dict) else None
        if not isinstance(events, list) or self._received >= _MAX_EVENTS_PER_PAGE:
            return
        self._received += len(events)
        background_tasks.create(_in_tenant(
            self._tenant_id,
            TelemetryService().track_ui_batch(events, **self._identity),
        ))

    def tab_viewed(self, base_path: str, slug: str) -> None:
        if not self._enabled:
            return
        try:
            background_tasks.create(_in_tenant(
                self._tenant_id,
                TelemetryService().track_tab_view(
                    path=f'{normalize_path(base_path)}/{slug}', tab=slug, **self._identity,
                ),
            ))
        except Exception:
            logger.debug('tab view telemetry skipped', exc_info=True)
