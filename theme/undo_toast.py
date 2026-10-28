"""The toast a one-tap action leaves behind, with Undo while it's still held.

Pairs with :mod:`application.pending_actions`: the action waits there for
:data:`~application.pending_actions.UNDO_SECONDS`, and the toast's countdown bar
runs out with it. Undo cancels the hold, so nothing was ever written or sent.
"""

import json
from typing import Awaitable, Callable, Optional

from nicegui import context, ui

from application import pending_actions
from theme.notify import TOAST_POSITION

Action = Callable[[], Awaitable[None]]


def undo_toast(
    message: str,
    token: str,
    *,
    caption: str = '',
    icon: str = 'check_circle',
    undone: str = 'Undone.',
    on_undone: Optional[Action] = None,
    extra: Optional[tuple[str, Action]] = None,
) -> None:
    """Show ``message`` with Undo for the held action ``token``.

    ``undone`` is the toast after a successful Undo; ``on_undone`` repaints
    whatever showed the action as on its way. ``extra`` is one more action
    beside Undo, as ``(label, handler)``: the proctor's Flag for review.
    """
    toast = ui.notification(
        message, position=TOAST_POSITION, type='positive', icon=icon,
        timeout=pending_actions.UNDO_SECONDS, progress=True, caption=caption,
        # Two actions don't fit beside the message on a phone.
        multi_line=extra is not None,
    )
    client = context.client

    async def undo() -> None:
        with client:
            if not pending_actions.cancel(token):
                ui.notify("Too late to undo: it's already saved. Open the match to change it.",
                          color='warning')
                return
            ui.notify(undone, color='info')
            if on_undone is not None:
                await on_undone()

    actions = []
    if extra is not None:
        label, handler = extra

        async def run_extra() -> None:
            with client:
                await handler()

        toast.on('extra', run_extra)
        actions.append(_action(label, toast.id, 'extra'))
    toast.on('undo', undo)
    actions.append(_action('Undo', toast.id, 'undo'))
    toast._props['options']['actions'] = actions
    toast.update()


def _action(label: str, element_id: int, event: str) -> dict:
    # Quasar calls the handler, then dismisses the toast; the handler reaches
    # the server as an event on the toast element itself.
    return {
        'label': label, 'color': 'white', 'noCaps': True,
        ':handler': f'() => getElement({element_id}).$emit({json.dumps(event)})',
    }
