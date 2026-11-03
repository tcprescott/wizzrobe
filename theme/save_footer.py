"""A card's own Save bar, shown only once something in the card has changed.

The old Settings page had one Save at the bottom that wrote a dozen unrelated
values, so fixing a typo in the reminder lead also rewrote the event dates, and
a bad hour on day three refused to save the player limit. Each settings card
now owns its values and its Save, and the bar appears only when there is
something to save.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Awaitable, Callable, Iterator, Optional

from nicegui import ui
from nicegui.elements.mixins.value_element import ValueElement

_collecting: ContextVar[Optional['UnsavedChanges']] = ContextVar('save_footer_collecting', default=None)


class UnsavedChanges:
    """Every ``SaveFooter`` built inside :meth:`collect`, so leaving can ask about them."""

    def __init__(self, on_change: Optional[Callable[[], None]] = None) -> None:
        self.footers: list['SaveFooter'] = []
        self._on_change = on_change

    @contextmanager
    def collect(self) -> Iterator[None]:
        token = _collecting.set(self)
        try:
            yield
        finally:
            _collecting.reset(token)

    def dirty(self) -> list['SaveFooter']:
        return [f for f in self.footers if f.dirty and not f.row.is_deleted]

    def changed(self) -> None:
        if self._on_change is not None:
            self._on_change()


class SaveFooter:
    """The footer row of a ``section_panel``; build it inside the panel."""

    def __init__(
        self,
        on_save: Callable[[], Awaitable[bool]],
        *,
        label: str = 'Save',
        enabled: bool = True,
    ) -> None:
        self._on_save = on_save
        self._baseline: dict[ValueElement, Any] = {}
        self._restoring = False
        self.dirty = False
        with ui.row().classes('wiz-panel__foot items-center no-wrap') as self.row:
            self._hint = ui.label('').classes('wiz-panel__foot-hint')
            ui.space()
            ui.button('Discard', on_click=self.discard).props('flat no-caps color=primary')
            self.button = ui.button(label, on_click=self.save).props('color=primary unelevated no-caps')
        self._enabled = enabled
        self.row.set_visibility(False)
        self._group = _collecting.get()
        if self._group is not None:
            self._group.footers.append(self)

    def watch(self, *elements: ValueElement) -> 'SaveFooter':
        """Light the button when any of ``elements`` changes value."""
        for element in elements:
            self._baseline[element] = element.value
            element.on_value_change(lambda _e: self.mark_dirty())
        return self

    def mark_dirty(self) -> None:
        if self._enabled and not self._restoring:
            self.row.set_visibility(True)
            self._hint.text = 'Unsaved changes'
            self._set_dirty(True)

    def mark_clean(self) -> None:
        self.row.set_visibility(False)
        self._set_dirty(False)

    def discard(self) -> None:
        """Put every watched field back to what it held when last saved."""
        self._restoring = True
        try:
            for element, value in self._baseline.items():
                if not element.is_deleted:
                    element.value = value
        finally:
            self._restoring = False
        self.mark_clean()

    async def save(self) -> bool:
        """``on_save`` returns whether it saved; a refusal keeps the edits live."""
        self.button.props('loading')
        try:
            saved = await self._on_save()
        finally:
            self.button.props(remove='loading')
        if saved:
            self._baseline = {e: e.value for e in self._baseline if not e.is_deleted}
            self.mark_clean()
        return saved

    def _set_dirty(self, dirty: bool) -> None:
        if dirty != self.dirty:
            self.dirty = dirty
            if self._group is not None:
                self._group.changed()
