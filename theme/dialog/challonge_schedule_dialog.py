"""Dialog for scheduling a Challonge bracket matchup.

The tournament and both players come from the bracket, so this dialog only
collects a date/time and delegates to ChallongeService.schedule_challonge_match,
which reuses the existing match-request + acknowledgment flow. Shares its
Suggest a time button with the non-bracket UserMatchDialog match-request
dialog (``theme/dialog/match_dialog.py``), rather than silently pre-filling an
availability/occupancy-aware suggestion on open.
"""

from nicegui import context, ui

from application.services import ChallongeService
from application.tenant_context import get_current_tenant_id, tenant_scope
from application.utils.match_labels import challonge_round_label
from application.utils.timezone import next_whole_hour_local
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
from theme.help import resolve_help


class ChallongeScheduleDialog:
    def __init__(self, challonge_match, actor, opponent_name: str, on_submit=None):
        self.challonge_match = challonge_match
        self.actor = actor
        self.opponent_name = opponent_name
        self.on_submit = on_submit
        self.dialog = None
        self.challonge_service = ChallongeService()

    async def open(self):
        cm = self.challonge_match
        # Captured while the opening handler's slot is alive. on_submit
        # refreshes the section this dialog was built in, deleting it, so the
        # refresh after it has to be handed its client and tenant back
        # (bracket_schedule_dialog has the same shape).
        client = context.client
        tenant_id = get_current_tenant_id()
        slot = next_whole_hour_local()
        default_date = slot.strftime('%Y-%m-%d')
        default_time = slot.strftime('%H:%M')
        player_ids = [cm.participant1.user_id, cm.participant2.user_id]

        with ui.dialog() as dialog, ui.card().classes('dialog-card dialog-card--sm'):
            self.dialog = dialog
            mobile_sheet(dialog)
            dialog_header(
                f'Schedule vs {self.opponent_name}', dialog,
                help=await resolve_help(['suggest-time']),
            )
            with ui.column().classes('q-pa-md gap-2'):
                matchup_line(cm.tournament.name, challonge_round_label(cm.round))
                ui.label('Pick a time you both can play.').classes('text-caption text-grey-7')

                with ui.row().classes('form-pair items-center gap-2'):
                    date = native_date_input('Date', default_date, required=True)
                    time = native_time_input('Time', default_time, required=True)
                error = FieldError(date, time)

                render_suggest_time_button(
                    dialog,
                    get_tournament_id=lambda: cm.tournament_id,
                    get_player_ids=lambda: player_ids,
                    date=date,
                    time=time,
                    missing_message='Unable to suggest a time for this match.',
                )

            async def submit():
                if not (date.value and time.value):
                    error.show('Choose a date and a time.')
                    return
                if not await confirm_local_time(
                    tournament_id=cm.tournament_id, player_ids=player_ids,
                    date_value=date.value, time_value=time.value, actor=self.actor,
                ):
                    return
                try:
                    await self.challonge_service.schedule_challonge_match(
                        challonge_match_pk=cm.id,
                        scheduled_date=date.value,
                        scheduled_time=time.value,
                        actor=self.actor,
                    )
                except (PermissionError, ValueError) as e:
                    error.show(str(e))
                    return
                with self.dialog:
                    ui.notify(
                        booked_notice(
                            self.opponent_name,
                            bool(getattr(cm.tournament, 'allow_reschedule_requests', False)),
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
