"""Dialog that downloads the volunteer data export as a ZIP of CSVs."""

from datetime import timedelta

from nicegui import app, ui

from application.services import SystemConfigService, get_user_from_session
from application.services.volunteer.volunteer_export_service import (
    VolunteerExportBundle,
    VolunteerExportService,
)
from application.utils.csv_export import (
    files_to_zip_bytes,
    rows_to_csv_bytes,
    timestamped_filename,
)
from application.utils.timezone import parse_local_datetime
from theme.busy import busy_click
from theme.dialog._helpers import (
    dialog_actions,
    dialog_header,
    mobile_sheet,
    native_date_input,
)
from theme.notify import notify_error


def bundle_to_zip_bytes(bundle: VolunteerExportBundle) -> bytes:
    """Render a bundle as ``README.txt`` + one CSV per sheet, zipped."""
    files = {'README.txt': bundle.readme().encode('utf-8')}
    for sheet in bundle.sheets:
        files[f'{sheet.name}.csv'] = rows_to_csv_bytes(sheet.columns, sheet.rows)
    return files_to_zip_bytes(files)


class VolunteerExportDialog:
    """Pick a date range, download every volunteer table as CSVs in one ZIP."""

    def __init__(self) -> None:
        self.service = VolunteerExportService()

    async def open(self) -> None:
        actor = await get_user_from_session(app.storage.user)
        event_start, event_end = await SystemConfigService.get_event_window()

        with ui.dialog() as dialog, ui.card().classes('dialog-card dialog-card--sm'):
            mobile_sheet(dialog)
            dialog_header('Export volunteer data', dialog)
            with ui.column().classes('q-pa-md gap-3 full-width'):
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
                    bundle = await self.service.build(actor, start, end)
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                ui.download(
                    bundle_to_zip_bytes(bundle),
                    filename=timestamped_filename('volunteer-data', ext='zip'),
                )
                ui.notify('Export downloaded.', color='positive')
                dialog.close()

            with dialog_actions().classes('justify-end'):
                ui.button('Cancel', on_click=dialog.close).props('flat')
                ui.button('Download ZIP', icon='download', on_click=busy_click('Building ZIP…', download)).props('color=primary')
        dialog.open()
