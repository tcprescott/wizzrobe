"""CSV export helper for report tables."""

import csv
import io
import zipfile
from datetime import datetime, timezone
from typing import Iterable, Mapping, Sequence


def rows_to_csv_bytes(
    columns: Sequence[Mapping],
    rows: Iterable[Mapping],
) -> bytes:
    """Render a list of NiceGUI-style column descriptors + row dicts as CSV bytes.

    Each column should have at least ``name`` (key into row dict) and ``label``
    (header text). Hidden columns (``hidden=True``) are skipped.
    """
    visible_columns = [c for c in columns if not c.get('hidden')]
    fields = [c['name'] for c in visible_columns]
    headers = [c.get('label', c['name']) for c in visible_columns]

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator='\n')
    writer.writerow(headers)
    for row in rows:
        writer.writerow([_csv_safe_cell(row.get(field, '')) for field in fields])
    return buffer.getvalue().encode('utf-8-sig')


def files_to_zip_bytes(files: Mapping[str, bytes]) -> bytes:
    """Pack ``{filename: content}`` into a single ZIP archive.

    Insertion order is preserved so the archive lists its members in the order
    the caller cares about. Members carry a fixed timestamp: a ZIP entry stores
    a local-time date with no offset, so stamping "now" would bake an ambiguous
    time into the archive for no benefit.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            info = zipfile.ZipInfo(name, date_time=_ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    return buffer.getvalue()


_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def timestamped_filename(prefix: str, ext: str = 'csv') -> str:
    """``prefix-20251130T143015Z.csv`` style filename."""
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    return f'{prefix}-{stamp}.{ext}'


_FORMULA_PREFIXES = ('=', '+', '-', '@')


def _csv_safe_cell(value) -> str:
    s = _stringify(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return s
    if not s:
        return s
    stripped = s.lstrip(' \t\r\n')
    if stripped and stripped[0] in _FORMULA_PREFIXES:
        return "'" + s
    return s


def _stringify(value) -> str:
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, datetime):
        return value.isoformat()
    # Only a display key may leave: the payload carries discord ids and private ADA notes.
    if isinstance(value, Mapping):
        for key in _DISPLAY_KEYS:
            if value.get(key):
                return _stringify(value[key])
        return ''
    if isinstance(value, (list, tuple, set, frozenset)):
        return '; '.join(s for s in (_stringify(v) for v in value) if s)
    return str(value)


_DISPLAY_KEYS = ('name', 'display_name', 'label', 'username', 'title')
