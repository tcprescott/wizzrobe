"""Shared error-toast mapper for the presentation layer.

CLAUDE.md documents a single convention for surfacing service errors in the UI:
a user-facing ``ValueError`` (including ``NotFoundError``) becomes an amber
``warning`` toast showing ``str(e)`` with no prefix, while a ``PermissionError``
(a stale/insufficient session) becomes a red ``negative`` toast. Routing every
handler through :func:`notify_error` keeps the wording and colours consistent and
stops the three drifting variants the audit flagged (``§3.1``).

Every toast also opens at the top of the page, just under the header
(:func:`install_toast_position`): NiceGUI's bottom default sat on top of the
Cancel/Save footer of a phone bottom-sheet dialog, and on the keyboard.
"""

from typing import Any, Callable, Final, Literal

from nicegui import ui

__all__ = ['TOAST_POSITION', 'install_toast_position', 'notify_error']

TOAST_POSITION: Final[Literal['top']] = 'top'

_installed = False


def install_toast_position() -> None:
    """Default every ``ui.notify`` to :data:`TOAST_POSITION`. Idempotent.

    Patched at the source rather than passed per call so the ~400 existing
    toasts, and every new one, land in the same place without anyone
    remembering to. The header offset lives in styles.css.
    """
    global _installed
    if _installed:
        return
    original: Callable[..., Any] = ui.notify

    def notify(message: Any, *, position: str = TOAST_POSITION, **kwargs: Any) -> None:
        original(message, position=position, **kwargs)

    ui.notify = notify
    _installed = True


_LONG_MESSAGE = 90


def notify_error(error: Exception) -> None:
    """Show a service error toast per the documented convention.

    ``PermissionError`` → red (``negative``); any other user-facing error
    (``ValueError`` / ``NotFoundError``) → amber (``warning``). The message is
    ``str(error)`` with no ``Error:`` prefix. Call inside a dialog's slot context
    (e.g. ``with self.dialog:``) when notifying from a dialog handler.

    A long message additionally gets ``multi_line``, a dismiss button and a
    longer timeout: the default toast is one line for five seconds, which is
    where a validation error explaining what to fix went to die.
    """
    color = 'negative' if isinstance(error, PermissionError) else 'warning'
    message = str(error)
    if len(message) > _LONG_MESSAGE:
        ui.notify(
            message, color=color, multi_line=True, close_button='Dismiss',
            timeout=15000, classes='wiz-toast-long',
        )
        return
    ui.notify(message, color=color)
