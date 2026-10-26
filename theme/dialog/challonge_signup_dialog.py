"""Confirm a Challonge sign-up before Wizzrobe adds the player to the bracket.

Signing up here creates an entry on Challonge and makes Challonge email the
player, so the sheet shows the name they will join under before anything is sent.
"""

from typing import Awaitable, Callable

from nicegui import ui

from theme.busy import busy_click
from theme.dialog._helpers import dialog_actions, dialog_header, mobile_sheet


def open_challonge_signup_dialog(
    tournament_name: str,
    challonge_name: str,
    entrant_count: int,
    on_confirm: Callable[[], Awaitable[None]],
) -> None:
    with ui.dialog() as dialog, ui.card().classes('dialog-card dialog-card--sm'):
        mobile_sheet(dialog)
        dialog_header('Sign up on Challonge?', dialog)
        with ui.element('div').classes('wiz-signup-facts'):
            ui.label('Bracket').classes('text-muted')
            ui.label(tournament_name).classes('text-bold')
            ui.label('Challonge name').classes('text-muted')
            ui.label(challonge_name).classes('text-bold')
            ui.label('Entrants').classes('text-muted')
            ui.label(f'{entrant_count} so far')
        ui.label(
            'Challonge will email you an invite to confirm the entry is yours. '
            'You can withdraw here until the bracket starts.'
        )

        async def confirm() -> None:
            await on_confirm()
            dialog.close()

        with dialog_actions().classes('justify-end'):
            ui.button('Cancel', on_click=dialog.close).props('flat')
            ui.button('Sign up', icon='how_to_reg', on_click=busy_click('Signing up…', confirm)).props('color=primary')
    dialog.open()
