"""CSV export for any table, not only the reports.

Lifted out of ``pages/admin_tabs/reports/shared.py`` unchanged. It was never a
report detail — "give me these rows in a spreadsheet" is the same request on the
Users board as it is on a crew-hours report — and leaving it there is why only
the reports had it.

Pair it with a customized table by passing the *plan's* columns as the provider
rather than the shipped defaults, so the export matches what the person is
looking at:

    csv_export_button('people', lambda: view._plan.columns, lambda: table.rows)

The rows are already on screen, so the file downloads at once; it is also
recorded under Reports → Exports (``DataExportService.record_table_csv``) like
every other export, so the person can find it again for 3 days.
"""

import logging
from typing import Awaitable, Callable, Iterable, Mapping, Sequence

from nicegui import app, ui

from application.services import DataExportService, get_user_from_session
from application.utils.csv_export import rows_to_csv_bytes, timestamped_filename

logger = logging.getLogger(__name__)

__all__ = ['csv_export_button', 'csv_export_menu_item']


def csv_export_button(
    filename_prefix: str,
    columns_provider: Callable[[], Sequence[Mapping]],
    rows_provider: Callable[[], Iterable[Mapping]],
    label: str = 'Export CSV',
) -> ui.button:
    """Button that downloads the current rows as CSV when clicked."""
    return ui.button(
        label, icon='download',
        on_click=_downloader(filename_prefix, columns_provider, rows_provider),
    ).props('flat dense')


def csv_export_menu_item(
    filename_prefix: str,
    columns_provider: Callable[[], Sequence[Mapping]],
    rows_provider: Callable[[], Iterable[Mapping]],
    label: str = 'Export CSV',
) -> ui.menu_item:
    """The same download as :func:`csv_export_button`, as an entry in an overflow menu."""
    with ui.menu_item(on_click=_downloader(filename_prefix, columns_provider, rows_provider)) as item:
        with ui.item_section().props('avatar'):
            ui.icon('download')
        ui.item_section(label)
    return item


def _downloader(
    filename_prefix: str,
    columns_provider: Callable[[], Sequence[Mapping]],
    rows_provider: Callable[[], Iterable[Mapping]],
) -> Callable[[], Awaitable[None]]:
    async def _click():
        try:
            data = rows_to_csv_bytes(columns_provider(), rows_provider())
            filename = timestamped_filename(filename_prefix)
            ui.download(data, filename=filename)
        except Exception as exc:  # pragma: no cover - defensive UI feedback
            ui.notify(f'Export failed: {exc}', color='negative')
            return
        # The download already happened; failing to list it must not say otherwise.
        try:
            actor = await get_user_from_session(app.storage.user)
            if actor is not None:
                await DataExportService().record_table_csv(actor, filename, filename, data)
        except Exception:
            logger.exception('could not record table CSV export %s', filename)
    return _click
