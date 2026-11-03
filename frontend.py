"""Frontend initialization for the Wizzrobe FastAPI application.

Sets up NiceGUI pages and integrates them with the FastAPI app.
"""

import logging
import os

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from nicegui import app, core, ui

from application.utils import page_build_timeout, socketio_closed_session
from application.utils.environment import (
    get_platform_host,
    is_production,
    session_storage_url,
    validate_security_config,
    validate_session_storage,
)
from application.utils.timer_teardown import install_log_filter, is_timer_teardown_race
from middleware.auth import AuthMiddleware
from middleware.error_handlers import register_error_handlers
from middleware.framework_cache import FrameworkCacheMiddleware
from middleware.public_cache import PublicCacheMiddleware
from middleware.tenant import TenantMiddleware, TransportPrefixMiddleware
from middleware.watch_mode import WatchModeMiddleware
from pages import (
    about,
    admin,
    auth,
    brackets,
    challonge_oauth,
    changelog,
    check_in,
    community_events,
    discord_email_oauth,
    email_unsubscribe,
    equipment,
    equipment_labels,
    event_info,
    facts,
    home,
    item_tracker,
    live_watch,
    mcp_consent,
    my_messages,
    oauth_consent,
    password_login,
    platform,
    qualifiers,
    racetime_oauth,
    room_seeds,
    startgg_bracket,
    startgg_oauth,
    static_brackets,
    static_watch,
    stream_overlay,
    survey,
    twitch_oauth,
    volunteer,
    watch_data,
)

# Keep aliased and on its own line: the bare name would shadow the help builtin.
from pages import help as help_pages
from pages._oauth_link import register_link_handoff_pages
from theme.a11y import install_tooltip_labels
from theme.assets import cache_control
from theme.browser_monitoring import flush_replay
from theme.layout_probe import install_layout_probe
from theme.notify import install_toast_position

_ui_logger = logging.getLogger('wizzrobe.ui')

# Order matters: the last-added runs outermost, so requests go session -> transport-strip -> tenant -> auth -> watch-mode.
app.add_middleware(WatchModeMiddleware)
app.add_middleware(AuthMiddleware)
app.add_middleware(TenantMiddleware)
app.add_middleware(TransportPrefixMiddleware)


@app.on_exception
def _handle_unhandled_ui_exception(exc: Exception) -> None:
    """Backstop for event handlers that miss the ValueError->ui.notify wrap:
    log the traceback (reaches Sentry) and, when a UI context is available,
    surface a generic notice so a failure is visible-but-generic rather than
    silent. Never raises itself."""
    if is_timer_teardown_race(exc):
        _ui_logger.debug('Timer outlived its page; stopping quietly.')
        return
    _ui_logger.exception('Unhandled exception in a UI event handler', exc_info=exc)
    try:
        ui.notify('Something went wrong. Please try again.', color='negative')
    except Exception:
        pass
    try:
        flush_replay()
    except Exception:
        pass


class VersionedStaticFiles(StaticFiles):
    """StaticFiles that states its cache policy instead of leaving it to chance.

    Development sends ``no-store`` so an edit shows up on reload. Production
    answers on the version stamp :func:`theme.assets.asset_url` puts in the URL:

    * stamped (``?v=<mtime>``) — ``immutable``, cached for a year. The URL
      changes when the file does, so a client never has to be told to re-fetch.
    * unstamped — ``must-revalidate`` against the ETag every time.

    The policy itself lives in :func:`theme.assets.cache_control`, beside the
    function that writes the stamp.
    """

    def __init__(self, *args, **kwargs):
        self.is_dev = os.environ.get('ENVIRONMENT', 'development') == 'development'
        super().__init__(*args, **kwargs)

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await super().__call__(scope, receive, send)
            return

        policy = cache_control(scope.get('query_string', b''), is_dev=self.is_dev)

        async def send_wrapper(message):
            if message['type'] == 'http.response.start':
                headers = [
                    h for h in message.get('headers', [])
                    if h[0].lower() not in (b'cache-control', b'pragma', b'expires')
                ]
                headers.append((b'cache-control', policy))
                message['headers'] = headers
            await send(message)

        await super().__call__(scope, receive, send_wrapper)


ROBOTS_TXT = 'User-agent: *\nDisallow: /\n'


SESSION_MAX_AGE_SECONDS = 90 * 24 * 60 * 60


def _session_middleware_kwargs() -> dict:
    """The signed session cookie that carries the login.

    ``Secure`` in production so it never travels over plaintext HTTP; left off
    in development so local http:// keeps working.
    """
    return {
        'https_only': is_production(),
        'same_site': 'lax',
        'max_age': SESSION_MAX_AGE_SECONDS,
    }


