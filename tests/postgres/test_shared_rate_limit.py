"""With ``MULTI_INSTANCE``, the API limiter counts in Postgres, so every instance shares a budget.

Run with:

    WIZZROBE_TEST_DB_URL=postgres://user:pass@localhost:5432/db \\
        poetry run pytest tests/postgres -q
"""

import asyncio
import importlib
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from application.services import RateLimitService, shared_counters_enabled
from models import RateLimitWindow
from tests.conftest import ON_POSTGRES

rate_limit = importlib.import_module('api.rate_limit')

pytestmark = pytest.mark.skipif(
    not ON_POSTGRES,
    reason="shared counters need a real PostgreSQL; set WIZZROBE_TEST_DB_URL",
)


def _request(ip='203.0.113.9'):
    return SimpleNamespace(headers={}, client=SimpleNamespace(host=ip))


async def test_concurrent_hits_are_counted_exactly(db):
    service = RateLimitService()
    results = await asyncio.gather(*[
        service.over_limit(['ip:1'], {'ip:1': 30}, now=600.0) for _ in range(40)
    ])
    assert sum(r is None for r in results) == 30
    assert (await RateLimitWindow.get(key='ip:1', window_start=10)).hits == 40


async def test_a_new_window_starts_fresh_and_retry_points_at_it(db):
    service = RateLimitService()
    assert await service.over_limit(['k'], {'k': 1}, now=120.0) is None
    assert await service.over_limit(['k'], {'k': 1}, now=150.0) == 31
    assert await service.over_limit(['k'], {'k': 1}, now=180.0) is None


async def test_api_limiter_uses_the_shared_counters_when_multi_instance(db, monkeypatch):
    monkeypatch.setenv('MULTI_INSTANCE', 'true')
    monkeypatch.setenv('API_RATE_LIMIT_PER_MIN', '2')
    assert shared_counters_enabled()
    for _ in range(2):
        await rate_limit.rate_limit(_request())
    with pytest.raises(HTTPException) as exc:
        await rate_limit.rate_limit(_request())
    assert exc.value.status_code == 429
    assert not rate_limit._hits, 'the in-memory buckets must stay untouched'
    assert await RateLimitWindow.filter(key='ip:203.0.113.9').count() == 1
