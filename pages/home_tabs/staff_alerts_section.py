"""Profile → Notifications → Staff alerts: duty shifts and the per-tournament opt-out.

Shown only to someone who gets match alerts: an admin-desk volunteer with a
current or upcoming duty shift, or a tournament admin. The desk row explains
why they are pinged and cannot be switched off (the duty shift *is* the
opt-in); each tournament they run gets a switch.
"""

from nicegui import ui

from application.services import StaffAlertService
from application.utils.timezone import format_local_datetime
from models import User


async def render_staff_alerts_section(user: User, *, on_saving, on_saved, on_error) -> None:
    duty = await StaffAlertService.duty_summary(user)
    tournaments = await StaffAlertService.admin_tournaments(user)
    if duty is None and not tournaments:
        return

    async def toggle(tournament_id: int, on: bool) -> None:
        on_saving()
        try:
            await StaffAlertService.set_tournament_alerts(user, tournament_id, on)
        except ValueError as e:
            on_error(str(e))
            ui.notify(str(e), color='warning')
            return
        on_saved()

    with ui.column().classes('wiz-notify-group'):
        ui.label('Staff alerts').classes('wiz-notify-group__head')
        if duty is not None:
            with ui.row().classes('wiz-notify-item no-wrap'):
                ui.icon('support_agent', size='sm').classes('wiz-notify-item__icon')
                with ui.column().classes('gap-0 col min-w-0'):
                    with ui.row().classes('items-center gap-2'):
                        ui.label(f'{duty.position_name} shifts').classes('wiz-notify-item__name')
                        if duty.on_now:
                            with ui.element('span').classes('wiz-chip wiz-chip--ok'):
                                ui.label('On duty now')
                    detail = "While you're on one, you get every player comment and reschedule request."
                    if duty.next_start is not None:
                        detail += f" Next: {format_local_datetime(duty.next_start, '%a %H:%M')}."
                    ui.label(detail).classes('text-muted text-caption')
        if tournaments:
            with ui.column().classes('wiz-notify-item gap-2'):
                with ui.column().classes('gap-0'):
                    ui.label('Tournaments you run').classes('wiz-notify-item__name')
                    ui.label(
                        'Player comments and reschedule requests on their matches, any time.'
                    ).classes('text-muted text-caption')
                for tournament, on in tournaments:
                    with ui.row().classes('items-center no-wrap w-full gap-2'):
                        ui.label(tournament.name).classes('col min-w-0 ellipsis')
                        switch = ui.switch(
                            value=on,
                            on_change=lambda e, tid=tournament.id: toggle(tid, e.value),
                        )
                        switch.props['aria-label'] = f'Staff alerts for {tournament.name}'
