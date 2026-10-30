from inspect import isawaitable
from typing import Any, Optional

from nicegui import helpers, ui
from nicegui.events import ClickEventArguments, handle_event

from theme.busy import busy
from theme.dialog._helpers import dialog_actions, dialog_header, mobile_sheet


class ConfirmationDialog:
    def __init__(self, message: str = "Are you sure?", on_confirm=None, confirm_text="Confirm",
                 cancel_text="Cancel", tone: str = "negative", title: str = "Confirm",
                 require_phrase: Optional[str] = None, busy_text: Optional[str] = None):
        self.message = message
        self.on_confirm = on_confirm
        self.confirm_text = confirm_text
        self.cancel_text = cancel_text
        self.tone = tone
        self.title = title
        self.require_phrase = require_phrase
        self.busy_text = busy_text
        self.typed_phrase = ''
        self.dialog: Any = None

    def open(self):
        with ui.dialog() as dialog, ui.card().classes('dialog-card dialog-card--sm'):
            self.dialog = dialog
            mobile_sheet(dialog)
            dialog_header(self.title, dialog)
            with ui.column():
                # pre-line: callers write paragraphs with '\n\n' that a plain label collapses.
                ui.label(self.message).style('white-space: pre-line')
                phrase_input = None
                if self.require_phrase:
                    phrase_input = ui.input(
                        f'Type “{self.require_phrase}” to confirm',
                    ).classes('input-full-width').props('autocomplete=off')

            required = (self.require_phrase or '').lower()

            def matches(value) -> bool:
                return (value or '').strip().lower() == required

            # Closes before a quick handler runs; left to callers, confirmations sat open over finished work.
            def confirm(e: ClickEventArguments) -> None:
                if phrase_input is not None:
                    self.typed_phrase = phrase_input.value or ''
                dialog.close()
                handle_event(self.on_confirm, e)

            async def confirm_slowly(e: ClickEventArguments) -> None:
                if phrase_input is not None:
                    self.typed_phrase = phrase_input.value or ''
                dialog.props('persistent')
                cancel_button.disable()
                try:
                    async with busy(confirm_button, self.busy_text or ''):
                        if self.on_confirm is not None:
                            result = (self.on_confirm(e) if helpers.expects_arguments(self.on_confirm)
                                      else self.on_confirm())
                            if isawaitable(result):
                                await result
                finally:
                    dialog.close()

            with dialog_actions().classes('justify-end'):
                cancel_button = ui.button(self.cancel_text, on_click=dialog.close).props('flat')
                confirm_button = ui.button(
                    self.confirm_text, on_click=confirm_slowly if self.busy_text else confirm,
                ).props(f'color={self.tone}')
                if phrase_input is not None:
                    confirm_button.bind_enabled_from(
                        phrase_input, 'value', backward=matches,
                    )
        dialog.open()