def _register_root_routes(fastapi_app: FastAPI) -> None:
    """Register the site-root routes that cannot live under ``/static``.

    Extracted from :func:`init` so it can be exercised on a bare app.
    """

    @fastapi_app.get('/robots.txt', include_in_schema=False)
    async def _robots() -> PlainTextResponse:
        return PlainTextResponse(
            ROBOTS_TXT, headers={'Cache-Control': 'public, max-age=3600'},
        )

    # Must be served from the root: a worker under /static/ can't control '/'.
    @fastapi_app.get('/sw.js', include_in_schema=False)
    async def _service_worker() -> FileResponse:
        # Keep no-cache: a stale service worker pins old behaviour for everything it serves.
        return FileResponse(
            'static/sw.js',
            media_type='text/javascript',
            headers={'Cache-Control': 'no-cache'},
        )

    async def _brand_logo_response(tenant_id: int, sha256: str, fmt: str, request: Request) -> Response:
        from application.services import TenantLogoService
        from application.services.tenant_logo_service import stored_format

        etag = f'"{sha256}"'
        headers = {
            'Cache-Control': 'public, max-age=31536000, immutable',
            'ETag': etag,
            'X-Content-Type-Options': 'nosniff',
            'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; sandbox",
        }
        content = await TenantLogoService.get_logo_bytes(tenant_id, sha256)
        if content is None or stored_format(content) != fmt:
            return Response(status_code=404, headers={'Cache-Control': 'no-store'})
        if request.headers.get('if-none-match') == etag:
            return Response(status_code=304, headers=headers)
        media = 'image/svg+xml' if fmt == 'svg' else 'image/webp'
        return Response(content, media_type=media, headers=headers)

    @fastapi_app.get('/brand-logo/{tenant_id}/{sha256}.webp', include_in_schema=False)
    async def _brand_logo(tenant_id: int, sha256: str, request: Request) -> Response:
        return await _brand_logo_response(tenant_id, sha256, 'webp', request)

    @fastapi_app.get('/brand-logo/{tenant_id}/{sha256}-{side}.png', include_in_schema=False)
    async def _brand_logo_icon(tenant_id: int, sha256: str, side: int, request: Request) -> Response:
        from application.services import TenantLogoService

        etag = f'"{sha256}-{side}"'
        headers = {
            'Cache-Control': 'public, max-age=31536000, immutable',
            'ETag': etag,
            'X-Content-Type-Options': 'nosniff',
        }
        content = await TenantLogoService.get_icon_png(tenant_id, sha256, side)
        if content is None:
            return Response(status_code=404, headers={'Cache-Control': 'no-store'})
        if request.headers.get('if-none-match') == etag:
            return Response(status_code=304, headers=headers)
        return Response(content, media_type='image/png', headers=headers)

    @fastapi_app.get('/brand-logo/{tenant_id}/{sha256}.svg', include_in_schema=False)
    async def _brand_logo_svg(tenant_id: int, sha256: str, request: Request) -> Response:
        return await _brand_logo_response(tenant_id, sha256, 'svg', request)


def init(fastapi_app: FastAPI) -> None:
    """
    Initialize the frontend by registering NiceGUI pages and attaching them to the FastAPI app.

    Args:
        fastapi_app (FastAPI): The FastAPI application instance to integrate with NiceGUI.
    """
    validate_security_config()
    validate_session_storage()
    install_log_filter()
    socketio_closed_session.install(core.sio.eio)
    # Before any page is registered: the default is read when ui.page is built.
    page_build_timeout.install()
    install_tooltip_labels()
    install_toast_position()
    install_layout_probe()

    redis_url = session_storage_url()
    if redis_url:
        logging.getLogger('wizzrobe').info(
            'Session storage: Redis (sessions survive restarts).',
        )
    else:
        logging.getLogger('wizzrobe').warning(
            'Session storage: local file (.nicegui). Every restart signs all users out — '
            'set NICEGUI_REDIS_URL to persist sessions across deploys.'
            if is_production() else
            'Session storage: local file (.nicegui).',
        )

    logging.getLogger('wizzrobe').info(
        'Tenant routing: PLATFORM_HOST=%s (path mode /t/<slug> + host mode for custom domains)',
        get_platform_host(),
    )

    fastapi_app.mount("/static", VersionedStaticFiles(directory="static"), name="static")

    _register_root_routes(fastapi_app)
    email_unsubscribe.create(fastapi_app)

    auth.create()
    password_login.create()
    challonge_oauth.create()
    twitch_oauth.create()
    startgg_oauth.create()
    racetime_oauth.create()
    discord_email_oauth.create()
    mcp_consent.create()
    oauth_consent.create()
    # Registered after the providers so their handoff configs are in the registry.
    register_link_handoff_pages()
    admin.create()
    home.create()
    volunteer.create()
    # Register before equipment.create() so this static route beats /equipment/{asset_id}.
    equipment_labels.create()
    equipment.create()
    check_in.create()
    brackets.create()
    startgg_bracket.create()
    room_seeds.create()
    stream_overlay.create()
    # Must register before ui.run_with so these views sit behind the same tenant middleware.
    static_brackets.create()
    static_watch.create()
    watch_data.subscribe()
    live_watch.create()
    platform.create()
    qualifiers.create()
    help_pages.create()
    event_info.create()
    community_events.create()
    my_messages.create()
    survey.create()
    item_tracker.create()
    facts.create()
    changelog.create()
    about.create()
    ui.run_with(
        fastapi_app,
        title='Wizzrobe',
        viewport='width=device-width, initial-scale=1, viewport-fit=cover',
        favicon='static/icons/icon-192.png',
        language='en-US',
        storage_secret=(os.environ.get('STORAGE_SECRET') or '').strip(),
        session_middleware_kwargs=_session_middleware_kwargs(),
        # Don't lower: the 3s default dropped the page whenever a phone locked or Wi-Fi blinked.
        reconnect_timeout=30,
    )
    # Must be added after ui.run_with (outside the session middleware) to strip Set-Cookie off cacheable responses.
    fastapi_app.add_middleware(PublicCacheMiddleware)
    # Also outside NiceGUI's cache middleware, which marks every /_nicegui/
    # answer immutable, failures included.
    fastapi_app.add_middleware(FrameworkCacheMiddleware)
    register_error_handlers(fastapi_app)