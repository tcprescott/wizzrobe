"""The migration advisory lock, against a real PostgreSQL.

``tests/utils/test_migration_lock.py`` proves the lock is *asked for* — it fakes
asyncpg and asserts the statements. It cannot prove the lock excludes anything,
because on SQLite there is no lock manager to exclude with. That is the only
property that matters here, so it needs the real backend.

Run with:

    WIZZROBE_TEST_DB_URL=postgres://user:pass@localhost:5432/db \\
        poetry run pytest tests/postgres -q
"""

import asyncio
import os

import pytest

from application.utils.migration_lock import migration_lock
from tests.conftest import ON_POSTGRES

pytestmark = pytest.mark.skipif(
    not ON_POSTGRES,
    reason="advisory locks need a real PostgreSQL; set WIZZROBE_TEST_DB_URL",
)


def _dsn() -> str:
    return os.environ['WIZZROBE_TEST_DB_URL']


async def test_second_holder_waits_for_the_first_to_release():
    """The whole point: two boots cannot be inside the upgrade at once.

    Without the lock both blocks interleave and the recorded order comes out
    entered/entered/exited/exited. With it, the second entry cannot appear
    before the first exit.
    """
    order: list[str] = []
    first_inside = asyncio.Event()
    release_first = asyncio.Event()

    async def first():
        async with migration_lock(_dsn()) as locked:
            assert locked is True
            order.append('first-enter')
            first_inside.set()
            await release_first.wait()
            order.append('first-exit')

    async def second():
        await first_inside.wait()
        # Keep this sleep: it's when a missing lock would let the blocked contender through.
        second_task = asyncio.create_task(_enter_second(order))
        await asyncio.sleep(0.5)
        assert 'second-enter' not in order, 'second holder entered while the first held the lock'
        release_first.set()
        await second_task

    await asyncio.gather(first(), second())

    assert order == ['first-enter', 'first-exit', 'second-enter', 'second-exit']


async def _enter_second(order: list[str]) -> None:
    async with migration_lock(_dsn()):
        order.append('second-enter')
        order.append('second-exit')


async def test_lock_is_released_for_the_next_boot():
    """A released lock must be re-acquirable — otherwise the first deploy after
    this change works and every one after it hangs for LOCK_TIMEOUT_SECONDS."""
    for _ in range(3):
        async with migration_lock(_dsn(), acquire_timeout=5) as locked:
            assert locked is True


async def test_a_crashing_migration_still_releases():
    with pytest.raises(RuntimeError):
        async with migration_lock(_dsn(), acquire_timeout=5):
            raise RuntimeError('boom')

    async with migration_lock(_dsn(), acquire_timeout=5) as locked:
        assert locked is True
