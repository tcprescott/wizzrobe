"""Browser-side Sentry on every page: load the SDK, and talk to it from the server.

The settings come from :func:`application.utils.sentry.browser_sdk_config`
(``None`` when there's no DSN or a super-admin switched browser errors off, and
then nothing loads). ``static/js/sentry-browser.js`` does the browser's side and
reports the tab's replay id back here, where the server's own Sentry events
from this tab pick it up (see ``_enrich_event`` in ``application/utils/sentry.py``).
"""

import json
import logging
import re
from typing import Any

from nicegui import Client, app, ui

from application.utils import browser_monitoring
from application.utils.sentry import REPLAY_ID_KEY, browser_sdk_config
from theme.assets import asset_url

logger = logging.getLogger(__name__)

_REPLAY_ID = re.compile(r'^[0-9a-f]{32}$')


def _inline_json(value: Any) -> str:
    # Super-admins type these patterns; never let one close the <script>.
    return json.dumps(value).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')


def install_browser_monitoring() -> None:
    """Load the browser SDK for this page, when browser errors are on. Call at page build."""
    config = browser_sdk_config()
    if config is None:
        return
    # In the head: markup added from a lazy tab build never runs its <script>.
    ui.add_head_html(f'<script>window.__wizSentryConfig={_inline_json(config)};</script>')
    ui.add_head_html(f'<script defer src="{asset_url("vendor/sentry/bundle.replay.min.js")}"></script>')
    ui.add_head_html(f'<script defer src="{asset_url("js/sentry-browser.js")}"></script>')
    ui.on('wiz_sentry_replay', _remember_replay_id)


def _remember_replay_id(e: Any) -> None:
    replay_id = (e.args or {}).get('id') if isinstance(e.args, dict) else None
    if isinstance(replay_id, str) and _REPLAY_ID.match(replay_id):
        app.storage.client[REPLAY_ID_KEY] = replay_id


def flush_replay() -> None:
    """Upload the current tab's recording; its server-side error already names it."""
    if app.storage.client.get(REPLAY_ID_KEY):
        ui.run_javascript('window.__wizSentry && window.__wizSentry.flushReplay()')


def push_to_open_tabs() -> int:
    """Hand the current settings and rules to every open tab's SDK; returns how many tabs."""
    code = f'window.__wizSentry && window.__wizSentry.apply({_inline_json(browser_monitoring.live_config(browser_monitoring.current()))})'
    pushed = 0
    for client in list(Client.instances.values()):
        if not client.has_socket_connection:
            continue
        try:
            client.run_javascript(code)
            pushed += 1
        except Exception:
            logger.debug('Could not push browser monitoring settings to a tab', exc_info=True)
    return pushed
