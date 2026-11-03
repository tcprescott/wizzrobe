"""A live signal sent through Postgres NOTIFY reaches another instance's subscribers.

The second instance is played by a bare asyncpg listener on the same channel.

Run with:

    WIZZROBE_TEST_DB_URL=postgres://user:pass@localhost:5432/db \\
        poetry run pytest tests/postgres -q
"""

import asyncio
import json
import os

import asyncpg
import pytest

from application.events import live_relay, match_live
from tests.conftest import ON_POSTGRES

pytestmark = pytest.mark.skipif(
    not ON_POSTGRES,
    reason="LISTEN/NOTIFY needs a real PostgreSQL; set WIZZROBE_TEST_DB_URL",
)


async def test_publish_reaches_another_instance_and_back(monkeypatch):
    dsn = os.environ['WIZZROBE_TEST_DB_URL']
    monkeypatch.setenv('MULTI_INSTANCE', 'true')
    other = await asyncpg.connect(dsn)
    heard: asyncio.Queue = asyncio.Queue()
    await other.add_listener(live_relay.CHANNEL, lambda *a: heard.put_nowait(a[-1]))
    assert await live_relay.start(dsn) is True
    seen: asyncio.Queue = asyncio.Queue()
    token = match_live.subscribe(lambda m, c: seen.put_nowait((m, c)))
    try:
        match_live.publish(11, match_live.CHANGED)
        message = json.loads(await asyncio.wait_for(heard.get(), timeout=5))
        assert message['k'] == 'match' and message['a'] == [11, 'changed']
        assert await seen.get() == (11, 'changed')

        await other.execute(
            'SELECT pg_notify($1, $2)', live_relay.CHANNEL,
            json.dumps({'o': 'other', 'k': 'match', 'a': [12, 'deleted']}),
        )
        assert await asyncio.wait_for(seen.get(), timeout=5) == (12, 'deleted')
        await asyncio.sleep(0.2)
        assert seen.empty(), 'own echo must not be delivered twice'
    finally:
        match_live.unsubscribe(token)
        await live_relay.stop()
        await other.close()
