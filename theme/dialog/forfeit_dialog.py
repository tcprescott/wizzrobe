"""Forfeit match: staff decide a head-to-head match against one player.

Opened from the match's ⋮ (the match sheet footer and Edit match). One step
replaces check in, start, finish and confirm, and the players get one DM.
"""

from typing import Awaitable, Callable, Optional

from nicegui import app, ui

from application.services import MatchService, get_user_from_session
from application.utils.match_labels import match_model_label
from models import ForfeitReason, Match
from theme.banner import banner
from theme.busy import busy_click
from theme.dialog._helpers import dialog_actions, dialog_header, mobile_sheet
from theme.notify import notify_error


def _name(player) -> str:
    return player.user.preferred_name or player.user.username


class ForfeitDialog:
    def __init__(self, match: Match, on_done: Optional[Callable[[], Awaitable[None]]] = None):
        self.match = match
        self.on_done = on_done
        self.forfeiter_id: Optional[int] = None
        self.reason = ForfeitReason.NO_SHOW

    async def open(self) -> None:
        await self.match.fetch_related('tournament', 'players', 'players__user')
        players = list(self.match.players)  # type: ignore[attr-defined]
        if len(players) != 2:
            ui.notify('A forfeit needs a match with exactly two players.', color='warning')
            return

        with ui.dialog() as dialog, ui.card().classes('dialog-card dialog-card--sm forfeit-dialog'):
            self.dialog = dialog
            mobile_sheet(dialog)
            dialog_header('Forfeit match', dialog)
            with ui.column().classes('q-pa-md gap-3 full-width'):
                ui.label(match_model_label(self.match)).classes('text-caption text-muted')

                @ui.refreshable
                def body() -> None:
                    ui.label("Who's out?").classes('forfeit-dialog__label')
                    with ui.row().classes('forfeit-dialog__who no-wrap full-width gap-2'):
                        for player in players:
                            chosen = player.id == self.forfeiter_id
                            tile = ui.button(
                                _name(player),
                                icon='person_off' if chosen else None,
                                on_click=lambda _, pid=player.id: pick(pid),
                                color=None,
                            ).props('outline no-caps align=left').classes(
                                'forfeit-dialog__tile col' + (' forfeit-dialog__tile--out' if chosen else ''),
                            )
                            tile.props(f'aria-pressed={"true" if chosen else "false"}')
                    ui.label('Why').classes('forfeit-dialog__label')
                    # url-state: exempt — a form choice inside a dialog
                    ui.toggle(
                        {r: r.label for r in ForfeitReason}, value=self.reason,
                        on_change=lambda e: set_reason(e.value),
                    ).props('no-caps unelevated spread toggle-color=primary aria-label="Why"') \
                        .classes('wiz-segmented forfeit-dialog__why')
                    out = next((p for p in players if p.id == self.forfeiter_id), None)
                    if out is not None:
                        winner = next(p for p in players if p.id != out.id)
                        with banner(
                            'The match closes as confirmed and the result goes to the bracket, so '
                            f'it moves on. {_name(winner)} and {_name(out)} each get one DM.',
                            title=f'{_name(winner)} advances.',
                        ):
                            pass

                def pick(player_id: int) -> None:
                    self.forfeiter_id = player_id
                    out = next(p for p in players if p.id == player_id)
                    submit.set_text(f'Forfeit {_name(out)}')
                    submit.enable()
                    body.refresh()

                def set_reason(value) -> None:
                    self.reason = ForfeitReason(value)

                body()
                note = ui.input('Note for staff (optional)').props('maxlength=280 counter') \
                    .classes('full-width')

            async def do_forfeit() -> None:
                if self.forfeiter_id is None:
                    return
                try:
                    actor = await get_user_from_session(app.storage.user)
                    if actor is None:
                        return
                    await MatchService().forfeit_match(
                        self.match.id, self.forfeiter_id, self.reason, actor, note=note.value or '',
                    )
                except (ValueError, PermissionError) as e:
                    with dialog:
                        notify_error(e)
                    return
                out = next(p for p in players if p.id == self.forfeiter_id)
                ui.notify(f'{_name(out)} forfeited. The result is confirmed.', color='positive')
                dialog.close()
                if self.on_done:
                    await self.on_done()

            with dialog_actions():
                ui.space()
                ui.button('Cancel', on_click=dialog.close).props('flat')
                submit = ui.button('Forfeit', on_click=busy_click('Forfeiting…', do_forfeit)).props('color=negative')
                submit.disable()
        dialog.open()
