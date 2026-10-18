"""Dialog for booking a matchup from a linked bracket site (start.gg, Challonge).

The site's bracket decides the tournament and both players, so this only
collects a date and time and books through :mod:`bracket_sources`, which hands
off to that site's service and the usual match-request flow. Same shape as the
Challonge dialog, written once for every site on the shared contract.

Two modes, as in ``bracket_schedule_dialog``: a player books their own matchup
("Schedule vs Bob"); staff pass ``matchup_label`` to book someone else's
("Book Alice vs Bob") from Match Schedule's "Not booked yet" view, and may book
outside the stage's window after a warning.
"""

from nicegui import context, ui

from application.services import bracket_sources
from application.services.bracket_sources import BookableMatch
from application.tenant_context import get_current_tenant_id, tenant_scope
from application.utils.timezone import next_whole_hour_local
from theme.dialog._helpers import (
    FieldError,
    booked_notice,
    booking_window_line,
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


class BracketSourceScheduleDialog:
    def __init__(self, bookable: BookableMatch, actor, on_submit=None, *, matchup_label: str | None = None):
        self.bookable = bookable
        self.actor = actor
        self.on_submit = on_submit
        self.matchup_label = matchup_label
        self.dialog = None

    async def open(self):
        item = self.bookable
        # Captured while the opening handler's slot is alive: on_submit rebuilds
        # the section this dialog was opened from.
        client = context.client
        tenant_id = get_current_tenant_id()
        slot = next_whole_hour_local()

        with ui.dialog() as dialog, ui.card().classes('dialog-card dialog-card--sm'):
            self.dialog = dialog
            mobile_sheet(dialog)
            dialog_header(
                f'Book {self.matchup_label}' if self.matchup_label else f'Schedule vs {item.opponent_name}',
                dialog,
                help=await resolve_help(['suggest-time']),
            )
            with ui.column().classes('q-pa-md gap-2'):
                matchup_line(item.tournament.name, item.round_label)
                booking_window_line(item.window_text)
                ui.label(
                    'Pick a time both players can make.' if self.matchup_label
                    else 'Pick a time you both can play.'
                ).classes('text-caption text-grey-7')
                with ui.row().classes('form-pair items-center gap-2'):
                    date = native_date_input('Date', slot.strftime('%Y-%m-%d'), required=True)
                    time = native_time_input('Time', slot.strftime('%H:%M'), required=True)
                error = FieldError(date, time)
                render_suggest_time_button(
                    dialog,
                    get_tournament_id=lambda: item.tournament.id,
                    get_player_ids=lambda: item.player_ids,
                    date=date,
                    time=time,
                    missing_message='Unable to suggest a time for this match.',
                    startgg_set_id=item.key if item.source == 'startgg' else None,
                    challonge_match_id=item.key if item.source == 'challonge' else None,
                )

            async def submit(allow_outside_round: bool = False):
                if not (date.value and time.value):
                    error.show('Choose a date and a time.')
                    return
                # A "Book anyway" resubmit has already been past the clash sheet.
                if not allow_outside_round and not await confirm_local_time(
                    tournament_id=item.tournament.id, player_ids=item.player_ids,
                    date_value=date.value, time_value=time.value, actor=self.actor,
                ):
                    return
                try:
                    with tenant_scope(tenant_id):
                        await bracket_sources.schedule(
                            item.source, item.key, date.value, time.value, self.actor,
                            allow_outside_round=allow_outside_round,
                        )
                except (PermissionError, ValueError) as e:
                    with dialog:
                        if confirm_outside_window(e, lambda: submit(True)):
                            return
                    error.show(str(e))
                    return
                with dialog:
                    ui.notify(
                        f'Booked {self.matchup_label}.' if self.matchup_label else booked_notice(
                            item.opponent_name,
                            bool(getattr(item.tournament, 'allow_reschedule_requests', False)),
                        ),
                        color='positive', multi_line=True,
                    )
                    dialog.close()
                if self.on_submit:
                    with client, tenant_scope(tenant_id):
                        await self.on_submit()

            with dialog_actions().classes('justify-end'):
                ui.button('Cancel', on_click=dialog.close).props('flat')
                ui.button('Schedule', icon='event', on_click=submit).props('color=primary')

            submit_on_enter(dialog, submit)
            dialog.open()
