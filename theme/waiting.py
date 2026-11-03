"""The shared waiting panel — a spinner with a fun fact under it.

Used wherever someone is sitting in front of a blank screen waiting on work the
app cannot make faster: the OAuth round trip, a deferred tab build, a report
re-render. The fact is the same easter egg the empty states and the 404 page
carry, on the theory that a wait with something to read is a shorter wait.

:func:`waiting_screen` is the full-page form, for a route whose entire job is
the wait (``/oauth/callback``, ``/session/claim``). Those pages render outside
:class:`~theme.base.BaseLayout`, so it paints the brand itself — the in-scope
community's palette, or the platform's when there is no tenant — and holds the
panel on screen for :data:`MIN_WAIT_SECONDS` so the fact is readable rather than
a flash between two redirects.
"""

import asyncio
import time

from nicegui import app, ui

from theme.assets import asset_url
from theme.browser_monitoring import install_browser_monitoring
from theme.chrome import apply_brand_palette, install_page_scripts
from theme.empty_state import fun_fact

__all__ = ['MIN_WAIT_SECONDS', 'WaitingScreen', 'waiting_panel', 'waiting_screen']

MIN_WAIT_SECONDS = 3.0

_PRELOAD_FONTS = (
    'atkinson-hyperlegible-latin-400-normal',
    'atkinson-hyperlegible-latin-700-normal',
)


def waiting_panel(message: str = 'Loading…', *, size: str = 'lg') -> ui.element:
    """Render a centered spinner, a message, and a fun fact.

    Returns the panel element so a caller that owns the wait can remove it when
    the work lands (``container.remove(panel)``).
    """
    with ui.column().classes('wiz-waiting items-center') as panel:
        ui.spinner(size=size)
        ui.label(message).classes('wiz-waiting-message')
        fun_fact()
    return panel


class WaitingScreen:
    """A rendered :func:`waiting_screen`, holding its own minimum display time."""

    def __init__(self, started: float) -> None:
        self._started = started

    async def hold(self) -> None:
        """Sleep out whatever is left of :data:`MIN_WAIT_SECONDS`."""
        remaining = MIN_WAIT_SECONDS - (time.monotonic() - self._started)
        if remaining > 0:
            await asyncio.sleep(remaining)

    async def navigate_to(self, target: str) -> None:
        """Leave for ``target`` once the panel has had its time on screen."""
        await self.hold()
        ui.navigate.to(target)


async def waiting_screen(message: str = 'Loading…', *, brand: str | None = None) -> WaitingScreen:
    """Paint a whole page as a centered, branded wait.

    Resolves the in-scope community's palette and name (the platform's shipped
    defaults when no tenant is in scope — the path-mode OAuth callback runs on
    the bare platform host), then centers the panel in the viewport. Call it
    before ``await client.connected()`` so the browser paints the spinner while
    the work behind it is still running.
    """
    from application.services import TenantService
    from application.services.tenant_theme_service import DEFAULT_THEME, TenantThemeService

    started = time.monotonic()
    colors = await TenantThemeService.get_current_theme() or DEFAULT_THEME
    if brand is None:
        brand = await TenantService.current_community_name() or 'Wizzrobe'

    ui.dark_mode(app.storage.user.get('dark_mode'))
    for font_file in _PRELOAD_FONTS:
        ui.add_head_html(
            f'<link rel="preload" href="/static/fonts/{font_file}.woff2" '
            'as="font" type="font/woff2" crossorigin>'
        )
    ui.add_head_html(f'<link rel="stylesheet" href="{asset_url("css/styles.css")}">')
    ui.add_head_html('<meta name="robots" content="noindex, nofollow">')
    apply_brand_palette(colors)
    install_page_scripts()
    install_browser_monitoring()

    # The default content wrapper's padding would push a 100dvh child into a
    # scrollbar; this page has nothing else in it, so drop it.
    ui.query('.nicegui-content').classes('p-0 gap-0')
    with ui.column().classes('wiz-waiting-screen items-center justify-center'):
        ui.label(brand).classes('wiz-waiting-brand')
        waiting_panel(message)
    return WaitingScreen(started)
