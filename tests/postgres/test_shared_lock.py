"""``shared_lock`` excludes holders in other processes, against a real PostgreSQL.

A second process is played by a bare asyncpg connection taking the same
advisory key: the in-process lock can't see it, so only the advisory lock can
make ``shared_lock`` wait or refuse.

Run with:

    WIZZROBE_TEST_DB_URL=postgres://user:pass@localhost:5432/db \\
        poetry run pytest tests/postgres -q
"""

import asyncio
import os

import asyncpg
import pytest

from application.utils.shared_lock import LockBusy, advisory_key, shared_lock
from tests.conftest import ON_POSTGRES

pytestmark = pytest.mark.skipif(
    not ON_POSTGRES,
    reason="advisory locks need a real PostgreSQL; set WIZZROBE_TEST_DB_URL",
)


async def _other_process_holding(name, key):
    conn = await asyncpg.connect(os.environ['WIZZROBE_TEST_DB_URL'])
    await conn.execute('SELECT pg_advisory_lock($1, $2)', *advisory_key(name, key))
    return conn


async def test_try_lock_refuses_while_another_process_holds_it(db):
    other = await _other_process_holding('match.seed_roll', 41)
    try:
        with pytest.raises(LockBusy):
            async with shared_lock('match.seed_roll', 41, wait=False):
                pytest.fail('entered a lock another process holds')
        async with shared_lock('match.seed_roll', 42, wait=False):
            pass
    finally:
        await other.close()

    async with shared_lock('match.seed_roll', 41, wait=False):
        pass


async def test_waiting_lock_enters_only_after_the_other_process_releases(db):
    other = await _other_process_holding('race_room.open', 'match:7')
    entered = asyncio.Event()

    async def take():
        async with shared_lock('race_room.open', 'match:7'):
            entered.set()

    task = asyncio.create_task(take())
    await asyncio.sleep(0.3)
    assert not entered.is_set()
    await other.execute('SELECT pg_advisory_unlock($1, $2)', *advisory_key('race_room.open', 'match:7'))
    await asyncio.wait_for(task, timeout=5)
    assert entered.is_set()
    await other.close()


async def test_lock_is_released_when_the_block_raises(db):
    with pytest.raises(RuntimeError):
        async with shared_lock('check_in.sync', 3):
            raise RuntimeError('boom')

    probe = await asyncpg.connect(os.environ['WIZZROBE_TEST_DB_URL'])
    try:
        assert await probe.fetchval('SELECT pg_try_advisory_lock($1, $2)', *advisory_key('check_in.sync', 3))
    finally:
        await probe.close()
