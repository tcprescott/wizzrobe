"""The startup migration lock: taken on PostgreSQL, skipped elsewhere."""

import asyncio
import sys
import types
from unittest.mock import AsyncMock

import pytest

from application.utils.migration_lock import MIGRATION_LOCK_KEY, migration_lock


class FakeConnection:
    def __init__(self, acquire_delay: float = 0.0):
        self.statements: list[tuple[str, int]] = []
        self.closed = False
        self._acquire_delay = acquire_delay

    async def execute(self, sql: str, key: int) -> None:
        if 'pg_advisory_lock' in sql and self._acquire_delay:
            await asyncio.sleep(self._acquire_delay)
        self.statements.append((sql, key))

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_asyncpg(monkeypatch):
    """Stand in for the asyncpg module the lock imports lazily."""
    module = types.ModuleType('asyncpg')
    holder: dict[str, FakeConnection] = {}

    async def connect(dsn: str) -> FakeConnection:
        conn = holder.get('conn') or FakeConnection()
        holder['conn'] = conn
        holder['dsn'] = dsn  # type: ignore[assignment]
        return conn

    module.connect = connect  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, 'asyncpg', module)
    return holder


class TestPostgres:
    async def test_takes_and_releases_the_lock_around_the_block(self, fake_asyncpg):
        async with migration_lock('postgres://u:p@localhost:5432/db') as locked:
            assert locked is True
            conn = fake_asyncpg['conn']
            assert [s for s, _ in conn.statements] == ['SELECT pg_advisory_lock($1)']

        conn = fake_asyncpg['conn']
        assert [s for s, _ in conn.statements] == [
            'SELECT pg_advisory_lock($1)',
            'SELECT pg_advisory_unlock($1)',
        ]
        assert {k for _, k in conn.statements} == {MIGRATION_LOCK_KEY}
        assert conn.closed is True

    async def test_logs_before_waiting_and_how_long_the_wait_took(self, fake_asyncpg, caplog):
        caplog.set_level('INFO', logger='wizzrobe.migrations')
        async with migration_lock('postgres://u:p@localhost:5432/db'):
            messages = [r.getMessage() for r in caplog.records]
            assert messages[0].startswith('Waiting for the migration lock')
            assert 'after waiting' in messages[1]

    async def test_releases_and_closes_when_the_block_raises(self, fake_asyncpg):
        with pytest.raises(RuntimeError, match='migration blew up'):
            async with migration_lock('postgresql://u:p@localhost:5432/db'):
                raise RuntimeError('migration blew up')

        conn = fake_asyncpg['conn']
        assert 'SELECT pg_advisory_unlock($1)' in [s for s, _ in conn.statements]
        assert conn.closed is True

    async def test_timeout_raises_rather_than_migrating_unguarded(self, monkeypatch):
        module = types.ModuleType('asyncpg')
        conn = FakeConnection(acquire_delay=5)

        async def connect(dsn: str) -> FakeConnection:
            return conn

        module.connect = connect  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, 'asyncpg', module)

        with pytest.raises(TimeoutError, match='migration lock'):
            async with migration_lock('postgres://u:p@localhost:5432/db', acquire_timeout=0.01):
                pytest.fail('the block must not run when the lock was never taken')

        assert conn.closed is True


class TestOtherBackends:
    @pytest.mark.parametrize('dsn', ['sqlite://:memory:', 'sqlite:///tmp/test.db'])
    async def test_sqlite_runs_the_block_unlocked(self, dsn, monkeypatch):
        connect = AsyncMock(side_effect=AssertionError('asyncpg must not be used for SQLite'))
        module = types.ModuleType('asyncpg')
        module.connect = connect  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, 'asyncpg', module)

        ran = False
        async with migration_lock(dsn) as locked:
            ran = True
            assert locked is False
        assert ran is True
        connect.assert_not_called()
