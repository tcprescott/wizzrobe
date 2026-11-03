from nicegui import ui

from application.utils.easter_eggs import current_theme, random_fact
from theme.dialog._helpers import dialog_actions, dialog_header, mobile_sheet


class FactDialog:
    """A hidden easter-egg dialog that serves up a random fact from today's theme."""

    def __init__(self):
        self.dialog = None
        self._fact_label = None

    def _reroll(self) -> None:
        if self._fact_label is not None:
            self._fact_label.set_text(random_fact(exclude=self._fact_label.text))

    def _see_all(self) -> None:
        if self.dialog is not None:
            self.dialog.close()
        ui.navigate.to('/facts')

    def open(self):
        theme = current_theme()
        with ui.dialog() as dialog, ui.card().classes('dialog-card dialog-card--sm'):
            self.dialog = dialog
            mobile_sheet(dialog)
            dialog_header(f'{theme.emoji} {theme.noun}', dialog)
            with ui.column().classes('items-center'):
                ui.icon(theme.icon).props('size=lg')
                self._fact_label = ui.label(random_fact()).classes('text-center')
            with dialog_actions().classes('justify-end'):
                ui.button('See them all', icon=theme.icon, on_click=self._see_all).props('flat')
                ui.button(theme.another, icon='refresh', on_click=self._reroll).props('color=primary')
        dialog.open()
