"""Table Preference Service — per-user table layouts.

Owns validation and persistence of the blob behind each table's Preferences
gear: which columns are visible, in what order, at what width, plus page size,
density and line wrapping, and which groups of a run-sheet board are folded.

**No audit row and no event, deliberately.** ``AuditActions`` are ``verb.object``
records of changes to a *community's data*; hiding a column on your own screen is
not one, and an audit row per drag would drown the log the moment widths persist.
``save`` and ``reset`` instead fire a best-effort telemetry interaction, so
adoption shows up in the existing Engagement report without inventing an event
contract for chrome.

**This service never learns column names.** :meth:`validate` checks shape and
bounds only. The authoritative column list lives in presentation
(``theme/tables/preferences.py``), and a service importing ``theme/`` would
invert the architecture — so an unknown name is reconciled away at render time,
not rejected here.
"""

import logging
from typing import Any, Mapping, Optional

from application.repositories import TablePreferenceRepository
from application.services.telemetry_service import TelemetryService
from models import User

logger = logging.getLogger(__name__)


class TablePreferenceService:
    """Read, validate and store one person's table layouts."""

    ALLOWED_KEYS = ('columns', 'page_size', 'density', 'wrap', 'folded')
    ALLOWED_PAGE_SIZES = (0, 10, 25, 50, 100)     # 0 == "All"
    ALLOWED_DENSITIES = ('comfortable', 'compact')
    MAX_COLUMNS = 64
    MIN_WIDTH, MAX_WIDTH = 40, 1200
    MAX_NAME_LEN = 64
    MAX_FOLDED = 64

    def __init__(self) -> None:
        self.repository = TablePreferenceRepository()

    async def prime(self, user: Optional[User]) -> dict[str, dict]:
        """Every table config for ``user`` — the one query a page build makes.

        Returns ``{}`` for an anonymous viewer. Deliberately does **not** write
        the contextvar: the caller enters ``table_prefs_scope`` with the result,
        which is what lets a test call this directly.
        """
        if user is None:
            return {}
        return await self.repository.all_for_user(user.id)

    async def all_for_user(self, user: User) -> dict[str, dict]:
        return await self.repository.all_for_user(user.id)

    async def get(self, user: User, table_key: str) -> Optional[dict]:
        row = await self.repository.get(user.id, table_key)
        return (row.config or {}) if row is not None else None

    async def save(self, user: User, table_key: str, config: Mapping) -> dict:
        """Validate and store one table's whole layout.

        Folded groups are not the gear's to set, so a save that leaves them out
        keeps the ones already stored.
        """
        clean = self.validate(config)
        if 'folded' not in clean:
            current = await self.get(user, table_key) or {}
            if isinstance(current.get('folded'), Mapping):
                clean['folded'] = self._validate_folded(current['folded'])
        await self.repository.upsert(user.id, table_key, clean)
        await self._track(user, 'table.preferences_saved', table_key)
        return clean

    async def set_width(self, user: User, table_key: str, column: str,
                        width: Optional[int]) -> dict:
        """Persist one dragged column width.

        Read-modify-write of a single entry in the stored config. Two columns
        dragged in quick succession by the same person can interleave; the loser
        is one column width, the row is never corrupted, and the modal's Confirm
        rewrites the whole blob anyway — so this stays lock-free on purpose.
        """
        if not isinstance(column, str) or not column.strip():
            raise ValueError('A column name is required.')
        column = column.strip()
        if width is not None:
            width = self._coerce_width(width)

        current = await self.get(user, table_key) or {}
        columns = [dict(c) for c in current.get('columns', []) if isinstance(c, Mapping)]
        for entry in columns:
            if entry.get('name') == column:
                entry['width'] = width
                break
        else:
            columns.append({'name': column, 'visible': True, 'width': width})

        merged = dict(current)
        merged['columns'] = columns
        clean = self.validate(merged)
        await self.repository.upsert(user.id, table_key, clean)
        return clean

    async def set_page_size(self, user: User, table_key: str, page_size: int) -> dict:
        """Persist the rows-per-page picked in a table's footer.

        Read-modify-write like :meth:`set_width`, and lock-free for the same reason.
        """
        merged = dict(await self.get(user, table_key) or {})
        merged['page_size'] = page_size
        clean = self.validate(merged)
        await self.repository.upsert(user.id, table_key, clean)
        return clean

    async def set_folded(self, user: User, table_key: str, group: str, folded: bool) -> dict:
        """Persist one run-sheet group folded or open.

        Stored per group rather than as a set of folded ones, so a group the
        board folds by default stays open once its viewer has opened it.
        Read-modify-write like :meth:`set_width`, and lock-free for the same reason.
        """
        merged = dict(await self.get(user, table_key) or {})
        current = merged.get('folded')
        groups = dict(current) if isinstance(current, Mapping) else {}
        groups[group] = folded
        merged['folded'] = groups
        clean = self.validate(merged)
        await self.repository.upsert(user.id, table_key, clean)
        return clean

    async def reset(self, user: User, table_key: str) -> None:
        """Forget one table's layout, restoring the shipped defaults."""
        await self.repository.delete(user.id, table_key)
        await self._track(user, 'table.preferences_reset', table_key)

    async def reset_all(self, user: User) -> None:
        """Forget every table layout this person has saved."""
        await self.repository.delete_all_for_user(user.id)
        await self._track(user, 'table.preferences_reset_all', None)

    @classmethod
    def validate(cls, config: Mapping) -> dict:
        """Return a normalised copy of ``config``, or raise ``ValueError``.

        The whole security surface, since this arrives from a browser. Shape and
        bounds only — never column names.
        """
        if not isinstance(config, Mapping):
            raise ValueError('Table preferences must be an object.')

        unknown = sorted(set(config) - set(cls.ALLOWED_KEYS))
        if unknown:
            raise ValueError(f"Unknown table preference: {', '.join(unknown)}.")

        clean: dict[str, Any] = {}

        if 'page_size' in config:
            page_size = config['page_size']
            if isinstance(page_size, bool) or not isinstance(page_size, int) \
                    or page_size not in cls.ALLOWED_PAGE_SIZES:
                raise ValueError(
                    'Rows per page must be one of '
                    f"{', '.join(str(s) for s in cls.ALLOWED_PAGE_SIZES)}.")
            clean['page_size'] = page_size

        if 'density' in config:
            density = config['density']
            if density not in cls.ALLOWED_DENSITIES:
                raise ValueError(
                    f"Density must be one of {', '.join(cls.ALLOWED_DENSITIES)}.")
            clean['density'] = density

        if 'wrap' in config:
            wrap = config['wrap']
            if not isinstance(wrap, bool):
                raise ValueError('Wrap lines must be true or false.')
            clean['wrap'] = wrap

        if 'columns' in config:
            clean['columns'] = cls._validate_columns(config['columns'])

        if 'folded' in config:
            clean['folded'] = cls._validate_folded(config['folded'])

        return clean

    @classmethod
    def _validate_folded(cls, folded: Any) -> dict[str, bool]:
        if not isinstance(folded, Mapping):
            raise ValueError('Folded groups must be an object.')
        if len(folded) > cls.MAX_FOLDED:
            raise ValueError(f'A table may not fold more than {cls.MAX_FOLDED} groups.')
        cleaned: dict[str, bool] = {}
        for name, value in folded.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError('Each folded group needs a name.')
            if len(name) > cls.MAX_NAME_LEN:
                raise ValueError(
                    f'Group names may not exceed {cls.MAX_NAME_LEN} characters.')
            if not isinstance(value, bool):
                raise ValueError(f'Folded for {name} must be true or false.')
            cleaned[name.strip()] = value
        return cleaned

    @classmethod
    def _validate_columns(cls, columns: Any) -> list[dict]:
        if not isinstance(columns, (list, tuple)):
            raise ValueError('Columns must be a list.')
        if len(columns) > cls.MAX_COLUMNS:
            raise ValueError(f'A table may not have more than {cls.MAX_COLUMNS} columns.')

        seen: set[str] = set()
        cleaned: list[dict] = []
        for entry in columns:
            if not isinstance(entry, Mapping):
                raise ValueError('Each column must be an object.')
            extra = sorted(set(entry) - {'name', 'visible', 'width'})
            if extra:
                raise ValueError(f"Unknown column setting: {', '.join(extra)}.")

            name = entry.get('name')
            if not isinstance(name, str):
                raise ValueError('Each column needs a name.')
            name = name.strip()
            if not name:
                raise ValueError('Each column needs a name.')
            if len(name) > cls.MAX_NAME_LEN:
                raise ValueError(
                    f'Column names may not exceed {cls.MAX_NAME_LEN} characters.')
            if name in seen:
                raise ValueError(f'Column {name} is listed twice.')
            seen.add(name)

            visible = entry.get('visible', True)
            if not isinstance(visible, bool):
                raise ValueError(f'Visibility for {name} must be true or false.')

            width = entry.get('width')
            if width is not None:
                width = cls._coerce_width(width, column=name)

            cleaned.append({'name': name, 'visible': visible, 'width': width})
        return cleaned

    @classmethod
    def _coerce_width(cls, width: Any, column: Optional[str] = None) -> int:
        where = f' for {column}' if column else ''
        if isinstance(width, bool) or not isinstance(width, int):
            raise ValueError(f'Column width{where} must be a whole number of pixels.')
        if not (cls.MIN_WIDTH <= width <= cls.MAX_WIDTH):
            raise ValueError(
                f'Column width{where} must be between '
                f'{cls.MIN_WIDTH} and {cls.MAX_WIDTH} pixels.')
        return width

    @staticmethod
    async def _track(user: User, event_type: str, table_key: Optional[str]) -> None:
        try:
            await TelemetryService().track_interaction(
                event_type=event_type,
                discord_id=getattr(user, 'discord_id', None),
                username=getattr(user, 'username', None),
                details={'table_key': table_key} if table_key else None,
            )
        except Exception:
            logger.debug('table preference telemetry failed', exc_info=True)
