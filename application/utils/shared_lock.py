"""Mutual exclusion that holds across processes, not just inside one.

Several services serialise work with a per-key ``asyncio.Lock``: one seed roll
per match, one racetime room opener per match, one Challonge token refresh per
community. That only excludes coroutines in the same process. Once the API runs
as several Cloud Run instances, two instances can each take "their" lock and
both roll a seed.

``shared_lock`` keeps the in-process lock (cheap, and the only guard on SQLite)
and, on PostgreSQL, also takes a session advisory lock on a pooled connection it
holds for the length of the block. The advisory lock must be taken and released
on the same connection, so it borrows one with ``acquire_connection`` rather than
issuing two statements through the pool, where the unlock could land elsewhere.
That connection is held while the block runs, so keep locked blocks short
compared with the pool size.

Keys are two 32-bit ints: the lock's ``name`` hashed into the first, the caller's
key (an id, or a string hashed) into the second. Two string keys that hash alike
only serialise more than they needed to.
"""

import asyncio
import logging
import weakref
import zlib
from contextlib import asynccontextmanager
from typing import AsyncIterator, Union

from tortoise import connections

logger = logging.getLogger(__name__)

Key = Union[int, str]

_local: 'weakref.WeakValueDictionary[tuple[str, Key], asyncio.Lock]' = weakref.WeakValueDictionary()


class LockBusy(Exception):
    """Raised by ``shared_lock(..., wait=False)`` when another holder has the lock."""


def _int32(value: int) -> int:
    value &= 0xFFFFFFFF
    return value - 0x1_0000_0000 if value >= 0x8000_0000 else value


def advisory_key(name: str, key: Key) -> tuple[int, int]:
    """The ``(classid, objid)`` pair ``pg_advisory_lock(int, int)`` takes for this lock."""
    second = key if isinstance(key, int) else zlib.crc32(key.encode())
    return _int32(zlib.crc32(name.encode())), _int32(second)


def local_lock(name: str, key: Key) -> asyncio.Lock:
    lock = _local.get((name, key))
    if lock is None:
        lock = asyncio.Lock()
        _local[(name, key)] = lock
    return lock


def _postgres_client():
    try:
        client = connections.get('default')
    except Exception:
        return None
    return client if client.capabilities.dialect == 'postgres' else None


@asynccontextmanager
async def shared_lock(name: str, key: Key, *, wait: bool = True) -> AsyncIterator[None]:
    """Hold the ``(name, key)`` lock for the block, across every process on this database.

    With ``wait=False`` a lock someone else holds raises :class:`LockBusy`
    instead of waiting, for callers that answer "already in progress".
    """
    lock = local_lock(name, key)
    if not wait and lock.locked():
        raise LockBusy(name)
    async with lock:
        client = _postgres_client()
        if client is None:
            yield
            return
        classid, objid = advisory_key(name, key)
        async with client.acquire_connection() as conn:
            if wait:
                await conn.execute('SELECT pg_advisory_lock($1, $2)', classid, objid)
            elif not await conn.fetchval('SELECT pg_try_advisory_lock($1, $2)', classid, objid):
                raise LockBusy(name)
            try:
                yield
            finally:
                try:
                    await conn.execute('SELECT pg_advisory_unlock($1, $2)', classid, objid)
                except Exception:
                    # A dropped connection already released its session locks.
                    logger.warning('Could not release advisory lock %s:%s', name, key, exc_info=True)
