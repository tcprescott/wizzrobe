"""Themed renderer for 40x / 50x error pages.

Wraps error content in the standard ``BaseLayout`` chrome (header, drawer,
footer, phoenix palette, dark mode) so error pages match the rest of the app.

The renderer is **synchronous** on purpose: NiceGUI invokes the
``on_page_exception`` handler without awaiting it (see ``nicegui/page.py``
``create_500_error_page``), so the 50x page must be built without ``await``.
The only async work — loading the user to file a feedback report — happens lazily
in the "Report this error" button's click handler.
"""

import logging
from typing import Optional, Sequence

from nicegui import app, context, ui

from models import FeedbackCategory, User
from theme.base import BaseLayout
from theme.notice import drain_notice

logger = logging.getLogger(__name__)

#: The one not-found page. Every "this isn't here" state — an unknown route, a
#: feature the community has off, a dead room link, a stage or article that is
#: missing or not public — says the same thing with the same status, so none of
#: them tells a visitor more than an unknown route would.
NOT_FOUND_HEADLINE = 'Page not found'
NOT_FOUND_MESSAGE = "We couldn't find that page. It may have moved, or the link's out of date."

#: ``(label, icon, target)`` — a button on the error card. Targets are paths
#: handed to ``ui.navigate.to``, so tenant-local.
Action = tuple[str, str, str]


def _set_status(status_code: int) -> None:
    """Make the HTTP response carry the status the card shows.

    A not-found that answers 200 reads as a live page to a link checker, a
    crawler and any cache in front of the app.
    """
    try:
        context.client.status_code = status_code
    except Exception:  # pragma: no cover - no client in scope
        pass


def render_error_page(
    *,
    status_code: int,
    headline: str,
    message: str,
    error_id: str | None = None,
    traceback_text: str | None = None,
    user: User | None = None,
    layout: Optional[BaseLayout] = None,
    actions: Sequence[Action] = (),
) -> None:
    """Render a themed error page into the current page context.

    Args:
        status_code: HTTP status to display prominently (e.g. 404, 500), and
            the status the response carries.
        headline: Short title under the status code.
        message: Friendly explanation shown to the user.
        error_id: Traceable reference shown for 50x errors; when set, logged-in
            users get a "Report this error" button that prefills feedback.
        traceback_text: Full traceback for the debug diagnosis page; omit in
            production so internals are never exposed.
        user: Logged-in user (when known), used for the header/footer.
        layout: A layout an async caller already prepared
            (:meth:`BaseLayout.prepare`), so the page carries the community's
            name, palette and drawer. Without one the chrome falls back to the
            shipped defaults — the synchronous 500 path has no way to await.
        actions: Buttons offered before "Back to home", the first one primary.
    """
    _set_status(status_code)
    ui.page_title(f'{headline} — {layout.wordmark if layout else "Wizzrobe"}')

    # A notice stashed before a redirect must still be shown on the error page a
    # bad return path lands on — this renderer does not go through
    # BaseLayout.render(), so it drains for itself.
    try:
        drain_notice()
    except Exception:  # pragma: no cover - defensive, inside an error handler
        logger.exception('Failed to drain a pending notice on the error page')

    # Never let layout chrome throw from inside an error handler.
    try:
        (layout or BaseLayout(user=user)).render_chrome()
    except Exception:  # pragma: no cover - defensive
        logger.exception('Failed to render error-page chrome')

    with ui.column().classes('error-page-container'):
        with ui.card().classes('error-card'):
            ui.label(str(status_code)).classes('error-status')
            ui.label(headline).classes('error-headline').tag = 'h1'
            ui.label(message).classes('error-message')

            if status_code == 404:
                from application.utils.easter_eggs import random_cat_fact
                with ui.row().classes('error-cat-fact items-center'):
                    ui.icon('pets').props('size=sm')
                    ui.label(random_cat_fact())

            if error_id:
                _remember_error_id(error_id)
                with ui.row().classes('error-ref-row items-center'):
                    ui.icon('tag').props('size=sm')
                    ui.label('Error reference:').classes('error-ref-label')
                    ui.label(error_id).classes('error-ref-id')

            with ui.row().classes('error-actions gap-2'):
                for index, (label, icon, target) in enumerate(actions):
                    ui.button(
                        label, icon=icon,
                        on_click=lambda t=target: ui.navigate.to(t),
                    ).props('color=primary' if index == 0 else 'outline color=primary')
                if not any(target == '/' for _label, _icon, target in actions):
                    ui.button(
                        'Back to home', icon='home',
                        on_click=lambda: ui.navigate.to('/'),
                    ).props('flat color=primary' if actions else 'color=primary')
                if error_id and _is_authenticated():
                    report = ui.button(
                        'Report this error', icon='feedback',
                        # Return the coroutine so NiceGUI awaits it in the button's
                        # slot; a bare background task has no slot and the dialog/
                        # notify calls inside would raise.
                        on_click=lambda: _open_error_report(error_id),
                    ).props('flat')
                    # Only where the viewer could actually send it — a member with
                    # Feedback live, as the drawer decides. The
                    # 500 path renders synchronously with no prepared layout, so
                    # there it starts hidden and an async check reveals it.
                    if layout is not None and layout.is_prepared:
                        report.set_visibility(layout.offers_feedback)
                    else:
                        report.set_visibility(False)
                        _reveal_when_member(report)

            if traceback_text:
                ui.label('Diagnostic details (development only)').classes('error-trace-title')
                ui.code(traceback_text, language='python').classes('error-trace')


