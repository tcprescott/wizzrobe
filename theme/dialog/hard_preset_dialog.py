"""The player's private choice: play this match on the harder preset, or don't.

A dialog rather than a bare toggle, for one reason: a switch that silently
changes what you are about to play, and whose effect depends on somebody else's
answer you are not allowed to see, needs a sentence of explanation before it is
flipped. So the explanation and the two buttons live together, and the board
cell, the deep link and the Discord DM all open this same thing.

Every line of copy here is written to be readable by a player whose opponent
opted in and by one whose opponent did not, without either being able to tell
which they are. The only state it ever reports is the reader's own, plus the
unanimous result once there is nothing left to keep.
"""

from typing import Callable, Optional

from nicegui import ui

from application.services import HardPresetState, MatchHardPresetService
from models import Match, PresetOverride, User
from theme.dialog._helpers import dialog_actions, form_dialog
from theme.notify import notify_error


class HardPresetDialog:
    """Opt in to (or out of) a match's harder preset."""

    def __init__(
        self,
        match: Match,
        actor: User,
        state: HardPresetState,
        *,
        on_change: Optional[Callable] = None,
    ) -> None:
        self.match = match
        self.actor = actor
        self.state = state
        self.on_change = on_change
        self.service = MatchHardPresetService()

    async def open(self) -> None:
        state = self.state

        with form_dialog('Harder settings', card_classes='dialog-card dialog-card--sm') as dialog:
            with ui.column().classes('q-pa-md gap-3 full-width'):
                ui.label(state.preset_name).classes('text-bold text-subtitle1')

                if state.override is not None:
                    chosen = (
                        state.preset_name if state.override == PresetOverride.HARD
                        else state.standard_preset_name or 'the standard settings'
                    )
                    ui.label(
                        f'Staff set this match to {chosen}. That settles it, so there '
                        'is nothing to choose here.'
                    ).classes('text-caption text-grey-7')
                    dialog.open()
                    return

                if state.locked:
                    ui.label(
                        'The seed for this match is already rolled, so its settings '
                        'are decided.'
                    ).classes('text-caption text-grey-7')
                    dialog.open()
                    return

                if state.everyone_in:
                    ui.label(
                        f'Everyone opted in, so this match is playing {state.preset_name}. '
                        'You can still back out until the seed is rolled, and the others '
                        'will be told if you do.'
                    ).classes('text-caption text-grey-7')
                elif state.opted_in:
                    ui.label(
                        "You're in. Nobody will be told unless everyone opts in, so "
                        'nothing has changed yet as far as anyone else can see.'
                    ).classes('text-caption text-grey-7')
                else:
                    ui.label(
                        f'You can play this match on {state.preset_name} instead of '
                        f'{state.standard_preset_name or "the standard settings"}. '
                        'It only happens if everyone in the match opts in.'
                    ).classes('text-caption text-grey-7')
                    ui.label(
                        'Your answer is private. Nobody is told you opted in unless '
                        'all of you did, and nobody is told if you decide against it.'
                    ).classes('text-caption text-grey-7')

                ui.label(
                    'You can change your mind until the seed is rolled, which usually '
                    'happens shortly before the match starts.'
                ).classes('text-caption text-grey-7')

                async def choose(opt_in: bool) -> None:
                    try:
                        if opt_in:
                            await self.service.opt_in(self.match.id, self.actor)
                            ui.notify(
                                f"You're in for {state.preset_name}.", color='positive',
                            )
                        else:
                            await self.service.withdraw(self.match.id, self.actor)
                            ui.notify(
                                f"You're out of {state.preset_name}.", color='positive',
                            )
                    except ValueError as e:
                        notify_error(e)
                        return
                    dialog.close()
                    if self.on_change is not None:
                        await self.on_change()

                with dialog_actions().classes('justify-end'):
                    ui.button('Cancel', on_click=dialog.close).props('flat')
                    if state.opted_in:
                        ui.button(
                            'Back out', icon='undo',
                            on_click=lambda: choose(False),
                        ).props('color=warning')
                    else:
                        ui.button(
                            f'Play {state.preset_name}', icon='bolt',
                            on_click=lambda: choose(True),
                        ).props('color=primary')

        dialog.open()
