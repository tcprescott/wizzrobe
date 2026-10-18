"""Dialog for scheduling one game of a native bracket matchup.

The tournament and both players come from the bracket, so this dialog only
collects a date/time and delegates to ``BracketService.schedule_bracket_match``,
which routes staff through ``create_match`` and the matchup's own entrants
through ``submit_match_request``. Peer of
:mod:`theme.dialog.challonge_schedule_dialog`, and shared by the player dashboard
and the staff bracket view so both surfaces get the same wording and the same
date/time inputs.

Two modes, because the two surfaces address different people: pass
``opponent_name`` for a player scheduling their own matchup ("Schedule vs Bob"),
or ``matchup_label`` for staff scheduling someone else's ("Alice vs Bob"). The
Suggest a time button needs the two user ids, which the staff bracket view does
not carry; Match Schedule's "Not booked yet" view does. Shares the button with the non-bracket
UserMatchDialog match-request dialog (``theme/dialog/match_dialog.py``).

``tenant_id`` is optional: pass it from a detached client event (the bracket
view's card click); otherwise it is read when the dialog opens. Either way the
booking and ``on_submit`` run under ``tenant_scope`` and inside the client the
dialog opened in. That is not belt and braces: the player board's ``on_submit``
refreshes the section the dialog was built in, which deletes the dialog, and the
board refresh that follows used to find no slot, no client stash and so no
tenant ("No tenant in context" on every booking).

A booking that lost a race (``AlreadyBookedError``) closes the dialog and runs
``on_submit`` too, because the Schedule button it would otherwise leave on
screen can only fail again.
"""

from contextlib import nullcontext
from typing import List, Optional

from nicegui import Client, context, ui

from application.errors import AlreadyBookedError
from application.services import BracketService
from application.tenant_context import get_current_tenant_id, tenant_scope
from application.utils.timezone import format_local_display, next_whole_hour_local
from theme.dialog._helpers import (
    FieldError,
    booked_notice,
    dialog_actions,
    dialog_header,
    matchup_line,
    mobile_sheet,
    native_date_input,
    native_time_input,
    render_suggest_time_button,
    submit_on_enter,
)
from theme.dialog.clash_warning import confirm_local_time
from theme.dialog.round_schedule_dialog import confirm_outside_window
from theme.help import resolve_help


class BracketScheduleDialog:
    def __init__(
        self,
        bracket_match_id: int,
        actor,
        *,
        opponent_name: Optional[str] = None,
        matchup_label: Optional[str] = None,
        game_number: int = 1,
        best_of: int = 1,
        tournament_name: str = '',
        round_label: str = '',
        tournament_id: Optional[int] = None,
        player_ids: Optional[List[int]] = None,
        tenant_id: Optional[int] = None,
        opponent_can_ask: bool = False,
        on_submit=None,
    ):
        self.bracket_match_id = bracket_match_id
        self.actor = actor
        self.opponent_name = opponent_name
        self.matchup_label = matchup_label
        self.game_number = game_number
        self.best_of = best_of
        self.tournament_name = tournament_name
        self.round_label = round_label
        self.tournament_id = tournament_id
        self.player_ids = player_ids or []
        self.tenant_id = tenant_id
        self.opponent_can_ask = opponent_can_ask
        self.on_submit = on_submit
        self.dialog = None
        self._client: Optional[Client] = None
        self.bracket_service = BracketService()

    def _scope(self):
        return nullcontext() if self.tenant_id is None else tenant_scope(self.tenant_id)

    def _heading(self) -> str:
        game = (
            f'Schedule game {self.game_number} of {self.best_of}'
            if self.best_of > 1 else 'Schedule match'
        )
        return f'{game} vs {self.opponent_name}' if self.opponent_name else game

    def _defaults(self) -> tuple:
        slot = next_whole_hour_local()
        return slot.strftime('%Y-%m-%d'), slot.strftime('%H:%M')

    def _booked_message(self) -> str:
        if not self.opponent_name:
            return 'Match scheduled.'
        return booked_notice(self.opponent_name, self.opponent_can_ask)

    async def _after_submit(self) -> None:
        if self.on_submit is None or self._client is None:
            return
        with self._client, self._scope():
            await self.on_submit()

    async def open(self):
        self._client = context.client
        if self.tenant_id is None:
            self.tenant_id = get_current_tenant_id()
        default_date, default_time = self._defaults()
        can_suggest = bool(
            (self.opponent_name or self.matchup_label) and self.tournament_id
            and len(self.player_ids) == 2
        )

        with ui.dialog() as dialog, ui.card().classes('dialog-card dialog-card--sm'):
            self.dialog = dialog
            mobile_sheet(dialog)
            dialog_header(
                self._heading(), dialog,
                help=await resolve_help(['suggest-time'] if can_suggest else []),
            )
            with ui.column().classes('q-pa-md gap-2'):
                if self.tournament_name:
                    matchup_line(self.tournament_name, self.round_label)
                if self.opponent_name:
                    ui.label('Pick a time you both can play.').classes('text-caption text-grey-7')
                elif self.matchup_label:
                    ui.label(self.matchup_label).classes('text-muted')

                with ui.row().classes('form-pair items-center gap-2'):
                    date = native_date_input('Date', default_date, required=True)
                    time = native_time_input('Time', default_time, required=True)
                error = FieldError(date, time)

                if can_suggest:
                    # Needs both players' ids: the player dashboard and Match
                    # Schedule's "Not booked yet" view pass them, the staff
                    # bracket view doesn't. The suggestion runs in this click's
                    # own request-scoped tenant context.
                    render_suggest_time_button(
                        dialog,
                        get_tournament_id=lambda: self.tournament_id,
                        get_player_ids=lambda: self.player_ids,
                        get_bracket_match_id=lambda: self.bracket_match_id,
                        date=date,
                        time=time,
                        missing_message='Unable to suggest a time for this match.',
                    )

            async def submit(allow_outside_round: bool = False):
                if not (date.value and time.value):
                    error.show('Choose a date and a time.')
                    return
                # A "Book anyway" resubmit has already been past the clash sheet.
                if not allow_outside_round:
                    with self._scope():
                        go_ahead = await confirm_local_time(
                            tournament_id=self.tournament_id, player_ids=self.player_ids,
                            date_value=date.value, time_value=time.value, actor=self.actor,
                        )
                    if not go_ahead:
                        return
                try:
                    with self._scope():
                        await self.bracket_service.schedule_bracket_match(
                            self.actor, self.bracket_match_id,
                            scheduled_date=date.value,
                            scheduled_time=time.value,
                            game_number=self.game_number,
                            allow_outside_round=allow_outside_round,
                        )
                except AlreadyBookedError as e:
                    with dialog:
                        ui.notify(e.describe(format_local_display(e.scheduled_at)),
                                  color='warning', multi_line=True)
                        dialog.close()
                except (PermissionError, ValueError) as e:
                    with dialog:
                        if confirm_outside_window(e, lambda: submit(True)):
                            return
                    error.show(str(e))
                    return
                else:
                    with dialog:
                        ui.notify(self._booked_message(), color='positive', multi_line=True)
                        dialog.close()
                await self._after_submit()

            with dialog_actions().classes('justify-end'):
                ui.button('Cancel', on_click=dialog.close).props('flat')
                ui.button('Schedule', icon='event', on_click=submit).props('color=primary')

            submit_on_enter(dialog, submit)
            dialog.open()
