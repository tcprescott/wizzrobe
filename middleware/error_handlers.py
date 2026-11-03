"""NiceGUI exception handlers that render themed 40x/50x pages.

NiceGUI is mounted as a sub-application by ``ui.run_with`` (``app.mount('/',
core.app)``), so it handles its own 404/500 responses with the built-in
"sad face" page (``nicegui.error.error_content``). To theme those we override
NiceGUI's handlers on the NiceGUI ``app`` (``core.app``) rather than on the host
FastAPI app:

- **404**: override ``@app.exception_handler(404)``, keeping NiceGUI's guard that
  lets non-page endpoints (e.g. the REST API) return JSON, and rendering a themed
  page for actual UI routes.
- **500**: use ``app.on_page_exception`` — NiceGUI invokes it (synchronously)
  from ``create_500_error_page`` for exceptions raised inside ``@ui.page``
  builders, which is where essentially all of this app's request handling runs.

Every unhandled 500 gets a unique ``error_id`` that is logged (clearly marked)
and tagged on the Sentry scope, so logs, Sentry, and any feedback report the user
files all reference the same UUID. In non-production the 50x page shows the full
traceback; in production it shows only the UUID.
"""

import logging
import traceback
import uuid

import sentry_sdk
from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from nicegui import Client, app, context, ui
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from application.utils.environment import is_production
from theme.error_page import (
    NOT_FOUND_HEADLINE,
    NOT_FOUND_MESSAGE,
    prepared_layout,
    render_error_page,
)

logger = logging.getLogger(__name__)


def log_unhandled_error(exc: BaseException, path: str) -> str:
    """Log an unhandled exception with a traceable UUID and tag it in Sentry.

    The tag is set **before** the log call, and that order is load-bearing:
    Sentry's ``LoggingIntegration`` turns this ``logger.error`` into an event
    synchronously, snapshotting the scope's tags as it goes. Tagging afterwards
    left the event with no ``error_id`` at all — so the id printed on the user's
    error page matched nothing in Sentry — and then leaked that id onto the next
    unrelated event in the same request scope.
    """
    error_id = str(uuid.uuid4())
    try:
        sentry_sdk.set_tag('error_id', error_id)
    except Exception:  # pragma: no cover - Sentry optional
        pass
    logger.error('UNHANDLED ERROR error_id=%s path=%s', error_id, path, exc_info=exc)
    return error_id


async def _current_user_best_effort():
    """Load the logged-in user without letting a failure break the error page."""
    try:
        from application.services.auth_service import get_user_from_session
        return await get_user_from_session(app.storage.user)
    except Exception:
        return None


def register_error_handlers(fastapi_app: FastAPI) -> None:
    """Register themed 40x/50x handlers on the mounted NiceGUI app.

    ``fastapi_app`` is accepted for symmetry with the rest of frontend init but
    the handlers must live on the NiceGUI sub-app that actually serves the pages.
    """

    @app.exception_handler(404)
    async def _not_found_handler(request: Request, exc: Exception) -> Response:
        endpoint = request.scope.get('endpoint')
        if (
            endpoint is not None
            and endpoint is not app
            and not request.scope.get('nicegui_page_path')
            and isinstance(exc, StarletteHTTPException)
        ):
            return await http_exception_handler(request, exc)
        from middleware.tenant import UNKNOWN_COMMUNITY_SCOPE_KEY

        user = await _current_user_best_effort()
        with Client(ui.page(''), request=request) as client:
            if request.scope.get(UNKNOWN_COMMUNITY_SCOPE_KEY):
                render_error_page(
                    status_code=404,
                    headline='Community not found',
                    message=(
                        "There's no community at this address. The link may be "
                        'mistyped, or the community may have moved or closed.'
                    ),
                    user=user,
                    layout=await prepared_layout(user),
                    actions=[('See all communities', 'groups', '/')],
                )
            else:
                render_error_page(
                    status_code=404,
                    headline=NOT_FOUND_HEADLINE,
                    message=NOT_FOUND_MESSAGE,
                    user=user,
                    layout=await prepared_layout(user),
                )
        return client.build_response(request, 404)

    def _page_exception_handler(exc: Exception) -> None:
        path = '-'
        try:
            path = context.client.request.url.path
        except Exception:
            pass
        error_id = log_unhandled_error(exc, path)
        traceback_text = None
        if not is_production():
            traceback_text = ''.join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            ).strip()
        render_error_page(
            status_code=500,
            headline='Something went wrong',
            message=(
                "Something broke on our end. We've already been notified. "
                'If you reach out about it, just include this reference:'
            ),
            error_id=error_id,
            traceback_text=traceback_text,
        )

    app.on_page_exception(_page_exception_handler)
