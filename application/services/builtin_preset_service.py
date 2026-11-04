"""
Built-in Preset Service - Business Logic Layer

The read-only ``wizzrobe`` space: every file under ``presets/`` (Wizzrobe's own
plus SahasrahBot's global presets) as a library preset anyone can read, roll
and copy. Nobody edits it in the app; the files are the source, and a sync
brings the rows in line with them: new files are added, changed ones updated,
and rows whose file is gone are deleted (copies keep their settings).

The files only change with a deploy, which restarts the process, so they're
read once per process and synced the first time the library is read; a
deploy that adds a preset needs no step.
"""

import asyncio
from typing import Any, Dict, List

from application.repositories import PresetSpaceRepository
from application.services.preset_service import PresetService
from application.services.seedgen_service import SeedGenerationService
from models import PresetSpace

BUILTIN_SLUG = 'wizzrobe'

_lock = asyncio.Lock()
_synced: Dict[str, Any] = {'space_id': None}
_files: Dict[str, Any] = {'entries': None}


def reset_builtin_sync() -> None:
    _synced.update(space_id=None)


class BuiltinPresetService:
    """Keeps the built-in preset space in line with the files in ``presets/``."""

    def __init__(self) -> None:
        self.spaces = PresetSpaceRepository()

    async def ensure_synced(self) -> None:
        """Sync once per process; afterwards one lookup confirms the space is still there."""
        if await self._current():
            return
        async with _lock:
            if await self._current():
                return
            if _files['entries'] is None:
                _files['entries'] = await asyncio.to_thread(self._read)
            space = await self._sync(_files['entries'])
            _synced.update(space_id=space.id)

    async def _current(self) -> bool:
        if _synced['space_id'] is None:
            return False
        space = await self.spaces.builtin()
        return space is not None and space.id == _synced['space_id']

    async def _sync(self, entries: List[Dict[str, Any]]) -> PresetSpace:
        space = await self.spaces.builtin()
        if space is None:
            slug = BUILTIN_SLUG
            if await self.spaces.slug_taken(slug):
                slug = f'{BUILTIN_SLUG}-presets'
            space = await self.spaces.create_builtin(slug)
        existing = {(p.randomizer, p.name): p for p in await self.spaces.presets_in(space.id)}
        wanted = set()
        for entry in entries:
            key = (entry['randomizer'], entry['name'])
            wanted.add(key)
            settings = SeedGenerationService.normalize_preset_settings(entry['randomizer'], entry['settings'])
            row = existing.get(key)
            if row is None:
                await self.spaces.create_preset(
                    space.id, name=entry['name'], randomizer=entry['randomizer'],
                    settings=settings, description=entry['description'],
                )
            elif row.settings != settings or row.description != entry['description']:
                await self.spaces.update_preset(row, settings=settings, description=entry['description'])
        for key, row in existing.items():
            if key not in wanted:
                await self.spaces.delete_preset(row)
        return space

    @staticmethod
    def _read() -> List[Dict[str, Any]]:
        entries = []
        for entry in PresetService._load_builtins():
            try:
                SeedGenerationService.normalize_preset_settings(entry['randomizer'], entry['settings'])
            except ValueError:
                continue
            entries.append(entry)
        return entries
