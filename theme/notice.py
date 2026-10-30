"""A toast that survives a redirect.

``ui.notify`` enqueues a message on *this* client's outbox. A handler that
notifies and then calls ``ui.navigate.to`` unloads the document the toast would
have rendered in, so the message is written and never seen — the shape behind
nine unreachable failure strings across the OAuth link pages. ``ui.notify``
*during page build* is fine (the outbox buffers it until the websocket
connects), which is what makes the stash/drain pair below work: the message is
emitted while the **next** page is being built.

Use :func:`stash_notice` **only** for a notify that precedes a navigation. Where
a handler notifies and keeps rendering the same page — a table action that calls
``.refresh()`` — ``ui.notify`` is correct and this module is the wrong tool.

Three drain points, which is the honest cost of NiceGUI's page model here:
:meth:`theme.base.BaseLayout.render` (every ``/home/*``, ``/admin/*``,
``/volunteer/*`` page), :func:`theme.error_page.render_error_page` (a notice must
still show on the 403/404 a bad return path lands on), and ``/login``. There is
no single universal hook — ``protected_page``'s wrapper is not one, because
``/home`` is a bare ``@ui.page``.
"""

from typing import Optional

from nicegui import app, ui

from application.tenant_context import get_current_tenant_id
from theme.notify import TOAST_POSITION

__all__ = ['drain_notice', 'stash_notice']

# One slot, last write wins: a queue surfaced stale notices after an aborted redirect chain.
_KEY = 'pending_notice'
_STICKY_SHOWS = 2


def stash_notice(
    message: str,
    *,
    color: str = 'warning',
    sticky: bool = False,
    tenant_id: Optional[int] = None,
) -> None:
    """Queue a toast for the next page this browser loads.

    For the notify-then-redirect case only: ``ui.notify`` followed by
    ``ui.navigate.to`` loses the toast with the document it was queued against.
    Also for a gate that decides something *before* the page it lets you into
    renders (Discord auto-join's welcome). ``sticky`` keeps the toast up until
    it's dismissed, for a message too long to read in five seconds, and keeps
    it stashed until then (for up to :data:`_STICKY_SHOWS` page loads): a
    browser's first visit reloads once to set the timezone cookie, which would
    otherwise take the toast with it. ``tenant_id`` holds it for that
    community's pages: a welcome to one community must not surface on another.
    """
    if not message:
        return
    app.storage.user[_KEY] = {
        'message': message, 'color': color, 'sticky': sticky,
        'shows': _STICKY_SHOWS if sticky else 1,
        'tenant_id': tenant_id,
    }


def drain_notice() -> None:
    """Show and consume a notice stashed before a redirect. Idempotent.

    Pops before notifying, so a re-render cannot replay a message the user has
    already been shown.
    """
    try:
        notice = app.storage.user.pop(_KEY, None)
    except Exception:
        return
    if not isinstance(notice, dict):
        return
    wanted = notice.get('tenant_id')
    if wanted is not None and wanted != get_current_tenant_id():
        app.storage.user[_KEY] = notice
        return
    message = notice.get('message')
    if not message:
        return
    if notice.get('sticky'):
        shows = int(notice.get('shows') or 1) - 1
        if shows > 0:
            app.storage.user[_KEY] = {**notice, 'shows': shows}

        def dismissed() -> None:
            try:
                if app.storage.user.get(_KEY, {}).get('message') == message:
                    app.storage.user.pop(_KEY, None)
            except Exception:
                pass

        ui.notification(
            message, color=notice.get('color') or 'warning', position=TOAST_POSITION,
            timeout=None, multi_line=True, on_dismiss=dismissed,
            # Not close_button: the theme paints it primary, unreadable on a positive toast.
            actions=[{'label': 'Got it', 'color': 'white'}],
        )
        return
    ui.notify(message, color=notice.get('color') or 'warning')
