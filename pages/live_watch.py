"""``/live``: the Watch page, the one place an online viewer needs.

Every stage's stream in one player, what's on each stage now and next, what's
coming up on stream over the next few days, and every tournament's bracket. It
replaced the Event tab's On Air view, so whoever runs the Schedule board gets a
⋮ "Open in Schedule" on each match their board can show. No feature
flag. A live NiceGUI page by default; when staff set it static, or a rush of
arrivals trips it, or staff send signed-out visitors to the static page,
``middleware/watch_mode.py`` answers before this page is built (see
``pages/static_watch.py``).

The content comes from :func:`pages.watch_data.get_watch_view`, built once per
change for every reader. Each open page polls the shared version every couple of
seconds and repaints from the cached content when it moves, so a hundred viewers
cost one rebuild, not a hundred.
"""

import time

from nicegui import app, context, ui

from application.services import AuthService, TenantService, get_user_from_session
from application.tenant_context import require_tenant_id
from application.utils import live_audience
from middleware.auth import public_page
from pages.watch_data import get_watch_view, watch_version
from theme.base import BaseLayout
from theme.page_header import page_header
from theme.watch_live import LiveWatch, WatchReminders

#: How often an open page checks whether the content moved.
_POLL_SECONDS = 2.0
#: Repaint at least this often, for what no event announces (a match's time passing).
_REPAINT_SECONDS = 30.0


def create() -> None:
    @public_page('/live')
    async def live_watch() -> None:
        tenant_id = require_tenant_id()
        community = await TenantService.current_community_name() or 'Wizzrobe'
        ui.page_title(f'Watch — {community}')
        user = await get_user_from_session(app.storage.user)
        await BaseLayout(user=user).render()

        view = await get_watch_view(tenant_id)
        root_path = context.client.request.scope.get('root_path', '') or ''
        with ui.column().classes('page-container'):
            page_header('Watch', subtitle=f'Times in {view.timezone_label}' if view.timezone_label else None)
            live = LiveWatch(
                root_path, await AuthService.schedule_board_scope(user), await WatchReminders.load(user),
            )
            live.render(view)

        seen = {'version': watch_version(tenant_id), 'at': time.monotonic()}

        async def repaint_if_changed() -> None:
            version = watch_version(tenant_id)
            if version == seen['version'] and time.monotonic() - seen['at'] < _REPAINT_SECONDS:
                return
            seen['version'], seen['at'] = version, time.monotonic()
            live.render(await get_watch_view(tenant_id))

        ui.timer(_POLL_SECONDS, repaint_if_changed)

        client = context.client
        live_audience.viewer_joined(tenant_id, client.id)
        client.on_connect(lambda: live_audience.viewer_joined(tenant_id, client.id))
        client.on_disconnect(lambda: live_audience.viewer_left(tenant_id, client.id))
        # A tab that never connects (a link preview, a crawler) is deleted without a disconnect.
        client.on_delete(lambda: live_audience.viewer_left(tenant_id, client.id))

