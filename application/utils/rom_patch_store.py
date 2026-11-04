"""Where hosted ROM patches live: ``rom-patches/<token>.<format>`` under a directory.

The directory is ``ROM_PATCH_DIR``, else a ``rom-patches`` folder inside
``DOOR_SEED_DIR``, so the door seed volume holds both without another mount.
Names are only ever Wizzrobe-minted tokens and a known format. Every read and
write hops to a thread: the event loop is shared by every connected client.
"""

import asyncio
import os
import re
from pathlib import Path
from typing import Optional

from application.utils.rom_patch_format import FORMATS

_TOKEN = re.compile(r'^[A-Za-z0-9_-]{8,32}$')


def _default_root() -> Path:
    explicit = os.getenv('ROM_PATCH_DIR')
    if explicit:
        return Path(explicit)
    return Path(os.getenv('DOOR_SEED_DIR') or '.door-seeds') / 'rom-patches'


class RomPatchStore:
    def __init__(self, root: Optional[str] = None) -> None:
        self.root = Path(root) if root else _default_root()

    def _path(self, token: str, fmt: str) -> Path:
        if not _TOKEN.match(token) or fmt not in FORMATS:
            raise ValueError(f'Not a valid stored patch: {token!r}.{fmt!r}')
        return self.root / f'{token}.{fmt}'

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + '.tmp')
        tmp.write_bytes(data)
        tmp.replace(path)

    @staticmethod
    def _read(path: Path) -> Optional[bytes]:
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None

    async def put(self, token: str, fmt: str, data: bytes) -> None:
        await asyncio.to_thread(self._write, self._path(token, fmt), data)

    async def get(self, token: str, fmt: str) -> Optional[bytes]:
        return await asyncio.to_thread(self._read, self._path(token, fmt))

    async def delete(self, token: str, fmt: str) -> None:
        await asyncio.to_thread(self._path(token, fmt).unlink, True)
