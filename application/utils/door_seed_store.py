"""Where Door Randomizer patches live: a directory, normally a docker volume.

Three kinds of file, all named by values Wizzrobe minted or the sidecar
reported, never by request input that hasn't passed :func:`_name`:

* ``seeds/<token>.json``          the seed's byte writes, served to the patcher
* ``seeds/<token>.spoiler.json.gz`` its spoiler, gated by ``DoorSeed.spoiler_access``
* ``base/<version>.bps``          the randomizer build's base patch, shared by every seed it rolled

Kept behind this one class so an S3 bucket can stand in for the directory
later without touching the callers. Every read and write hops to a thread:
the event loop is shared by every connected client.
"""

import asyncio
import gzip
import os
import re
from pathlib import Path
from typing import Optional

_NAME = re.compile(r'^[A-Za-z0-9._-]{1,64}$')


def _name(value: str) -> str:
    if not _NAME.match(value) or value.startswith('.'):
        raise ValueError(f'Not a valid stored name: {value!r}')
    return value


class DoorSeedStore:
    def __init__(self, root: Optional[str] = None) -> None:
        self.root = Path(root or os.getenv('DOOR_SEED_DIR') or '.door-seeds')

    def _path(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

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

    async def put_patch(self, token: str, data: bytes) -> None:
        await asyncio.to_thread(self._write, self._path('seeds', f'{_name(token)}.json'), data)

    async def get_patch(self, token: str) -> Optional[bytes]:
        return await asyncio.to_thread(self._read, self._path('seeds', f'{_name(token)}.json'))

    async def put_spoiler(self, token: str, data: bytes) -> None:
        await asyncio.to_thread(
            self._write, self._path('seeds', f'{_name(token)}.spoiler.json.gz'), gzip.compress(data),
        )

    async def get_spoiler(self, token: str) -> Optional[bytes]:
        raw = await asyncio.to_thread(self._read, self._path('seeds', f'{_name(token)}.spoiler.json.gz'))
        return gzip.decompress(raw) if raw is not None else None

    async def has_base_patch(self, version: str) -> bool:
        return await asyncio.to_thread(self._path('base', f'{_name(version)}.bps').is_file)

    async def put_base_patch(self, version: str, data: bytes) -> None:
        await asyncio.to_thread(self._write, self._path('base', f'{_name(version)}.bps'), data)

    async def get_base_patch(self, version: str) -> Optional[bytes]:
        return await asyncio.to_thread(self._read, self._path('base', f'{_name(version)}.bps'))