def _reveal_when_member(button) -> None:
    """Show ``button`` once the signed-in viewer turns out to be able to send feedback."""
    from nicegui import background_tasks, context

    from application.tenant_context import get_current_tenant_id

    try:
        client = context.client
        tenant_id = get_current_tenant_id()
        # A copy: the background task below runs outside the session.
        session = dict(app.storage.user)
    except Exception:  # pragma: no cover - no client or session in scope
        return
    if tenant_id is None or not session.get('authenticated'):
        return
    background_tasks.create(_reveal(button, client, tenant_id, session))


async def _reveal(button, client, tenant_id: int, session: dict) -> None:
    from application.services import (
        FeatureFlagService,
        TenantService,
        get_user_from_session,
    )
    from application.tenant_context import tenant_scope
    from models import FeatureFlag

    try:
        with tenant_scope(tenant_id):
            user = await get_user_from_session(session)
            if user is None:
                return
            allowed = (
                await TenantService.is_member(user.id, tenant_id)
                and await FeatureFlagService().is_enabled(FeatureFlag.FEEDBACK)
            )
        if allowed:
            with client:
                button.set_visibility(True)
    except Exception:
        logger.exception('Failed to resolve whether to offer an error report')


def _is_authenticated() -> bool:
    try:
        return bool(app.storage.user.get('authenticated', False))
    except Exception:
        return False


def _remember_error_id(error_id: str) -> None:
    try:
        app.storage.user['last_error_id'] = error_id
    except Exception:
        pass


async def _open_error_report(error_id: str) -> None:
    """Load the current user lazily and open the prefilled feedback dialog."""
    from application.services.auth_service import get_user_from_session
    from theme.dialog import FeedbackDialog

    user = await get_user_from_session(app.storage.user)
    if user is None:
        ui.notify('Please log in to report this error.', color='warning')
        return
    initial_message = (
        f'Error reference: {error_id}\n\n'
        'What I was doing when this happened:\n'
    )
    await FeedbackDialog(
        user,
        initial_category=FeedbackCategory.BUG.value,
        initial_message=initial_message,
    ).open()


async def prepared_layout(user: Optional[User]) -> BaseLayout:
    """A layout for an error page, resolved so it carries the community's chrome.

    Never raises: an error page whose chrome lookup fails still renders, on
    the shipped defaults.
    """
    try:
        layout = BaseLayout(user=user)
        return await layout.prepare()
    except Exception:
        logger.exception('Failed to resolve error-page chrome')
        return BaseLayout(user=user)


async def render_not_found(
    *,
    user: Optional[User] = None,
    headline: str = NOT_FOUND_HEADLINE,
    message: str = NOT_FOUND_MESSAGE,
    actions: Sequence[Action] = (),
) -> None:
    """The themed 404, in the community's chrome, showing who is looking."""
    render_error_page(
        status_code=404,
        headline=headline,
        message=message,
        user=user,
        layout=await prepared_layout(user),
        actions=actions,
    )
