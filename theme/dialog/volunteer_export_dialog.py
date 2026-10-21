"""Dialog that queues the volunteer data export (a ZIP of CSVs) and downloads it when built."""

from datetime import timedelta

from nicegui import app, ui

from application.services import DataExportService, SystemConfigService, get_user_from_session
from application.utils.timezone import parse_local_datetime
from pages.admin_tabs.links import REPORTS, admin_url
from theme.busy import busy_click
from theme.dialog._helpers import (
    dialog_actions,
    dialog_header,
    mobile_sheet,
    native_date_input,
)
from theme.exports import watch_until_ready
from theme.notify import notify_error


class VolunteerExportDialog:
    """Pick a date range, then download every volunteer table as CSVs in one ZIP.

    The ZIP is built in the background like every export; the dialog waits for
    it and downloads it, and it stays under Reports → Exports for 3 days.
    """

    def __init__(self) -> None:
        self.service = DataExportService()

    async def open(self) -> None:
        actor = await get_user_from_session(app.storage.user)
        event_start, event_end = await SystemConfigService.get_event_window()

        with ui.dialog() as dialog, ui.card().classes('dialog-card dialog-card--sm'):
            mobile_sheet(dialog)
            dialog_header('Export volunteer data', dialog)
            with ui.column().classes('q-pa-md gap-3 full-width') as body:
                ui.label(
                    'Downloads a ZIP of CSVs — the roster and who is qualified for what, declared '
                    'availability, positions, shifts, assignments, and a slot-by-slot '
                    'schedule grid — for building a schedule in a spreadsheet.'
                ).classes('text-body2')
                with ui.row().classes('form-pair gap-2'):
                    start_input = native_date_input('Start date', event_start.isoformat())
                    end_input = native_date_input('End date', event_end.isoformat())
                ui.label(
                    'Defaults to the configured event window. The roster is always exported '
                    'in full; the range bounds shifts, assignments, and availability.'
                ).classes('italic-note')

            async def download() -> None:
                try:
                    start = parse_local_datetime(start_input.value, '00:00')
                    # Inclusive end date: the window runs to midnight *after* it.
                    end = parse_local_datetime(end_input.value, '00:00') + timedelta(days=1)
                except ValueError:
                    ui.notify('Enter both dates as YYYY-MM-DD.', color='warning')
                    return
                try:
                    export = await self.service.request_volunteers(actor, start, end)
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                body.clear()
                with body:
                    with ui.row().classes('items-center no-wrap gap-2'):
                        ui.spinner(size='sm')
                        ui.label('Building the ZIP. It downloads here as soon as it is ready.')
                    with ui.row().classes('items-center gap-1'):
                        ui.label('You can close this; it stays under').classes('text-body2')
                        ui.link('Reports → Exports', admin_url(REPORTS, report='exports'))
                        ui.label('for 3 days.').classes('text-body2')
                    watch_until_ready(actor, export.id, on_change=_closed_when_done)
                download_button.set_visibility(False)
                cancel_button.set_text('Close')

            def _closed_when_done(export) -> None:
                if export.status.value in ('ready', 'failed'):
                    dialog.close()

            with dialog_actions().classes('justify-end'):
                cancel_button = ui.button('Cancel', on_click=dialog.close).props('flat')
                download_button = ui.button('Download ZIP', icon='download', on_click=busy_click('Queuing…', download)).props('color=primary')
        dialog.open()
