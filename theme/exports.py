"""Shared presentation for data exports: status chips, rows, and the wait-then-download.

Every export lands in Reports → Exports. Anything that queues one (the
Exports page, the volunteer export dialog) uses :func:`watch_until_ready` so the
file downloads in the browser that asked for it as soon as the worker finishes.
"""

from typing import Any, Callable, Optional

from nicegui import ui

from application.services import DataExportService
from application.utils.timezone import format_local_datetime, format_local_time
from models import DataExport, DataExportKind, DataExportStatus, User
from theme.notify import notify_error

KIND_LABEL = {
    DataExportKind.TOURNAMENTS: 'Tournaments',
    DataExportKind.VOLUNTEERS: 'Volunteer data',
    DataExportKind.TABLE_CSV: 'Table CSV',
}

POLL_SECONDS = 2.0


def _size(n: int) -> str:
    if not n:
        return ''
    if n < 1024:
        return f'{n} B'
    if n < 1024 * 1024:
        return f'{n / 1024:.0f} KB'
    return f'{n / (1024 * 1024):.1f} MB'


def status_text(e: DataExport) -> tuple[str, str]:
    """``(label, chip class)`` for an export's state."""
    if e.status == DataExportStatus.READY:
        return 'Ready', 'wiz-chip--ok'
    if e.status == DataExportStatus.FAILED:
        return 'Failed', 'wiz-chip--cancelled'
    if e.status == DataExportStatus.RUNNING:
        if e.progress_total > 1:
            return f'Building {e.progress_done} of {e.progress_total}', 'wiz-chip--pending'
        return 'Building', 'wiz-chip--pending'
    return 'Queued', 'wiz-chip--pending'


def export_row(e: DataExport) -> dict[str, Any]:
    label, chip = status_text(e)
    expires = DataExportService.expires_at(e)
    return {
        'id': e.id,
        'title': e.title,
        'kind': KIND_LABEL.get(e.kind, e.kind.value),
        'requested': f"{format_local_datetime(e.created_at, '%a %d %b')} {format_local_time(e.created_at)}",
        'status': label,
        'status_cls': chip,
        'error': e.error or '',
        'size': _size(e.size_bytes),
        'expires': f"{format_local_datetime(expires, '%a %d %b')} {format_local_time(expires)}",
        'ready': e.status == DataExportStatus.READY,
        'has_json': e.kind == DataExportKind.TOURNAMENTS,
        'pending': e.status in DataExportStatus.pending(),
    }


async def download_export(actor: User, export_id: int, fmt: str = 'file') -> bool:
    """Send a finished export to this browser. ``False`` (with a toast) when it can't."""
    try:
        f = await DataExportService().download(actor, export_id, fmt)
    except (ValueError, PermissionError) as e:
        notify_error(e)
        return False
    ui.download(f.content, filename=f.filename, media_type=f.media_type)
    return True


def watch_until_ready(
    actor: User, export_id: int, fmt: str = 'file',
    *, on_change: Optional[Callable[[DataExport], Any]] = None,
) -> ui.timer:
    """Poll one export and download it here once it's ready.

    Build inside the slot the page or dialog owns: the timer dies with it, so a
    reader who navigates away just finds the file under Reports → Exports.
    """
    service = DataExportService()

    async def poll() -> None:
        try:
            export = await service.get_mine(actor, export_id)
        except (ValueError, PermissionError):
            timer.deactivate()
            return
        if on_change is not None:
            result = on_change(export)
            if hasattr(result, '__await__'):
                await result
        if export.status == DataExportStatus.READY:
            timer.deactivate()
            if await download_export(actor, export_id, fmt):
                ui.notify(f'{export.title}: downloaded.', color='positive')
        elif export.status == DataExportStatus.FAILED:
            timer.deactivate()
            ui.notify(f'{export.title}: {export.error or "the export failed."}', color='negative')

    timer = ui.timer(POLL_SECONDS, poll)
    return timer
