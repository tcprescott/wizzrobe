"""Busy state for a button whose work takes long enough to notice.

A button that calls a randomizer, racetime.gg, Challonge or Discord can sit for
seconds with nothing on screen, and the reader presses it again: two race
rooms, a second batch of 25 qualifier seeds. While :func:`busy` runs, the
pressed button says what it is doing ("Syncing…") beside a spinner, is
disabled so it can't be pressed again, and grows a seconds count after three seconds so a long job
reads as working rather than frozen (``static/js/busy.js`` ticks the count in
the browser; the server sends nothing per second).

A sender that is not a button (a ⋮ menu item, which closes as it is tapped)
gets the same words on a spinner toast instead.

Wire a slow handler with :func:`busy_click`::

    ui.button('Sync', icon='sync', on_click=busy_click('Syncing…', sync_now))

and report progress from inside it with ``current_busy().say(...)``.
"""

from contextlib import asynccontextmanager
from contextvars import ContextVar
from inspect import isawaitable
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from nicegui import helpers, ui
from nicegui.element import Element
from nicegui.elements.button import Button
from nicegui.events import ClickEventArguments

from theme.notify import TOAST_POSITION

__all__ = ['Busy', 'busy', 'busy_click', 'current_busy', 'is_busy', 'slot_busy_template']

_current: ContextVar[Optional['Busy']] = ContextVar('wiz_busy', default=None)

_FLAG = '_wiz_busy'


class Busy:
    """One running busy state; :meth:`say` changes what it reads."""

    def __init__(self, button: Optional[Button] = None,
                 toast: Optional[ui.notification] = None) -> None:
        self._button = button
        self._toast = toast

    def say(self, text: str) -> None:
        if self._button is not None and not self._button.is_deleted:
            self._button.text = text
        if self._toast is not None:
            self._toast.message = text

    def progress(self, verb: str, done: int, total: int) -> None:
        """``Rolling 7 of 25…``: the count, for a job that knows its size."""
        self.say(f'{verb} {done} of {total}…')


def current_busy() -> Busy:
    """The busy state around the running handler, or one that shows nothing."""
    return _current.get() or Busy()


def is_busy(target: Optional[Element]) -> bool:
    """Whether :func:`busy` is running on ``target`` (drop a second press)."""
    return bool(getattr(target, _FLAG, False))


def slot_busy_template(flag: str) -> str:
    """The spinner and seconds count for a ``<q-btn>`` written in a slot template.

    Put it inside the button and bind the rest to the same row flag the click
    sets: ``:label`` to the busy words, ``:disable``, ``:icon`` to nothing and
    ``:class="{'wiz-busy-btn': flag}"``.
    """
    return (f'<q-spinner v-if="{flag}" size="1em" aria-hidden="true" />'
            f'<span v-if="{flag}" class="wiz-busy__elapsed" aria-hidden="true"></span>')


@asynccontextmanager
async def busy(target: Optional[Element], text: str) -> AsyncIterator[Busy]:
    """Show ``text`` as busy on ``target`` until the block exits."""
    if isinstance(target, Button) and not target.is_deleted:
        # The words replace the label in place, so the button grows rather
        # than squeezing them over its old label (Quasar's `loading` overlay
        # keeps the old width and spills anything longer).
        label, icon, enabled = target.text, target.props.get('icon'), target.enabled
        with target:
            extras = [
                ui.spinner(size='1.1em', color=None, thickness=6).props('aria-hidden=true'),
                ui.element('span').classes('wiz-busy__elapsed').props('aria-hidden=true'),
            ]
        target.text = text
        target.props(remove='icon').props('aria-busy=true').classes('wiz-busy-btn')
        target.disable()
        setattr(target, _FLAG, True)
        state = Busy(button=target)
        token = _current.set(state)
        try:
            yield state
        finally:
            _current.reset(token)
            setattr(target, _FLAG, False)
            if not target.is_deleted:
                for extra in extras:
                    if not extra.is_deleted:
                        extra.delete()
                target.text = label
                if icon:
                    target.props(f'icon={icon}')
                target.props(remove='aria-busy').classes(remove='wiz-busy-btn')
                target.set_enabled(enabled)
        return

    toast = ui.notification(text, spinner=True, timeout=None, position=TOAST_POSITION)
    if target is not None:
        setattr(target, _FLAG, True)
    state = Busy(toast=toast)
    token = _current.set(state)
    try:
        yield state
    finally:
        _current.reset(token)
        if target is not None:
            setattr(target, _FLAG, False)
        toast.dismiss()


def busy_click(text: str, handler: Callable[..., Any]) -> Callable[[ClickEventArguments], Awaitable[None]]:
    """An ``on_click`` that runs ``handler`` under :func:`busy` on the pressed control."""
    wants_args = helpers.expects_arguments(handler)

    async def run(e: ClickEventArguments) -> None:
        if is_busy(e.sender):
            return
        async with busy(e.sender, text):
            result = handler(e) if wants_args else handler()
            if isawaitable(result):
                await result

    return run
